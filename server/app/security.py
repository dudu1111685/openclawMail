import hashlib
import secrets

from fastapi import Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Agent


def generate_api_key() -> str:
    """Generate a new API key with amb_ prefix."""
    return f"amb_{secrets.token_hex(32)}"


def hash_api_key(raw_key: str) -> str:
    """Hash an API key with SHA-256."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def get_current_agent(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Agent:
    """FastAPI dependency to authenticate and return the current agent."""
    key_hash = hash_api_key(x_api_key)
    result = await db.execute(select(Agent).where(Agent.api_key_hash == key_hash))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return agent
