import logging

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://mailbox:password@localhost:5432/agent_mailbox"
    secret_key: str = "change-me"
    ws_ping_interval: int = 30
    ws_ping_timeout: int = 60
    host: str = "0.0.0.0"
    port: int = 8000
    mailbox_encryption_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

# If no encryption key provided, generate one and warn (dev mode)
if not settings.mailbox_encryption_key:
    from cryptography.fernet import Fernet
    settings.mailbox_encryption_key = Fernet.generate_key().decode()
    logger.warning(
        "MAILBOX_ENCRYPTION_KEY not set — generated ephemeral key for dev mode. "
        "Set MAILBOX_ENCRYPTION_KEY in production to persist encrypted data across restarts."
    )
