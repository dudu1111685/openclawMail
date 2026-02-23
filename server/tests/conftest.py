import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models import Base
from app.security import generate_api_key, hash_api_key

# Use SQLite in-memory for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def registered_agent(client: AsyncClient) -> dict:
    """Register a test agent and return {id, name, api_key}."""
    resp = await client.post(
        "/agents/register",
        json={"name": "test-agent", "owner_contact": "test@example.com"},
    )
    assert resp.status_code == 201
    return resp.json()


@pytest_asyncio.fixture
async def second_agent(client: AsyncClient) -> dict:
    """Register a second test agent."""
    resp = await client.post(
        "/agents/register",
        json={"name": "other-agent"},
    )
    assert resp.status_code == 201
    return resp.json()


@pytest_asyncio.fixture
async def connected_agents(client: AsyncClient, registered_agent: dict, second_agent: dict) -> dict:
    """Two agents with an active connection."""
    # Agent 1 requests connection
    resp = await client.post(
        "/connections/request",
        json={"target_agent_name": "other-agent", "message": "Hello!"},
        headers={"X-API-Key": registered_agent["api_key"]},
    )
    assert resp.status_code == 201
    code = resp.json()["verification_code"]

    # Agent 2 approves
    resp = await client.post(
        "/connections/approve",
        json={"verification_code": code},
        headers={"X-API-Key": second_agent["api_key"]},
    )
    assert resp.status_code == 200

    return {
        "agent1": registered_agent,
        "agent2": second_agent,
    }
