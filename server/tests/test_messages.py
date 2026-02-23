import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_send_message_new_session(client: AsyncClient, connected_agents: dict):
    resp = await client.post(
        "/messages/send",
        json={
            "to": "other-agent",
            "subject": "Test Subject",
            "content": "Hello from test!",
        },
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "message_id" in data
    assert "session_id" in data
    assert data["subject"] == "Test Subject"


@pytest.mark.asyncio
async def test_send_message_existing_session(client: AsyncClient, connected_agents: dict):
    # First message creates session
    resp1 = await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Thread", "content": "First"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    session_id = resp1.json()["session_id"]

    # Second message with same subject goes to same session
    resp2 = await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Thread", "content": "Second"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    assert resp2.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_send_message_case_insensitive_subject(client: AsyncClient, connected_agents: dict):
    resp1 = await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "My Topic", "content": "First"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    session_id = resp1.json()["session_id"]

    resp2 = await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "my topic", "content": "Second"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    assert resp2.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_send_message_no_connection(client: AsyncClient, registered_agent: dict, second_agent: dict):
    resp = await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Test", "content": "Hello"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_send_message_by_session_id(client: AsyncClient, connected_agents: dict):
    # Create session first
    resp1 = await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Direct", "content": "Init"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    session_id = resp1.json()["session_id"]

    # Send via session_id
    resp2 = await client.post(
        "/messages/send",
        json={"to": "other-agent", "content": "Follow up", "session_id": session_id},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    assert resp2.status_code == 201
    assert resp2.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_send_message_missing_subject(client: AsyncClient, connected_agents: dict):
    resp = await client.post(
        "/messages/send",
        json={"to": "other-agent", "content": "No subject"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    assert resp.status_code == 422
