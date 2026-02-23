from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..encryption import encrypt_content
from ..models import Agent, Connection, Message, Session, utcnow
from ..schemas import MessageSendRequest, MessageSendResponse
from ..security import get_current_agent
from ..websocket import manager

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("/send", response_model=MessageSendResponse, status_code=201)
async def send_message(
    request: MessageSendRequest,
    current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> MessageSendResponse:
    # Resolve target agent
    result = await db.execute(select(Agent).where(Agent.name == request.to))
    target_agent = result.scalar_one_or_none()
    if target_agent is None:
        raise HTTPException(status_code=404, detail="Target agent not found")

    # Verify ACTIVE connection exists
    result = await db.execute(
        select(Connection).where(
            Connection.status == "ACTIVE",
            or_(
                and_(
                    Connection.requester_id == current_agent.id,
                    Connection.target_id == target_agent.id,
                ),
                and_(
                    Connection.requester_id == target_agent.id,
                    Connection.target_id == current_agent.id,
                ),
            ),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="No active connection with target agent")

    session = None

    if request.session_id is not None:
        # Use existing session
        result = await db.execute(
            select(Session).where(Session.id == request.session_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        # Verify both agents are participants
        agent_ids = {session.initiator_id, session.participant_id}
        if current_agent.id not in agent_ids or target_agent.id not in agent_ids:
            raise HTTPException(status_code=403, detail="Not a participant of this session")
    else:
        # subject is required when no session_id
        if not request.subject:
            raise HTTPException(status_code=422, detail="Subject is required when session_id is not provided")

        # Find existing session with same subject between these agents
        subject_lower = request.subject.lower()
        result = await db.execute(
            select(Session).where(
                func.lower(Session.subject) == subject_lower,
                or_(
                    and_(
                        Session.initiator_id == current_agent.id,
                        Session.participant_id == target_agent.id,
                    ),
                    and_(
                        Session.initiator_id == target_agent.id,
                        Session.participant_id == current_agent.id,
                    ),
                ),
            )
        )
        session = result.scalar_one_or_none()

        if session is None:
            # Create new session
            session = Session(
                subject=request.subject,
                initiator_id=current_agent.id,
                participant_id=target_agent.id,
            )
            db.add(session)
            await db.flush()

    # Create message (encrypt content at rest)
    now = utcnow()
    message = Message(
        session_id=session.id,
        sender_id=current_agent.id,
        content=encrypt_content(request.content),
        reply_to_session_key=request.reply_to_session_key,
        created_at=now,
    )
    db.add(message)

    # Update last_message_at
    session.last_message_at = now
    await db.flush()

    # Push WebSocket notification to recipient
    await manager.send_to_agent(
        target_agent.id,
        {
            "type": "new_message",
            "session_id": str(session.id),
            "subject": session.subject,
            "from_agent": current_agent.name,
            "content": request.content,
            "message_id": str(message.id),
            "created_at": message.created_at.isoformat(),
            "reply_to_session_key": request.reply_to_session_key,
        },
    )

    return MessageSendResponse(
        message_id=message.id,
        session_id=session.id,
        subject=session.subject,
        created_at=message.created_at,
    )
