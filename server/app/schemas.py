import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# --- Agent Schemas ---

class AgentRegisterRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    owner_contact: str | None = None


class AgentRegisterResponse(BaseModel):
    id: uuid.UUID
    name: str
    api_key: str


class AgentMeResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime


# --- Connection Schemas ---

class ConnectionRequestRequest(BaseModel):
    target_agent_name: str = Field(..., min_length=1)
    message: str | None = Field(None, max_length=500)


class ConnectionRequestResponse(BaseModel):
    connection_id: uuid.UUID
    verification_code: str
    target_agent_name: str
    status: str


class ConnectionApproveRequest(BaseModel):
    verification_code: str = Field(..., min_length=1)


class ConnectionApproveResponse(BaseModel):
    connection_id: uuid.UUID
    status: str
    connected_agent_name: str


class PendingConnectionDetail(BaseModel):
    id: uuid.UUID
    direction: str  # "incoming" or "outgoing"
    other_agent_name: str
    code: str
    created_at: datetime


# --- Message Schemas ---

class MessageSendRequest(BaseModel):
    to: str = Field(..., min_length=1)
    subject: str | None = Field(None, max_length=255)
    content: str = Field(..., min_length=1, max_length=10000)
    session_id: uuid.UUID | None = None


class MessageSendResponse(BaseModel):
    message_id: uuid.UUID
    session_id: uuid.UUID
    subject: str
    created_at: datetime


# --- Inbox Schemas ---

class MessageSummary(BaseModel):
    id: uuid.UUID
    sender_name: str
    content: str
    created_at: datetime
    is_read: bool


class SessionSummary(BaseModel):
    session_id: uuid.UUID
    subject: str
    other_agent_name: str
    unread_count: int
    last_message_at: datetime
    recent_messages: list[MessageSummary]


class PendingConnectionSummary(BaseModel):
    connection_id: uuid.UUID
    from_agent_name: str
    message: str | None
    verification_code: str
    created_at: datetime


class InboxResponse(BaseModel):
    sessions: list[SessionSummary]
    pending_connections: list[PendingConnectionSummary]


# --- Session History Schemas ---

class SessionHistoryResponse(BaseModel):
    session_id: uuid.UUID
    subject: str
    messages: list[MessageSummary]
