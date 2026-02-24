from mcp.server import Server
from mcp.types import TextContent, Tool

from .mailbox_client import MailboxClient

mailbox = MailboxClient()


def create_server() -> Server:
    server = Server("agent-mailbox")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="mailbox_check",
                description="Check your mailbox for new messages and pending connection requests.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="mailbox_connect",
                description="Request to connect with another agent. Returns a verification code to share through a human channel.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target_agent_name": {
                            "type": "string",
                            "description": "Name of the agent to connect with",
                        },
                        "message": {
                            "type": "string",
                            "description": "Optional introductory message",
                        },
                    },
                    "required": ["target_agent_name"],
                },
            ),
            Tool(
                name="mailbox_approve",
                description="Approve a pending connection request using a verification code.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Verification code (e.g., 'XC-992')",
                        },
                    },
                    "required": ["code"],
                },
            ),
            Tool(
                name="mailbox_send",
                description=(
                    "Send a message to a connected agent and wait for their reply.\n\n"
                    "After calling this tool, DO NOT continue — stop and wait. "
                    "The daemon will inject the reply into your session automatically.\n\n"
                    "To continue an existing thread, pass session_id from a previous mailbox_send result.\n"
                    "To start a new thread, omit session_id."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Name of the recipient agent",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Subject of the conversation (used for new threads; ignored if session_id is set)",
                        },
                        "content": {
                            "type": "string",
                            "description": "Message content",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Existing session ID to continue a thread. Omit to start a new thread.",
                        },
                        "reply_to_session_key": {
                            "type": "string",
                            "description": (
                                "Your current OpenClaw session key. When set, the recipient's reply "
                                "will be injected directly into this session (e.g. your Telegram topic) "
                                "instead of a background dm: session. Always set this when messaging "
                                "from an active user conversation so the reply arrives in context."
                            ),
                        },
                        "room": {
                            "type": "string",
                            "description": (
                                "Optional room name (alphanumeric + _ -). Like a WhatsApp group: "
                                "all agents in the same room share conversation context. "
                                "Without a room, each thread is fully isolated."
                            ),
                        },
                    },
                    "required": ["to", "content"],
                },
            ),
            Tool(
                name="mailbox_reply",
                description=(
                    "Reply to an agent in an existing thread. "
                    "Use this after receiving a message — it automatically continues the correct thread.\n\n"
                    "After calling this tool, DO NOT continue — stop and wait. "
                    "The daemon will inject the reply into your session automatically."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Name of the agent to reply to",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "The session ID of the thread you're replying to (from the incoming message header)",
                        },
                        "content": {
                            "type": "string",
                            "description": "Your reply content",
                        },
                        "reply_to_session_key": {
                            "type": "string",
                            "description": (
                                "Your current OpenClaw session key — copy from the incoming message header. "
                                "Ensures the reply from the other agent comes back to the right session."
                            ),
                        },
                    },
                    "required": ["to", "session_id", "content"],
                },
            ),
            Tool(
                name="mailbox_wait",
                description=(
                    "Signal that you are waiting for a reply from an agent. "
                    "Call this after mailbox_send when you want to make it explicit that "
                    "you are pausing and expecting an inbound message.\n\n"
                    "This tool does nothing on the network — the daemon is already listening. "
                    "It exists purely to anchor your turn: after calling it, stop and do not continue "
                    "until the daemon injects the reply into your session."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "The session ID you are waiting on",
                        },
                        "from_agent": {
                            "type": "string",
                            "description": "Name of the agent whose reply you are waiting for",
                        },
                    },
                    "required": ["session_id", "from_agent"],
                },
            ),
            Tool(
                name="mailbox_read",
                description="Read the recent messages in a session.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session ID to read",
                        },
                    },
                    "required": ["session_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "mailbox_check":
                return await _handle_check()
            elif name == "mailbox_connect":
                return await _handle_connect(arguments)
            elif name == "mailbox_approve":
                return await _handle_approve(arguments)
            elif name == "mailbox_send":
                return await _handle_send(arguments)
            elif name == "mailbox_reply":
                return await _handle_reply(arguments)
            elif name == "mailbox_wait":
                return await _handle_wait(arguments)
            elif name == "mailbox_read":
                return await _handle_read(arguments)
            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    return server


async def _handle_check() -> list[TextContent]:
    data = await mailbox.get_inbox()
    lines = []

    sessions = data.get("sessions", [])
    if sessions:
        lines.append(f"=== Inbox ({len(sessions)} sessions) ===\n")
        for s in sessions:
            unread = s.get("unread_count", 0)
            marker = f" [{unread} unread]" if unread else ""
            lines.append(f"Session: {s['subject']}{marker}")
            lines.append(f"  With: {s['other_agent_name']}")
            lines.append(f"  Session ID: {s['session_id']}")
            for msg in s.get("recent_messages", []):
                read_marker = "" if msg.get("is_read") else " *NEW*"
                lines.append(f"  - {msg['sender_name']}: {msg['content']}{read_marker}")
            lines.append("")
    else:
        lines.append("No message sessions.\n")

    pending = data.get("pending_connections", [])
    if pending:
        lines.append(f"=== Pending Connection Requests ({len(pending)}) ===\n")
        for c in pending:
            lines.append(f"From: {c['from_agent_name']}")
            if c.get("message"):
                lines.append(f"  Message: {c['message']}")
            lines.append(f"  Code: {c['verification_code']}")
            lines.append(f"  Use mailbox_approve with this code to accept.\n")

    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_connect(arguments: dict) -> list[TextContent]:
    result = await mailbox.request_connection(
        target_agent_name=arguments["target_agent_name"],
        message=arguments.get("message"),
    )
    code = result["verification_code"]
    text = (
        f"Connection request sent to {result['target_agent_name']}.\n"
        f"Verification code: {code}\n\n"
        f"Share this code with the other agent's owner through a human channel "
        f"(WhatsApp, Telegram, email, etc.). They will use it to approve the connection."
    )
    return [TextContent(type="text", text=text)]


async def _handle_approve(arguments: dict) -> list[TextContent]:
    result = await mailbox.approve_connection(arguments["code"])
    text = (
        f"Connection approved!\n"
        f"You are now connected with {result['connected_agent_name']}.\n"
        f"You can now send messages using mailbox_send."
    )
    return [TextContent(type="text", text=text)]


async def _handle_send(arguments: dict) -> list[TextContent]:
    result = await mailbox.send_message(
        to=arguments["to"],
        subject=arguments.get("subject"),
        content=arguments["content"],
        session_id=arguments.get("session_id"),
        reply_to_session_key=arguments.get("reply_to_session_key"),
        room=arguments.get("room"),
    )
    room_info = f"\nRoom   : #{result['room']}" if result.get("room") else ""
    text = (
        f"✉️ Message sent.\n"
        f"To      : {arguments['to']}\n"
        f"Session : {result['subject']} (ID: {result['session_id']}){room_info}\n"
        f"Msg ID  : {result['message_id']}\n"
        f"\n"
        f"⏳ Waiting for reply — the daemon will inject it into your session automatically.\n"
        f"Do NOT poll mailbox_check. Just stop here and wait."
    )
    return [TextContent(type="text", text=text)]


async def _handle_reply(arguments: dict) -> list[TextContent]:
    """Convenience wrapper — reply to an existing thread."""
    result = await mailbox.send_message(
        to=arguments["to"],
        content=arguments["content"],
        session_id=arguments["session_id"],
        reply_to_session_key=arguments.get("reply_to_session_key"),
    )
    text = (
        f"↩️ Reply sent.\n"
        f"To      : {arguments['to']}\n"
        f"Session : {result['session_id']}\n"
        f"Msg ID  : {result['message_id']}\n"
        f"\n"
        f"⏳ Waiting for reply — the daemon will inject it into your session automatically.\n"
        f"Do NOT poll mailbox_check. Just stop here and wait."
    )
    return [TextContent(type="text", text=text)]


async def _handle_wait(arguments: dict) -> list[TextContent]:
    """Semantic anchor — tells the agent to stop and wait. Does nothing on the network."""
    session_id = arguments["session_id"]
    from_agent = arguments["from_agent"]
    text = (
        f"⏳ Waiting for reply from {from_agent} on session {session_id}.\n"
        f"\n"
        f"The daemon is listening. When the reply arrives it will be injected into your session.\n"
        f"Stop here — do not call any other tools or continue your response."
    )
    return [TextContent(type="text", text=text)]


async def _handle_read(arguments: dict) -> list[TextContent]:
    data = await mailbox.get_session_history(arguments["session_id"])
    lines = [f"Session: {data['subject']}\n"]
    for msg in data.get("messages", []):
        lines.append(f"[{msg['created_at']}] {msg['sender_name']}: {msg['content']}")
    return [TextContent(type="text", text="\n".join(lines))]
