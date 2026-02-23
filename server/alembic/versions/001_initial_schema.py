"""Initial schema - agents, connections, sessions, messages

Revision ID: 001
Revises:
Create Date: 2026-02-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("api_key_hash", sa.String(64), nullable=False),
        sa.Column("api_key_prefix", sa.String(8), nullable=False),
        sa.Column("owner_contact", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agents_name", "agents", ["name"], unique=True)
    op.create_index("ix_agents_api_key_hash", "agents", ["api_key_hash"], unique=True)

    op.create_table(
        "connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("requester_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("target_agent_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("verification_code", sa.String(10), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_connections_verification_code", "connections", ["verification_code"], unique=True)
    op.create_index("ix_connections_requester_id", "connections", ["requester_id"])
    op.create_index("ix_connections_target_agent_name", "connections", ["target_agent_name"])
    op.create_index("ix_connections_status", "connections", ["status"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("initiator_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("participant_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("initiator_id != participant_id", name="ck_sessions_different_agents"),
    )
    op.create_index("ix_sessions_initiator_id", "sessions", ["initiator_id"])
    op.create_index("ix_sessions_participant_id", "sessions", ["participant_id"])
    op.create_index("ix_sessions_last_message_at", "sessions", ["last_message_at"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("sender_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_messages_session_id_created_at", "messages", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("connections")
    op.drop_table("agents")
