import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Agent
from app.security import hash_api_key
from app.websocket_endpoint import ws_endpoint, AUTH_TIMEOUT_SECONDS


def _make_mock_ws(messages: list[str]):
    """Create a mock WebSocket that yields messages from a list."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()

    msg_iter = iter(messages)

    async def receive_text():
        try:
            return next(msg_iter)
        except StopIteration:
            raise Exception("No more messages")

    async def receive_json():
        raw = await receive_text()
        return json.loads(raw)

    ws.receive_text = AsyncMock(side_effect=receive_text)
    ws.receive_json = AsyncMock(side_effect=receive_json)
    return ws


@pytest.mark.asyncio
async def test_ws_auth_invalid_key_closes_4001(client, registered_agent):
    """Invalid API key should close with code 4001."""
    ws = _make_mock_ws([json.dumps({"type": "auth", "api_key": "amb_bad_key"})])

    with patch("app.websocket_endpoint.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        await ws_endpoint(ws)

    ws.accept.assert_called_once()
    ws.close.assert_called_once_with(code=4001, reason="Invalid API key")


@pytest.mark.asyncio
async def test_ws_auth_missing_type_closes_4001(client, registered_agent):
    """Auth message without type=auth should close with 4001."""
    ws = _make_mock_ws([json.dumps({"api_key": "amb_test"})])

    await ws_endpoint(ws)

    ws.accept.assert_called_once()
    ws.close.assert_called_once_with(code=4001, reason="Invalid auth message")


@pytest.mark.asyncio
async def test_ws_auth_missing_api_key_closes_4001(client, registered_agent):
    """Auth message with type=auth but no api_key should close with 4001."""
    ws = _make_mock_ws([json.dumps({"type": "auth"})])

    await ws_endpoint(ws)

    ws.accept.assert_called_once()
    ws.close.assert_called_once_with(code=4001, reason="Invalid auth message")


@pytest.mark.asyncio
async def test_ws_auth_non_json_closes_4001(client, registered_agent):
    """Non-JSON first message should close with 4001."""
    ws = _make_mock_ws(["not valid json"])

    await ws_endpoint(ws)

    ws.accept.assert_called_once()
    ws.close.assert_called_once_with(code=4001, reason="Invalid auth message")


@pytest.mark.asyncio
async def test_ws_auth_timeout_closes_4000(client, registered_agent):
    """No auth message within timeout should close with 4000."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()

    async def slow_receive():
        await asyncio.sleep(AUTH_TIMEOUT_SECONDS + 1)
        return ""

    ws.receive_text = AsyncMock(side_effect=slow_receive)

    await ws_endpoint(ws)

    ws.accept.assert_called_once()
    ws.close.assert_called_once_with(code=4000, reason="Auth timeout")


@pytest.mark.asyncio
async def test_ws_auth_success_then_ping_pong(client, registered_agent):
    """Valid auth followed by ping should get pong response."""
    api_key = registered_agent["api_key"]
    key_hash = hash_api_key(api_key)
    agent = Agent(
        id=uuid.UUID(registered_agent["id"]),
        name="test-agent",
        api_key_hash=key_hash,
        api_key_prefix=api_key[:8],
    )

    from starlette.websockets import WebSocketDisconnect

    messages = [
        json.dumps({"type": "auth", "api_key": api_key}),
        json.dumps({"type": "ping"}),
    ]
    msg_iter = iter(messages)

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()

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

    with patch("app.websocket_endpoint.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        await ws_endpoint(ws)

    # Should have sent pong
    ws.send_json.assert_called_once_with({"type": "pong"})
    # Should NOT have closed with error
    ws.close.assert_not_called()
