from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Agent
from ..schemas import AgentMeResponse, AgentRegisterRequest, AgentRegisterResponse
from ..security import generate_api_key, get_current_agent, hash_api_key

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/register", response_model=AgentRegisterResponse, status_code=201)
async def register_agent(
    request: AgentRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AgentRegisterResponse:
    # Check name uniqueness
    result = await db.execute(select(Agent).where(Agent.name == request.name))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Agent name already taken")

    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    key_prefix = raw_key[:8]

    agent = Agent(
        name=request.name,
        api_key_hash=key_hash,
        api_key_prefix=key_prefix,
        owner_contact=request.owner_contact,
    )
    db.add(agent)
    await db.flush()

    return AgentRegisterResponse(
        id=agent.id,
        name=agent.name,
        api_key=raw_key,
    )


@router.get("/me", response_model=AgentMeResponse)
async def get_agent_me(
    current_agent: Agent = Depends(get_current_agent),
) -> AgentMeResponse:
    return AgentMeResponse(
        id=current_agent.id,
        name=current_agent.name,
        created_at=current_agent.created_at,
    )
