import httpx

from .config import settings

# Force IPv4 to avoid IPv6 connectivity failures on servers that resolve
# dual-stack but lack working IPv6 routes.
_TRANSPORT = httpx.AsyncHTTPTransport(local_address="0.0.0.0")


class MailboxClient:
    """HTTP client for the Mailbox Server API."""

    def __init__(self) -> None:
        # Use URL as-is from config — it already includes the scheme (https://)
        url = settings.mailbox_server_url
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        self.base_url = url.rstrip("/")
        self.api_key = settings.mailbox_api_key

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=_TRANSPORT, timeout=10)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    async def register(self, name: str, owner_contact: str | None = None) -> dict:
        async with self._client() as client:
            payload: dict = {"name": name}
            if owner_contact:
                payload["owner_contact"] = owner_contact
            resp = await client.post(
                f"{self.base_url}/agents/register",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    async def request_connection(self, target_agent_name: str, message: str | None = None) -> dict:
        async with self._client() as client:
            payload: dict = {"target_agent_name": target_agent_name}
            if message:
                payload["message"] = message
            resp = await client.post(
                f"{self.base_url}/connections/request",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def approve_connection(self, verification_code: str) -> dict:
        async with self._client() as client:
            resp = await client.post(
                f"{self.base_url}/connections/approve",
                json={"verification_code": verification_code},
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_inbox(self, unread_only: bool = False) -> dict:
        async with self._client() as client:
            params = {"unread_only": str(unread_only).lower()}
            resp = await client.get(
                f"{self.base_url}/inbox",
                params=params,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_session_history(self, session_id: str, limit: int = 3) -> dict:
        async with self._client() as client:
            resp = await client.get(
                f"{self.base_url}/sessions/{session_id}/history",
                params={"limit": limit},
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def send_message(
        self,
        to: str,
        content: str,
        subject: str | None = None,
        session_id: str | None = None,
        reply_to_session_key: str | None = None,
        room: str | None = None,
    ) -> dict:
        async with self._client() as client:
            payload: dict = {"to": to, "content": content}
            if subject:
                payload["subject"] = subject
            if session_id:
                payload["session_id"] = session_id
            if reply_to_session_key:
                payload["reply_to_session_key"] = reply_to_session_key
            if room:
                payload["room"] = room
            resp = await client.post(
                f"{self.base_url}/messages/send",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_pending_connections(self) -> list:
        async with self._client() as client:
            resp = await client.get(
                f"{self.base_url}/connections/pending",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()
