"""
Tests for ConnectionManager and WebSocket endpoint correctness.

Covers bugs found in production:
1. auth_ok must be sent after successful authentication
2. Race condition: disconnect() of old WS must NOT remove a newer connection
3. Events must be deliverable after reconnect (new WS replaced old one)
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.websocket import ConnectionManager
from app.websocket_endpoint import ws_endpoint, AUTH_TIMEOUT_SECONDS
from app.models import Agent
from app.security import hash_api_key
from starlette.websockets import WebSocketDisconnect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_ws(messages: list[str] | None = None):
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()

    if messages is not None:
        msg_iter = iter(messages)

        async def receive_text():
            try:
                return next(msg_iter)
            except StopIteration:
                raise WebSocketDisconnect()

        async def receive_json():
            raw = await receive_text()
            return json.loads(raw)

        ws.receive_text = AsyncMock(side_effect=receive_text)
        ws.receive_json = AsyncMock(side_effect=receive_json)
    return ws


def _patched_db(agent: Agent):
    """Return a context manager mock that returns *agent* from db.execute()."""
    mock_factory = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory.return_value = mock_session
    return mock_factory


# ---------------------------------------------------------------------------
# 1. auth_ok sent on successful auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_ok_sent_after_valid_auth(registered_agent):
    """Server must send {type: auth_ok} immediately after accepting a valid API key."""
    api_key = registered_agent["api_key"]
    agent = Agent(
        id=uuid.UUID(registered_agent["id"]),
        name="test-agent",
        api_key_hash=hash_api_key(api_key),
        api_key_prefix=api_key[:8],
    )

    ws = _mock_ws([json.dumps({"type": "auth", "api_key": api_key})])

    with patch("app.websocket_endpoint.async_session_factory", _patched_db(agent)):
        await ws_endpoint(ws)

    # First send_json call must be auth_ok
    calls = ws.send_json.call_args_list
    assert len(calls) >= 1, "send_json was never called"
    first_call_arg = calls[0].args[0]
    assert first_call_arg.get("type") == "auth_ok", (
        f"Expected first message to be auth_ok, got: {first_call_arg}"
    )
    assert first_call_arg.get("agent") == agent.name


# ---------------------------------------------------------------------------
# 2. ConnectionManager race condition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disconnect_does_not_remove_newer_connection():
    """
    Regression: when WS-A is replaced by WS-B via connect(), the deferred
    disconnect(agent_id, ws_a) from WS-A's finally block must NOT remove WS-B.
    """
    manager = ConnectionManager()
    agent_id = uuid.uuid4()

    ws_a = AsyncMock()
    ws_b = AsyncMock()

    # Connect WS-A
    await manager.connect(agent_id, ws_a)
    assert manager.active_connections[agent_id] is ws_a

    # Connect WS-B — this replaces WS-A (closes it)
    await manager.connect(agent_id, ws_b)
    assert manager.active_connections[agent_id] is ws_b

    # WS-A's finally block runs its disconnect — must NOT evict WS-B
    await manager.disconnect(agent_id, ws_a)
    assert agent_id in manager.active_connections, (
        "disconnect(ws_a) wrongly removed ws_b from active_connections!"
    )
    assert manager.active_connections[agent_id] is ws_b


@pytest.mark.asyncio
async def test_disconnect_removes_own_connection():
    """disconnect(agent_id, ws) removes the entry when ws IS the active one."""
    manager = ConnectionManager()
    agent_id = uuid.uuid4()
    ws = AsyncMock()

    await manager.connect(agent_id, ws)
    await manager.disconnect(agent_id, ws)

    assert agent_id not in manager.active_connections


@pytest.mark.asyncio
async def test_disconnect_without_ws_always_removes():
    """disconnect(agent_id) with no websocket arg removes unconditionally."""
    manager = ConnectionManager()
    agent_id = uuid.uuid4()
    ws = AsyncMock()

    await manager.connect(agent_id, ws)
    await manager.disconnect(agent_id)  # no ws arg

    assert agent_id not in manager.active_connections


# ---------------------------------------------------------------------------
# 3. Events still delivered after reconnect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_delivered_after_reconnect():
    """
    After WS-A is replaced by WS-B, send_to_agent must send to WS-B, not WS-A.
    This is the end-to-end guard for the race condition fix.
    """
    manager = ConnectionManager()
    agent_id = uuid.uuid4()

    ws_a = AsyncMock()
    ws_b = AsyncMock()

    # Simulate reconnect: WS-A replaced by WS-B
    await manager.connect(agent_id, ws_a)
    await manager.connect(agent_id, ws_b)

    # Old coroutine's disconnect fires — must not remove WS-B
    await manager.disconnect(agent_id, ws_a)

    # Now send an event — should go to WS-B, not WS-A
    event = {"type": "new_message", "content": "hello"}
    delivered = await manager.send_to_agent(agent_id, event)

    assert delivered is True, "Event was not delivered (agent not in active_connections)"
    ws_b.send_json.assert_called_once_with(event)
    ws_a.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_send_to_unconnected_agent_returns_false():
    """send_to_agent returns False when no connection exists."""
    manager = ConnectionManager()
    delivered = await manager.send_to_agent(uuid.uuid4(), {"type": "ping"})
    assert delivered is False
