import asyncio
import json
import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


class OpenClawClient:
    """Client for the OpenClaw Gateway — inject messages into sessions (fire-and-forget)."""

    def __init__(self) -> None:
        self.gateway_url = settings.openclaw_gateway_url.rstrip("/")
        self.token = settings.openclaw_gateway_token

    async def inject_to_session(
        self,
        session_key: str,
        message: str,
        timeout_seconds: int = 10,
    ) -> dict:
        """
        Fire-and-forget: inject a message into an OpenClaw session via /tools/invoke.

        Uses sessions_send with timeoutSeconds=0 — Gateway enqueues the run immediately
        and returns {status: "accepted"} without waiting for the agent turn to complete.
        This never blocks the WebSocket event loop.

        Requirements:
        - Gateway config must include: gateway.tools.allow = ["sessions_send"]
          (sessions_send is in the default HTTP deny list)
        """
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        body = {
            "tool": "sessions_send",
            "args": {
                "sessionKey": session_key,
                "message": message,
                "timeoutSeconds": 0,   # fire-and-forget: enqueue and return immediately
            },
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(
                    f"{self.gateway_url}/tools/invoke",
                    json=body,
                    headers=headers,
                )
                if resp.status_code == 404:
                    logger.error(
                        "sessions_send blocked by gateway policy (404). "
                        "Add 'sessions_send' to gateway.tools.allow in openclaw.json"
                    )
                    return {
                        "status": "error",
                        "error": "sessions_send not allowed. Add it to gateway.tools.allow in openclaw.json",
                    }
                resp.raise_for_status()
                data = resp.json()
                run_id = data.get("details", {}).get("runId", "?")
                logger.info(
                    "Message accepted by gateway for session %s (runId=%s)",
                    session_key, run_id,
                )
                return {"status": "ok", "runId": run_id}
        except httpx.TimeoutException:
            logger.warning("Timeout injecting into session %s", session_key)
            return {"status": "timeout"}
        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP %s injecting into session %s: %s",
                e.response.status_code, session_key, e.response.text,
            )
            return {"status": "error", "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except Exception as e:
            logger.exception("Failed to inject message into session %s", session_key)
            return {"status": "error", "error": str(e)}

    async def list_sessions(self, active_minutes: int = 60) -> list:
        """List active OpenClaw sessions via the openclaw CLI."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "openclaw", "sessions", "--active", str(active_minutes), "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            data = json.loads(stdout)
            return data.get("sessions", [])
        except Exception:
            return []
