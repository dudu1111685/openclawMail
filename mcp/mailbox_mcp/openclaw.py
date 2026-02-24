import asyncio
import logging
import re

import httpx

from .config import settings

# Timeout for delivery HTTP call (fire-and-forget via timeoutSeconds=0)
DELIVERY_HTTP_TIMEOUT = 10


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
    #  Parse session_key → channel delivery params                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_session_key(session_key: str) -> dict | None:
        """
        Parse a session key into channel delivery parameters for the message tool.

        Supported formats:
          agent:main:telegram:group:-1003847194980:topic:3957
          agent:main:telegram:group:-1003847194980
          agent:main:telegram:dm:123456789

        Returns dict with keys: channel, target, thread_id (optional)
        Returns None if the session key cannot be parsed into a direct-send target.
        """
        parts = session_key.split(":")
        # Minimum: agent:main:<channel>
        if len(parts) < 3:
            return None

        channel = parts[2]  # telegram, whatsapp, discord, etc.

        # telegram:group:-1003847194980:topic:3957
        if channel == "telegram":
            # find chat_id (the negative number)
            chat_id = None
            thread_id = None
            for i, p in enumerate(parts):
                if p in ("group", "dm") and i + 1 < len(parts):
                    chat_id = parts[i + 1]
                if p == "topic" and i + 1 < len(parts):
                    thread_id = parts[i + 1]
            if chat_id:
                result = {"channel": "telegram", "target": chat_id}
                if thread_id:
                    result["thread_id"] = thread_id
                return result

        return None

    # ------------------------------------------------------------------ #
    #  Deliver a "reply arrived" notification to the owner's session        #
    # ------------------------------------------------------------------ #

    async def deliver_to_owner_session(
        self,
        session_key: str,
        message: str,
    ) -> None:
        """
        Deliver *message* directly to the owner's channel (Telegram topic etc.)
        using the message tool — bypasses sessions_send entirely.

        Why message tool instead of sessions_send:
        - sessions_send always runs an A2A announce flow (even with timeoutSeconds=0)
          which triggers an agent turn in the session → writes to Telegram as side-effect
        - message tool sends directly to the channel with no agent turn involved
        - Zero announce step, zero side effects, fire-and-forget

        Falls back to sessions_send(timeoutSeconds=0) for non-telegram sessions.
        """
        logger.info("Delivering reply to owner session %s via message tool", session_key)

        parsed = self._parse_session_key(session_key)
        if parsed:
            # Direct channel delivery — no agent turn, no announce step
            args: dict = {
                "action": "send",
                "channel": parsed["channel"],
                "target": parsed["target"],
                "message": message,
            }
            if "thread_id" in parsed:
                args["threadId"] = parsed["thread_id"]

            body = {"tool": "message", "args": args}
            try:
                async with httpx.AsyncClient(timeout=DELIVERY_HTTP_TIMEOUT) as client:
                    resp = await client.post(
                        f"{self.gateway_url}/tools/invoke",
                        json=body,
                        headers=self._headers,
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    logger.info(
                        "deliver_to_owner_session: message tool OK for %s (messageId=%s)",
                        session_key,
                        result.get("result", {}).get("details", {}).get("messageId"),
                    )
                    return
            except httpx.TimeoutException:
                logger.warning(
                    "deliver_to_owner_session: message tool HTTP timeout for %s", session_key
                )
            except httpx.HTTPStatusError as e:
                logger.error(
                    "deliver_to_owner_session: message tool HTTP %s for %s: %s",
                    e.response.status_code, session_key, e.response.text,
                )
            except Exception:
                logger.exception(
                    "deliver_to_owner_session: message tool unexpected error for %s", session_key
                )
            return  # don't fallback — avoid double-delivery attempts

        # Fallback: non-telegram session — use sessions_send fire-and-forget
        logger.info(
            "deliver_to_owner_session: no channel parse for %s — falling back to sessions_send",
            session_key,
        )
        body_fallback = {
            "tool": "sessions_send",
            "args": {
                "sessionKey": session_key,
                "message": message,
                "timeoutSeconds": 0,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=DELIVERY_HTTP_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.gateway_url}/tools/invoke",
                    json=body_fallback,
                    headers=self._headers,
                )
                resp.raise_for_status()
                logger.info(
                    "deliver_to_owner_session: sessions_send fallback OK for %s", session_key
                )
        except Exception:
            logger.exception(
                "deliver_to_owner_session: sessions_send fallback failed for %s", session_key
            )
