"""
Agent Mailbox WebSocket Daemon

Runs as a standalone background process that:
1. Maintains a persistent WebSocket connection to the mailbox server
2. When a new_message arrives, injects it into the appropriate OpenClaw session
3. Auto-reconnects on disconnect

Run: python3 ws_daemon.py
"""
import asyncio
import logging
import os
import sys

# Allow running from any directory
sys.path.insert(0, os.path.dirname(__file__) + "/mcp")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mailbox-ws-daemon")

from mailbox_mcp.config import settings
from mailbox_mcp.mailbox_client import MailboxClient
from mailbox_mcp.openclaw import OpenClawClient
from mailbox_mcp.ws_client import MailboxWSClient


async def main():
    logger.info("Starting Agent Mailbox WebSocket daemon")
    logger.info("Server: %s", settings.mailbox_server_url)
    logger.info("Agent API key: %s...", settings.mailbox_api_key[:16])

    mailbox_client = MailboxClient()
    openclaw_client = OpenClawClient()
    ws_client = MailboxWSClient(mailbox_client, openclaw_client)

    try:
        await ws_client.connect_loop()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        ws_client.stop()


if __name__ == "__main__":
    asyncio.run(main())
