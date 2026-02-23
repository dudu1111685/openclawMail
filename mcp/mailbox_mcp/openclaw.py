import asyncio
import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# Default timeout for agent turns in dm: sessions (agent needs time to think + act)
DM_SESSION_TIMEOUT = int(getattr(settings, "agent_reply_timeout", 300))
# Shorter timeout when delivering a reply back to the owner's active session
DELIVERY_TIMEOUT = 60


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
                details = resp.json().get("result", {}).get("details", {})
                status = details.get("status")
                reply = details.get("reply")
                logger.info(
                    "session=%s status=%s reply_len=%s",
                    session_key, status, len(reply) if reply else 0,
                )
                if status == "ok" and reply:
                    return reply
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
        """
        body = {"tool": "sessions_list", "args": {"limit": 200}}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{self.gateway_url}/tools/invoke",
                    json=body,
                    headers=self._headers,
                )
                resp.raise_for_status()
                # sessions_list returns list of session objects or a details.sessions array
                result = resp.json().get("result", {})
                details = result.get("details", {})
                # Try both response shapes
                sessions = (
                    details.get("sessions")
                    or details  # sometimes the list is the details itself
                    or []
                )
                if isinstance(sessions, list):
                    for s in sessions:
                        if isinstance(s, dict) and s.get("sessionKey") == session_key:
                            return True
                elif isinstance(sessions, dict):
                    # Flat dict keyed by sessionKey
                    if session_key in sessions:
                        return True
        except Exception:
            logger.debug("is_local_session check failed for %s", session_key)
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
        Inject *message* into the owner's session (e.g. Telegram thread).
        Uses a shorter timeout — we just need to deliver, not wait for a response.
        Does NOT send the agent's response anywhere else (breaks the loop).
        """
        logger.info("Delivering reply to owner session %s", session_key)
        reply = await self.inject_and_get_reply(
            session_key=session_key,
            message=message,
            timeout_seconds=DELIVERY_TIMEOUT,
        )
        # reply here is the owner's agent response delivered to Telegram — we discard it.
        # If the owner wants to continue the conversation, they'll call mailbox_send explicitly.
        if reply:
            logger.debug(
                "Owner session %s acknowledged delivery (reply discarded)", session_key
            )
