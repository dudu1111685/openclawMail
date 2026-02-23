from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from .database import engine
from .models import Base
from .routers import agents, connections, inbox, messages
from .websocket_endpoint import ws_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Agent Mailbox Server", version="1.0.0", lifespan=lifespan)

app.include_router(agents.router)
app.include_router(connections.router)
app.include_router(messages.router)
app.include_router(inbox.router)
app.add_api_websocket_route("/ws", ws_endpoint)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
