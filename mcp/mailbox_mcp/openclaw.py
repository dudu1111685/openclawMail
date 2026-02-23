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
                # timeoutSeconds > 0 is required to actually trigger an agent run.
                # timeoutSeconds=0 only enqueues the message but never activates the turn.
                # We use 0 here for the HTTP call itself (fire-and-forget dispatch),
                # and run in a background task so we don't block.
                # The gateway will run the turn asynchronously regardless.
                "timeoutSeconds": 120,
            },
        }

        async def _do_invoke():
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    resp = await client.post(
                        f"{self.gateway_url}/tools/invoke",
                        json=body,
                        headers=headers,
                    )
                    if resp.status_code == 404:
                        logger.error(
                            "sessions_send blocked by gateway policy. "
                            "Add 'sessions_send' to gateway.tools.allow in openclaw.json"
                        )
                        return
                    resp.raise_for_status()
                    run_id = resp.json().get("details", {}).get("runId", "?")
                    logger.info(
                        "Agent turn completed for session %s (runId=%s)", session_key, run_id
                    )
            except Exception:
                logger.exception("Background invoke failed for session %s", session_key)

        # Quick reachability check (3s) before firing background task
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                ping = await client.post(
                    f"{self.gateway_url}/tools/invoke",
                    json={"tool": "sessions_list", "args": {"activeMinutes": 1}},
                    headers=headers,
                )
                if ping.status_code == 404:
                    return {
                        "status": "error",
                        "error": "sessions_send not allowed. Add it to gateway.tools.allow in openclaw.json",
                    }
        except Exception as e:
            logger.error("Gateway unreachable, cannot inject into %s: %s", session_key, e)
            return {"status": "error", "error": f"Gateway unreachable: {e}"}

        # Fire background task — returns immediately, agent turn runs async
        asyncio.create_task(_do_invoke())
        logger.info("Dispatched background agent turn for session %s", session_key)
        return {"status": "ok"}

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
