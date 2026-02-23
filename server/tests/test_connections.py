import re
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.models import Connection


@pytest.mark.asyncio
async def test_request_connection_success(
    client: AsyncClient, registered_agent: dict, second_agent: dict
):
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent", "message": "Hi!"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["target_agent_name"] == "other-agent"
    assert data["status"] == "PENDING"
    # Verify code format: XX-NNN
    assert re.match(r"^[A-Z]{2}-\d{3}$", data["verification_code"])


@pytest.mark.asyncio
async def test_request_connection_self(client: AsyncClient, registered_agent: dict):
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "test-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 422
    assert "yourself" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_request_connection_no_target(client: AsyncClient, registered_agent: dict):
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "nonexistent-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_request_connection_duplicate(
    client: AsyncClient, registered_agent: dict, second_agent: dict
):
    await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_approve_connection_success(
    client: AsyncClient, registered_agent: dict, second_agent: dict
):
    # Request
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    code = resp.json()["verification_code"]

    # Approve
    resp = await client.post(
        "/connections/approve",
        json={"verification_code": code},
        headers={"X-API-Key": second_agent["api_key"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ACTIVE"
    assert data["connected_agent_name"] == "test-agent"


@pytest.mark.asyncio
async def test_approve_connection_wrong_agent(
    client: AsyncClient, registered_agent: dict, second_agent: dict
):
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    code = resp.json()["verification_code"]

    # Wrong agent tries to approve
    resp = await client.post(
        "/connections/approve",
        json={"verification_code": code},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_approve_connection_invalid_code(client: AsyncClient, registered_agent: dict):
    resp = await client.post(
        "/connections/approve",
        json={"verification_code": "ZZ-999"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rate_limit_pending_codes(client: AsyncClient, registered_agent: dict):
    """Agent with 3 pending codes cannot create a 4th."""
    # Register 4 target agents
    targets = []
    for i in range(4):
        resp = await client.post(
            "/agents/register",
            json={"name": f"target-{i}"},
        )
        assert resp.status_code == 201
        targets.append(resp.json())

    # Create 3 pending connection requests (should all succeed)
    for i in range(3):
        resp = await client.post(
            "/connections/request",
            json={"target_agent_name": f"target-{i}"},
            headers={"X-API-Key": registered_agent["api_key"]},
        )
        assert resp.status_code == 201

    # 4th should fail with 429
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "target-3"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 429
    assert "Too many" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_approve_expired_code_fails(client: AsyncClient, registered_agent: dict, second_agent: dict):
    """Approving an expired connection code should fail with 410."""
    # Create a connection request
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 201
    code = resp.json()["verification_code"]

    # Manually expire the code by updating the DB directly
    from tests.conftest import TestSessionFactory
    async with TestSessionFactory() as db:
        from sqlalchemy import update
        await db.execute(
            update(Connection)
            .where(Connection.verification_code == code)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await db.commit()

    # Trying to approve should fail
    resp = await client.post(
        "/connections/approve",
        json={"verification_code": code},
        headers={"X-API-Key": second_agent["api_key"]},
    )
    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_expired_codes_not_in_inbox(client: AsyncClient, registered_agent: dict, second_agent: dict):
    """Expired pending codes should not appear in inbox."""
    # Create a connection request
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 201
    code = resp.json()["verification_code"]

    # Verify it appears in inbox
    resp = await client.get("/inbox", headers={"X-API-Key": second_agent["api_key"]})
    assert len(resp.json()["pending_connections"]) == 1

    # Expire the code
    from tests.conftest import TestSessionFactory
    async with TestSessionFactory() as db:
        from sqlalchemy import update
        await db.execute(
            update(Connection)
            .where(Connection.verification_code == code)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await db.commit()

    # Should no longer appear in inbox
    resp = await client.get("/inbox", headers={"X-API-Key": second_agent["api_key"]})
    assert len(resp.json()["pending_connections"]) == 0


@pytest.mark.asyncio
async def test_race_condition_bidirectional_request(
    client: AsyncClient, registered_agent: dict, second_agent: dict
):
    """Simultaneous A->B and B->A: second request should be rejected."""
    # A requests connection to B
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 201

    # B tries to request connection to A (reverse direction) -- should fail
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "test-agent"},
        headers={"X-API-Key": second_agent["api_key"]},
    )
    assert resp.status_code == 409
    assert "Pending request already exists" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_race_condition_approve_after_reverse_active(
    client: AsyncClient, registered_agent: dict, second_agent: dict
):
    """If A->B is already ACTIVE, approving B->A should be rejected."""
    # A requests connection to B
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 201
    code_a = resp.json()["verification_code"]

    # B approves A->B (now ACTIVE)
    resp = await client.post(
        "/connections/approve",
        json={"verification_code": code_a},
        headers={"X-API-Key": second_agent["api_key"]},
    )
    assert resp.status_code == 200

    # Now try B->A: request should fail because ACTIVE connection already exists
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "test-agent"},
        headers={"X-API-Key": second_agent["api_key"]},
    )
    assert resp.status_code == 409
    assert "Connection already exists" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_connections_pending(
    client: AsyncClient, registered_agent: dict, second_agent: dict
):
    """GET /connections/pending returns pending connections with correct direction."""
    # Agent 1 requests connection to Agent 2
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 201
    code = resp.json()["verification_code"]

    # Agent 1 sees it as outgoing
    resp = await client.get(
        "/connections/pending",
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["direction"] == "outgoing"
    assert data[0]["other_agent_name"] == "other-agent"
    assert data[0]["code"] == code

    # Agent 2 sees it as incoming
    resp = await client.get(
        "/connections/pending",
        headers={"X-API-Key": second_agent["api_key"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["direction"] == "incoming"
    assert data[0]["other_agent_name"] == "test-agent"
    assert data[0]["code"] == code


@pytest.mark.asyncio
async def test_connections_pending_empty(client: AsyncClient, registered_agent: dict):
    """GET /connections/pending returns empty list when no pending connections."""
    resp = await client.get(
        "/connections/pending",
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 200
    assert resp.json() == []
