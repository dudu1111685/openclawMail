import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..encryption import decrypt_content
from ..models import Agent, Connection, Message, Session, utcnow
from ..schemas import (
    InboxResponse,
    MessageSummary,
    PendingConnectionSummary,
    SessionHistoryResponse,
    SessionSummary,
)
from ..security import get_current_agent

router = APIRouter(tags=["inbox"])


@router.get("/inbox", response_model=InboxResponse)
async def get_inbox(
    unread_only: bool = Query(False),
    current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> InboxResponse:
    # Get sessions where agent is a participant
    sessions_query = select(Session).where(
        or_(
            Session.initiator_id == current_agent.id,
            Session.participant_id == current_agent.id,
        )
    ).order_by(Session.last_message_at.desc())

    result = await db.execute(sessions_query)
    sessions = result.scalars().all()

    # Batch-fetch all other agents in one query (Fix N+1)
    other_agent_ids = [
        s.participant_id if s.initiator_id == current_agent.id else s.initiator_id
        for s in sessions
    ]
    if other_agent_ids:
        other_agents_result = await db.execute(select(Agent).where(Agent.id.in_(other_agent_ids)))
        other_agents = {a.id: a for a in other_agents_result.scalars().all()}
    else:
        other_agents = {}

    session_summaries = []
    for s in sessions:
        # Count unread messages (messages not sent by current agent and not read)
        unread_result = await db.execute(
            select(func.count(Message.id)).where(
                Message.session_id == s.id,
                Message.sender_id != current_agent.id,
                Message.is_read == False,  # noqa: E712
            )
        )
        unread_count = unread_result.scalar() or 0

        if unread_only and unread_count == 0:
            continue

        # Get last 3 messages
        msgs_result = await db.execute(
            select(Message)
            .where(Message.session_id == s.id)
            .order_by(Message.created_at.desc())
            .limit(3)
        )
        recent_msgs = msgs_result.scalars().all()

        # Batch-fetch message senders
        sender_ids = {m.sender_id for m in recent_msgs}
        # Reuse already-fetched agents where possible
        missing_sender_ids = sender_ids - set(other_agents.keys()) - {current_agent.id}
        senders = dict(other_agents)
        senders[current_agent.id] = current_agent
        if missing_sender_ids:
            senders_result = await db.execute(select(Agent).where(Agent.id.in_(missing_sender_ids)))
            for a in senders_result.scalars().all():
                senders[a.id] = a

        # Determine the other agent (already batch-fetched)
        other_agent_id = s.participant_id if s.initiator_id == current_agent.id else s.initiator_id
        other_agent = other_agents[other_agent_id]

        message_summaries = []
        for m in reversed(recent_msgs):  # chronological order
            sender = senders[m.sender_id]
            message_summaries.append(
                MessageSummary(
                    id=m.id,
                    sender_name=sender.name,
                    content=decrypt_content(m.content),
                    created_at=m.created_at,
                    is_read=m.is_read,
                )
            )

        session_summaries.append(
            SessionSummary(
                session_id=s.id,
                subject=s.subject,
                other_agent_name=other_agent.name,
                unread_count=unread_count,
                last_message_at=s.last_message_at,
                recent_messages=message_summaries,
            )
        )

    # Get pending connections targeting this agent (exclude expired)
    pending_result = await db.execute(
        select(Connection).where(
            Connection.target_agent_name == current_agent.name,
            Connection.status == "PENDING",
            Connection.expires_at > utcnow(),
        ).order_by(Connection.created_at.desc())
    )
    pending_connections = pending_result.scalars().all()

    pending_summaries = []
    for c in pending_connections:
        requester_result = await db.execute(select(Agent).where(Agent.id == c.requester_id))
        requester = requester_result.scalar_one()
        pending_summaries.append(
            PendingConnectionSummary(
                connection_id=c.id,
                from_agent_name=requester.name,
                message=c.message,
                verification_code=c.verification_code,
                created_at=c.created_at,
            )
        )

    return InboxResponse(
        sessions=session_summaries,
        pending_connections=pending_summaries,
    )


@router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history(
    session_id: uuid.UUID,
    limit: int = Query(3, ge=1, le=50),
    current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> SessionHistoryResponse:
    # Verify session exists
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify agent is a participant
    if current_agent.id not in (session.initiator_id, session.participant_id):
        raise HTTPException(status_code=403, detail="Not a participant of this session")

    # Get last N messages
    msgs_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    messages = msgs_result.scalars().all()

    # Mark fetched messages as read (where sender != current agent)
    msg_ids_to_mark = [m.id for m in messages if m.sender_id != current_agent.id and not m.is_read]
    if msg_ids_to_mark:
        await db.execute(
            update(Message)
            .where(Message.id.in_(msg_ids_to_mark))
            .values(is_read=True)
        )

    message_summaries = []
    for m in reversed(messages):  # chronological order
        sender_result = await db.execute(select(Agent).where(Agent.id == m.sender_id))
        sender = sender_result.scalar_one()
        message_summaries.append(
            MessageSummary(
                id=m.id,
                sender_name=sender.name,
                content=decrypt_content(m.content),
                created_at=m.created_at,
                is_read=True if m.sender_id != current_agent.id else m.is_read,
            )
        )

    return SessionHistoryResponse(
        session_id=session.id,
        subject=session.subject,
        messages=message_summaries,
    )
