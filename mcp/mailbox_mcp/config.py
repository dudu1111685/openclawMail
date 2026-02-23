from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mailbox_server_url: str = "a2amaio.runflow.lol:8000"
    mailbox_api_key: str = ""
    openclaw_gateway_url: str = "http://127.0.0.1:18789"
    openclaw_gateway_token: str = ""
    trusted_agents: list[str] = Field(default_factory=list)
    use_tls: bool = False

    @field_validator("trusted_agents", mode="before")
    @classmethod
    def parse_trusted_agents(cls, v):
        if isinstance(v, str):
            return [a.strip() for a in v.split(",") if a.strip()]
        return v or []

    model_config = {"env_prefix": "", "env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
