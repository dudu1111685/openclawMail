import secrets
import string

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Agent, Connection, utcnow
from ..schemas import (
    ConnectionApproveRequest,
    ConnectionApproveResponse,
    ConnectionRequestRequest,
    ConnectionRequestResponse,
    PendingConnectionDetail,
)
from ..security import get_current_agent
from ..websocket import manager

MAX_PENDING_CODES = 3

router = APIRouter(prefix="/connections", tags=["connections"])


def generate_verification_code() -> str:
    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(2))
    digits = "".join(secrets.choice(string.digits) for _ in range(3))
    return f"{letters}-{digits}"


@router.get("/pending", response_model=list[PendingConnectionDetail])
async def list_pending(
    current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> list[PendingConnectionDetail]:
    result = await db.execute(
        select(Connection).where(
            and_(
                Connection.status == "PENDING",
                Connection.expires_at > utcnow(),
                or_(
                    Connection.requester_id == current_agent.id,
                    Connection.target_agent_name == current_agent.name,
                ),
            )
        ).order_by(Connection.created_at.desc())
    )
    connections = result.scalars().all()

    summaries = []
    for c in connections:
        if c.requester_id == current_agent.id:
            direction = "outgoing"
            other_agent_name = c.target_agent_name
        else:
            requester_result = await db.execute(select(Agent).where(Agent.id == c.requester_id))
            requester = requester_result.scalar_one()
            direction = "incoming"
            other_agent_name = requester.name
        summaries.append(
            PendingConnectionDetail(
                id=c.id,
                direction=direction,
                other_agent_name=other_agent_name,
                code=c.verification_code,
                created_at=c.created_at,
            )
        )
    return summaries


@router.post("/request", response_model=ConnectionRequestResponse, status_code=201)
async def request_connection(
    request: ConnectionRequestRequest,
    current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> ConnectionRequestResponse:
    # Cannot connect to self
    if request.target_agent_name == current_agent.name:
        raise HTTPException(status_code=422, detail="Cannot connect to yourself")

    # Verify target exists
    result = await db.execute(select(Agent).where(Agent.name == request.target_agent_name))
    target_agent = result.scalar_one_or_none()
    if target_agent is None:
        raise HTTPException(status_code=404, detail="Target agent not found")

    # Check no ACTIVE connection exists
    result = await db.execute(
        select(Connection).where(
            Connection.status == "ACTIVE",
            or_(
                and_(
                    Connection.requester_id == current_agent.id,
                    Connection.target_agent_name == request.target_agent_name,
                ),
                and_(
                    Connection.requester_id == target_agent.id,
                    Connection.target_agent_name == current_agent.name,
                ),
            ),
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Connection already exists")

    # Rate limit: refuse if agent already has >= MAX_PENDING_CODES pending codes
    pending_count_result = await db.execute(
        select(func.count(Connection.id)).where(
            Connection.requester_id == current_agent.id,
            Connection.status == "PENDING",
            Connection.expires_at > utcnow(),
        )
    )
    pending_count = pending_count_result.scalar() or 0
    if pending_count >= MAX_PENDING_CODES:
        raise HTTPException(status_code=429, detail="Too many pending connection requests")

    # Check no PENDING request in either direction between these two agents
    result = await db.execute(
        select(Connection).where(
            Connection.status == "PENDING",
            or_(
                and_(
                    Connection.requester_id == current_agent.id,
                    Connection.target_agent_name == request.target_agent_name,
                ),
                and_(
                    Connection.requester_id == target_agent.id,
                    Connection.target_agent_name == current_agent.name,
                ),
            ),
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Pending request already exists")

    # Generate unique verification code
    for _ in range(10):
        code = generate_verification_code()
        result = await db.execute(
            select(Connection).where(Connection.verification_code == code)
        )
        if result.scalar_one_or_none() is None:
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate unique code")

    connection = Connection(
        requester_id=current_agent.id,
        target_agent_name=request.target_agent_name,
        verification_code=code,
        message=request.message,
        status="PENDING",
    )
    db.add(connection)
    await db.flush()

    # Push WebSocket notification to target
    await manager.send_to_agent(
        target_agent.id,
        {
            "type": "connection_request",
            "connection_id": str(connection.id),
            "from_agent": current_agent.name,
            "message": request.message,
            "verification_code": code,
        },
    )

    return ConnectionRequestResponse(
        connection_id=connection.id,
        verification_code=code,
        target_agent_name=request.target_agent_name,
        status="PENDING",
    )


@router.post("/approve", response_model=ConnectionApproveResponse)
async def approve_connection(
    request: ConnectionApproveRequest,
    current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> ConnectionApproveResponse:
    # Find connection by code
    result = await db.execute(
        select(Connection).where(
            Connection.verification_code == request.verification_code,
            Connection.status == "PENDING",
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Code not found or already used")

    # Reject expired codes
    now = utcnow()
    expires = connection.expires_at
    # Normalize timezone awareness for comparison (SQLite stores naive)
    if expires.tzinfo is None:
        from datetime import timezone
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        raise HTTPException(status_code=410, detail="Connection code has expired")

    # Verify approver is the target
    if connection.target_agent_name != current_agent.name:
        raise HTTPException(status_code=403, detail="Not the target agent")

    # Race condition guard: check no ACTIVE connection exists in reverse direction
    result = await db.execute(
        select(Connection).where(
            Connection.status == "ACTIVE",
            or_(
                and_(
                    Connection.requester_id == connection.requester_id,
                    Connection.target_id == current_agent.id,
                ),
                and_(
                    Connection.requester_id == current_agent.id,
                    Connection.target_id == connection.requester_id,
                ),
            ),
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Connection already exists in reverse direction")

    connection.target_id = current_agent.id
    connection.status = "ACTIVE"
    await db.flush()

    # Get requester name for response
    result = await db.execute(select(Agent).where(Agent.id == connection.requester_id))
    requester = result.scalar_one()

    # Push WebSocket notification to requester
    await manager.send_to_agent(
        connection.requester_id,
        {
            "type": "connection_approved",
            "connection_id": str(connection.id),
            "connected_agent": current_agent.name,
        },
    )

    return ConnectionApproveResponse(
        connection_id=connection.id,
        status="ACTIVE",
        connected_agent_name=requester.name,
    )
