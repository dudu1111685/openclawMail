import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_agent_success(client: AsyncClient):
    resp = await client.post(
        "/agents/register",
        json={"name": "alice-agent", "owner_contact": "alice@example.com"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["name"] == "alice-agent"
    assert data["api_key"].startswith("amb_")
    assert len(data["api_key"]) == 68  # amb_ + 64 hex chars


@pytest.mark.asyncio
async def test_register_agent_duplicate_name(client: AsyncClient):
    await client.post("/agents/register", json={"name": "dup-agent"})
    resp = await client.post("/agents/register", json={"name": "dup-agent"})
    assert resp.status_code == 409
    assert "already taken" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_agent_invalid_name(client: AsyncClient):
    resp = await client.post("/agents/register", json={"name": "a b c"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_agent_name_too_short(client: AsyncClient):
    resp = await client.post("/agents/register", json={"name": "ab"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_agent_auth_works(client: AsyncClient, registered_agent: dict):
    """Verify the returned API key works for authenticated requests."""
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_api_key(client: AsyncClient):
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": "amb_invalid_key_here"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_agents_me(client: AsyncClient, registered_agent: dict):
    """GET /agents/me returns current agent info."""
    resp = await client.get(
        "/agents/me",
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == registered_agent["id"]
    assert data["name"] == "test-agent"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_agents_me_unauthenticated(client: AsyncClient):
    """GET /agents/me without auth returns 422 (missing header)."""
    resp = await client.get("/agents/me")
    assert resp.status_code == 422
