import asyncio
import logging
import re

import httpx

from .config import settings

# Timeout for cron wake HTTP call (fire-and-forget, should be near-instant)
CRON_WAKE_TIMEOUT = 5


def _extract_reply(raw: str) -> str:
    """
    Extract the text between the first pair of %% markers.
    If no markers found, return the raw text as-is (backward compatible).

    Expected format from agent:
        %%
        <reply text>
        %%
    """
    match = re.search(r"%%\s*\n(.*?)\n\s*%%", raw, re.DOTALL)
    if match:
        extracted = match.group(1).strip()
        logger.debug("Extracted reply from %%-markers (%d chars)", len(extracted))
        return extracted
    # No markers — fallback to full reply (agent didn't use the format)
    logger.debug("No %%-markers found — using raw reply (%d chars)", len(raw))
    return raw

logger = logging.getLogger(__name__)

# Default timeout for agent turns in dm: sessions (agent needs time to think + act)
DM_SESSION_TIMEOUT = int(getattr(settings, "agent_reply_timeout", 300))


class OpenClawClient:
    """Gateway client — inject messages and get agent replies via /tools/invoke."""

    def __init__(self) -> None:
        self.gateway_url = settings.openclaw_gateway_url.rstrip("/")
        self.token = settings.openclaw_gateway_token
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ #
    #  Core: inject a message and wait for the agent's text reply          #
    # ------------------------------------------------------------------ #

    async def inject_and_get_reply(
        self,
        session_key: str,
        message: str,
        timeout_seconds: int = DM_SESSION_TIMEOUT,
    ) -> str | None:
        """
        Inject *message* into *session_key* and return the agent's text reply.

        Returns the reply string on success, or None on timeout / error.
        The HTTP call blocks for up to timeout_seconds + 15s.
        Run this inside asyncio.create_task() to avoid blocking the WS loop.
        """
        body = {
            "tool": "sessions_send",
            "args": {
                "sessionKey": session_key,
                "message": message,
                "timeoutSeconds": timeout_seconds,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds + 15) as client:
                resp = await client.post(
                    f"{self.gateway_url}/tools/invoke",
                    json=body,
                    headers=self._headers,
                )
                if resp.status_code == 404:
                    logger.error(
                        "sessions_send blocked by gateway (404). "
                        "Add 'sessions_send' to gateway.tools.allow in openclaw.json"
                    )
                    return None
                resp.raise_for_status()
                result = resp.json().get("result", {})
                details = result.get("details", {})

                # If details is sparse, also try parsing content[0].text
                if not details.get("status"):
                    import json as _json
                    content = result.get("content", [])
                    if content and content[0].get("type") == "text":
                        try:
                            details = _json.loads(content[0]["text"])
                        except Exception:
                            pass

                status = details.get("status")
                reply = details.get("reply")
                logger.info(
                    "session=%s status=%s reply_len=%s",
                    session_key, status, len(reply) if reply else 0,
                )
                if status == "ok" and reply:
                    return _extract_reply(reply)
                if status == "timeout":
                    logger.warning(
                        "Agent did not reply within %ds for session %s",
                        timeout_seconds, session_key,
                    )
        except httpx.TimeoutException:
            logger.warning("HTTP timeout waiting for reply from session %s", session_key)
        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP %s from gateway for session %s: %s",
                e.response.status_code, session_key, e.response.text,
            )
        except Exception:
            logger.exception("inject_and_get_reply failed for session %s", session_key)
        return None

    # ------------------------------------------------------------------ #
    #  Check whether a session key belongs to this agent's gateway         #
    # ------------------------------------------------------------------ #

    async def is_local_session(self, session_key: str) -> bool:
        """
        Return True if *session_key* is an active (or recently active) session
        on this agent's gateway.  Used to decide whether reply_to_session_key
        is ours (→ just deliver) or theirs (→ inject to dm: and send reply back).

        Gateway response shape:
          result.content[0].text = JSON string with {"count": N, "sessions": [...]}
          Each session has field "key" (not "sessionKey").
        """
        body = {"tool": "sessions_list", "args": {"limit": 200}}
        import json as _json
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self.gateway_url}/tools/invoke",
                    json=body,
                    headers=self._headers,
                )
                resp.raise_for_status()
                result = resp.json().get("result", {})

                # Primary path: content[0].text is a JSON string
                content = result.get("content", [])
                if content and content[0].get("type") == "text":
                    inner = _json.loads(content[0]["text"])
                    sessions = inner.get("sessions", [])
                    for s in sessions:
                        if s.get("key") == session_key:
                            return True

                # Fallback: details.sessions
                details = result.get("details", {})
                for s in details.get("sessions", []):
                    if s.get("key") == session_key or s.get("sessionKey") == session_key:
                        return True

        except Exception:
            logger.debug("is_local_session check failed for %s", session_key, exc_info=True)
        return False

    # ------------------------------------------------------------------ #
    #  cron wake — fire-and-forget systemEvent to the main session         #
    # ------------------------------------------------------------------ #

    async def cron_wake(self, text: str) -> bool:
        """
        Send a systemEvent to the main session via Gateway cron wake.

        This is fire-and-forget: the gateway enqueues a systemEvent and triggers
        an immediate heartbeat.  No announce step, no timeout, no side effects.

        Returns True on success, False on any error.
        """
        body = {
            "tool": "cron",
            "args": {
                "action": "wake",
                "text": text,
                "mode": "now",
            },
        }
        try:
            async with httpx.AsyncClient(timeout=CRON_WAKE_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.gateway_url}/tools/invoke",
                    json=body,
                    headers=self._headers,
                )
                resp.raise_for_status()
                result = resp.json()
                ok = result.get("ok", False)
                logger.info("cron_wake: ok=%s", ok)
                return bool(ok)
        except httpx.TimeoutException:
            logger.warning("cron_wake: HTTP timeout after %ds", CRON_WAKE_TIMEOUT)
        except httpx.HTTPStatusError as e:
            logger.error(
                "cron_wake: HTTP %s: %s",
                e.response.status_code, e.response.text,
            )
        except Exception:
            logger.exception("cron_wake: unexpected error")
        return False

    # ------------------------------------------------------------------ #
    #  Deliver a "reply arrived" notification to the owner's session        #
    # ------------------------------------------------------------------ #

    async def deliver_to_owner_session(
        self,
        session_key: str,
        message: str,
    ) -> None:
        """
        Deliver an incoming agent message to the owner's active session.

        Uses cron wake (systemEvent) instead of sessions_send, so:
        - No announce step → no side-effects writing to Telegram
        - Fire-and-forget → no 60s wait
        - Clean systemEvent → Ron wakes up as if a heartbeat fired

        The session_key is kept for logging only — cron wake always targets
        the main session, which is exactly where the owner reads messages.
        """
        logger.info(
            "Delivering reply to owner session %s via cron wake", session_key
        )
        ok = await self.cron_wake(message)
        if ok:
            logger.info(
                "cron_wake delivered for session %s — agent will handle it on next turn",
                session_key,
            )
        else:
            logger.warning(
                "cron_wake failed for session %s — message may be lost",
                session_key,
            )
