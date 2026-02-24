import uuid

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[uuid.UUID, WebSocket] = {}

    async def connect(self, agent_id: uuid.UUID, websocket: WebSocket) -> None:
        existing = self.active_connections.get(agent_id)
        if existing is not None:
            # Remove first, then close — prevents the old coroutine's finally-disconnect
            # from seeing itself as the active connection and removing the NEW socket.
            del self.active_connections[agent_id]
            try:
                await existing.close()
            except Exception:
                pass
        self.active_connections[agent_id] = websocket

    async def disconnect(self, agent_id: uuid.UUID, websocket: WebSocket | None = None) -> None:
        # Only remove if this specific websocket is still the active one.
        # Guards against the race: old coroutine's finally-disconnect removing
        # a newer connection that replaced it via connect().
        if websocket is None:
            self.active_connections.pop(agent_id, None)
        elif self.active_connections.get(agent_id) is websocket:
            self.active_connections.pop(agent_id, None)

    async def send_to_agent(self, agent_id: uuid.UUID, data: dict) -> bool:
        ws = self.active_connections.get(agent_id)
        if ws is None:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception:
            await self.disconnect(agent_id)
            return False


manager = ConnectionManager()
