import asyncio
import logging

from .mailbox_client import MailboxClient
from .openclaw import OpenClawClient
from .server import create_server
from .ws_client import MailboxWSClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run() -> None:
    server = create_server()

    # Start WebSocket client in background
    mailbox_client = MailboxClient()
    openclaw_client = OpenClawClient()
    ws_client = MailboxWSClient(mailbox_client, openclaw_client)

    ws_task = asyncio.create_task(ws_client.connect_loop())

    try:
        await server.run_stdio()
    finally:
        ws_client.stop()
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
