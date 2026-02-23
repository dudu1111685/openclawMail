import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_inbox_empty(client: AsyncClient, registered_agent: dict):
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sessions"] == []
    assert data["pending_connections"] == []


@pytest.mark.asyncio
async def test_inbox_with_messages(client: AsyncClient, connected_agents: dict):
    # Send a message
    await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Inbox Test", "content": "Hello!"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )

    # Check agent2's inbox
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 1
    session = data["sessions"][0]
    assert session["subject"] == "Inbox Test"
    assert session["other_agent_name"] == "test-agent"
    assert session["unread_count"] == 1
    assert len(session["recent_messages"]) == 1
    assert session["recent_messages"][0]["content"] == "Hello!"


@pytest.mark.asyncio
async def test_inbox_unread_only(client: AsyncClient, connected_agents: dict):
    # Send message
    await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Read Test", "content": "Message"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    resp = await client.get(
        "/inbox?unread_only=true",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    assert len(resp.json()["sessions"]) == 1

    # Read the session
    session_id = resp.json()["sessions"][0]["session_id"]
    await client.get(
        f"/sessions/{session_id}/history",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )

    # Now unread_only should be empty
    resp = await client.get(
        "/inbox?unread_only=true",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    assert len(resp.json()["sessions"]) == 0


@pytest.mark.asyncio
async def test_inbox_pending_connections(
    client: AsyncClient, registered_agent: dict, second_agent: dict
):
    # Request connection
    await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent", "message": "Want to chat?"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )

    # Check agent2's inbox
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": second_agent["api_key"]},
    )
    data = resp.json()
    assert len(data["pending_connections"]) == 1
    pending = data["pending_connections"][0]
    assert pending["from_agent_name"] == "test-agent"
    assert pending["message"] == "Want to chat?"


@pytest.mark.asyncio
async def test_session_history(client: AsyncClient, connected_agents: dict):
    # Send 5 messages
    for i in range(5):
        await client.post(
            "/messages/send",
            json={"to": "other-agent", "subject": "History", "content": f"Message {i}"},
            headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
        )

    # Get inbox to find session_id
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    session_id = resp.json()["sessions"][0]["session_id"]

    # Get history with default limit (3)
    resp = await client.get(
        f"/sessions/{session_id}/history",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 3
    assert data["subject"] == "History"


@pytest.mark.asyncio
async def test_session_history_marks_read(client: AsyncClient, connected_agents: dict):
    # Send message
    await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Mark Read", "content": "Unread message"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )

    # Get inbox to find session
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    session_id = resp.json()["sessions"][0]["session_id"]
    assert resp.json()["sessions"][0]["unread_count"] == 1

    # Read history (marks as read)
    await client.get(
        f"/sessions/{session_id}/history",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )

    # Check inbox again - should have 0 unread
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    assert resp.json()["sessions"][0]["unread_count"] == 0


@pytest.mark.asyncio
async def test_session_history_not_participant(
    client: AsyncClient, connected_agents: dict
):
    # Send message between agents 1 and 2
    resp = await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Private", "content": "Secret"},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    session_id = resp.json()["session_id"]

    # Register a third agent
    resp = await client.post(
        "/agents/register",
        json={"name": "third-agent"},
    )
    third_key = resp.json()["api_key"]

    # Third agent tries to access session
    resp = await client.get(
        f"/sessions/{session_id}/history",
        headers={"X-API-Key": third_key},
    )
    assert resp.status_code == 403
