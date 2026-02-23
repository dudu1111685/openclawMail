import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .database import async_session_factory
from .models import Agent
from .security import hash_api_key
from .websocket import manager

AUTH_TIMEOUT_SECONDS = 5


async def ws_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time push notifications.

    Protocol: client must send {"type": "auth", "api_key": "..."} as the
    first message after connecting.  The server closes the socket with 4000
    (timeout) if no auth arrives within 5 s, or 4001 (invalid key) on bad
    credentials.
    """
    await websocket.accept()

    # Wait for first-message auth
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=AUTH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await websocket.close(code=4000, reason="Auth timeout")
        return
    except WebSocketDisconnect:
        return

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        await websocket.close(code=4001, reason="Invalid auth message")
        return

    if data.get("type") != "auth" or not data.get("api_key"):
        await websocket.close(code=4001, reason="Invalid auth message")
        return

    api_key: str = data["api_key"]
    key_hash = hash_api_key(api_key)

    async with async_session_factory() as db:
        result = await db.execute(select(Agent).where(Agent.api_key_hash == key_hash))
        agent = result.scalar_one_or_none()

    if agent is None:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    await manager.connect(agent.id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(agent.id)
