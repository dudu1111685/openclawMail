import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.encryption import decrypt_content, encrypt_content
from app.models import Message


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip():
    """Encrypt then decrypt should return original plaintext."""
    original = "Hello, this is a secret message!"
    encrypted = encrypt_content(original)
    assert encrypted != original
    decrypted = decrypt_content(encrypted)
    assert decrypted == original


@pytest.mark.asyncio
async def test_encrypted_content_not_plaintext():
    """Encrypted content should not contain the original plaintext."""
    original = "Super secret agent message content"
    encrypted = encrypt_content(original)
    assert original not in encrypted


@pytest.mark.asyncio
async def test_decrypt_content_legacy_plaintext():
    """decrypt_content() should return legacy plaintext as-is when it's not a valid Fernet token."""
    legacy = "Hello, this is a plain old message from before encryption"
    assert decrypt_content(legacy) == legacy


@pytest.mark.asyncio
async def test_message_stored_encrypted_in_db(client: AsyncClient, connected_agents: dict):
    """Raw DB content should be encrypted, not plaintext."""
    plaintext = "This message should be encrypted at rest"

    resp = await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Encryption Test", "content": plaintext},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )
    assert resp.status_code == 201
    message_id = uuid.UUID(resp.json()["message_id"])

    # Read raw value from DB
    from tests.conftest import TestSessionFactory
    async with TestSessionFactory() as db:
        result = await db.execute(select(Message).where(Message.id == message_id))
        msg = result.scalar_one()
        raw_content = msg.content

    # Raw DB content should NOT be the plaintext
    assert raw_content != plaintext
    # But decrypting it should give back the plaintext
    assert decrypt_content(raw_content) == plaintext


@pytest.mark.asyncio
async def test_message_retrieved_decrypted(client: AsyncClient, connected_agents: dict):
    """Messages returned via API should be decrypted transparently."""
    plaintext = "Decrypted message for API consumers"

    await client.post(
        "/messages/send",
        json={"to": "other-agent", "subject": "Decrypt Test", "content": plaintext},
        headers={"X-API-Key": connected_agents["agent1"]["api_key"]},
    )

    # Check via inbox
    resp = await client.get(
        "/inbox",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["recent_messages"][0]["content"] == plaintext

    # Check via session history
    session_id = sessions[0]["session_id"]
    resp = await client.get(
        f"/sessions/{session_id}/history",
        headers={"X-API-Key": connected_agents["agent2"]["api_key"]},
    )
    assert resp.status_code == 200
    assert resp.json()["messages"][0]["content"] == plaintext
