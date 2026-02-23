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
                description="Send a message to a connected agent.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Name of the recipient agent",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Subject of the conversation",
                        },
                        "content": {
                            "type": "string",
                            "description": "Message content",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Optional existing session ID to continue a thread",
                        },
                        "reply_to_session_key": {
                            "type": "string",
                            "description": "Optional OpenClaw session key on YOUR side. "
                                           "When set, the recipient's replies will be injected "
                                           "into that specific session (e.g. your current Telegram topic) "
                                           "instead of their default dm:mailbox-{you} session. "
                                           "Use this when you want the conversation to stay in context.",
                        },
                        "room": {
                            "type": "string",
                            "description": "Optional room name (alphanumeric + _ -). "
                                           "Like a WhatsApp group: all agents in the same room share "
                                           "the same conversation context (dm:mailbox-room-{room}). "
                                           "Use a room when you want persistent shared context across "
                                           "multiple conversations with the same agent or group. "
                                           "Without a room, each message thread is fully isolated.",
                        },
                    },
                    "required": ["to", "content"],
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
    room_info = f"\nRoom: #{result['room']}" if result.get("room") else ""
    text = (
        f"Message sent!\n"
        f"Session: {result['subject']} (ID: {result['session_id']}){room_info}\n"
        f"Message ID: {result['message_id']}"
    )
    return [TextContent(type="text", text=text)]


async def _handle_read(arguments: dict) -> list[TextContent]:
    data = await mailbox.get_session_history(arguments["session_id"])
    lines = [f"Session: {data['subject']}\n"]
    for msg in data.get("messages", []):
        lines.append(f"[{msg['created_at']}] {msg['sender_name']}: {msg['content']}")
    return [TextContent(type="text", text="\n".join(lines))]
