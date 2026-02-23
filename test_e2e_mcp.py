"""
Comprehensive E2E test for Agent Mailbox MCP system.
Simulates two agents (Alice & Bob) communicating via the mailbox,
with a mock OpenClaw server that handles message forwarding.
"""

import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import websockets

BASE = "http://localhost:8000"
MOCK_OPENCLAW_PORT = 18799  # Use a different port to avoid conflicts
MOCK_TOKEN = "test-token-123"

# ─── Shared state ─────────────────────────────────────────────────────────────
received_by_mock = []   # Messages received by mock OpenClaw
bob_reply_sent = asyncio.Event() if False else None  # set up in main

PASS = "✅"
FAIL = "❌"
results = []


def log(symbol, test, detail=""):
    msg = f"{symbol} {test}"
    if detail:
        msg += f"\n   {detail}"
    print(msg)
    results.append((symbol, test))


# ─── Mock OpenClaw HTTP Server ─────────────────────────────────────────────────
class MockOpenClawHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default logging

    def do_POST(self):
        if self.path != "/tools/invoke":
            self.send_response(404)
            self.end_headers()
            return

        # Check auth
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {MOCK_TOKEN}":
            self.send_response(401)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        tool = body.get("tool")
        args = body.get("args", {})
        received_by_mock.append({"tool": tool, "args": args})

        if tool == "sessions_send":
            session_key = args.get("sessionKey", "")
            message = args.get("message", "")
            print(f"   [MockOpenClaw] sessions_send → session={session_key}")
            print(f"   [MockOpenClaw] message: {message[:80]}...")

            # Simulate agent reading the message and responding
            reply = f"[Bob's Agent] Got your message! I understood: '{message[:50]}...'. Will process this."
            response = {
                "ok": True,
                "result": {
                    "runId": "mock-run-001",
                    "status": "ok",
                    "reply": reply,
                },
            }
        elif tool == "sessions_list":
            response = {
                "ok": True,
                "result": [
                    {
                        "key": "agent:main:main",
                        "kind": "main",
                        "displayName": "Main Session",
                        "updatedAt": "2026-02-22T19:00:00Z",
                    }
                ],
            }
        else:
            response = {"ok": False, "error": f"Unknown tool: {tool}"}

        body_out = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)


def start_mock_openclaw():
    server = HTTPServer(("127.0.0.1", MOCK_OPENCLAW_PORT), MockOpenClawHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ─── HTTP helpers ──────────────────────────────────────────────────────────────
def api(method, path, data=None, key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    with httpx.Client() as client:
        func = getattr(client, method.lower())
        kwargs = {"headers": headers}
        if data is not None:
            kwargs["json"] = data
        resp = func(BASE + path, **kwargs)
        return resp.json()


# ─── MCP Tool tests (direct function calls) ───────────────────────────────────
async def test_mcp_tools(alice_key, bob_key, alice_name, bob_name):
    """Test all 5 MCP tools by calling the underlying Python functions directly."""
    print("\n━━━ TEST GROUP 1: MCP Tool Functions ━━━")

    # Set up env for Alice's MCP context
    os.environ["MAILBOX_SERVER_URL"] = "localhost:8000"
    os.environ["MAILBOX_API_KEY"] = alice_key
    os.environ["OPENCLAW_GATEWAY_URL"] = f"http://127.0.0.1:{MOCK_OPENCLAW_PORT}"
    os.environ["OPENCLAW_GATEWAY_TOKEN"] = MOCK_TOKEN

    # Reload settings with new env
    sys.path.insert(0, "/home/shlomo/agent-mailbox/mcp")
    import importlib
    import mailbox_mcp.config as cfg_mod
    cfg_mod.settings = cfg_mod.Settings()

    import mailbox_mcp.mailbox_client as mc_mod
    from mailbox_mcp.mailbox_client import MailboxClient
    from mailbox_mcp.server import (
        _handle_check,
        _handle_connect,
        _handle_approve,
        _handle_send,
        _handle_read,
    )
    import mailbox_mcp.server as srv_mod
    mc_mod.settings = cfg_mod.settings  # patch before MailboxClient() reads settings
    srv_mod.mailbox = MailboxClient()

    # Tool 1: mailbox_check (Alice's empty inbox initially)
    result = await _handle_check()
    text = result[0].text
    if "No message sessions" in text or "Inbox" in text:
        log(PASS, "mailbox_check — Alice's inbox readable")
    else:
        log(FAIL, "mailbox_check", text)

    # Tool 2: mailbox_connect (Alice connects to Bob)
    result = await _handle_connect({"target_agent_name": bob_name, "message": "Hi Bob, let's work together!"})
    text = result[0].text
    if "Verification code:" in text:
        code = text.split("Verification code:")[1].strip().split("\n")[0].strip()
        log(PASS, f"mailbox_connect — code received: {code}")
    else:
        log(FAIL, "mailbox_connect", text)
        return None, None

    # Tool 3: mailbox_approve (Bob approves) — switch to Bob's context
    os.environ["MAILBOX_API_KEY"] = bob_key
    cfg_mod.settings = cfg_mod.Settings()
    mc_mod.settings = cfg_mod.settings
    srv_mod.mailbox = MailboxClient()

    result = await _handle_approve({"code": code})
    text = result[0].text
    if "Connection approved" in text:
        log(PASS, f"mailbox_approve — Bob approved connection with {alice_name}")
    else:
        log(FAIL, "mailbox_approve", text)
        return None, None

    # Tool 4: mailbox_send (Alice sends to Bob)
    os.environ["MAILBOX_API_KEY"] = alice_key
    cfg_mod.settings = cfg_mod.Settings()
    mc_mod.settings = cfg_mod.settings
    srv_mod.mailbox = MailboxClient()

    result = await _handle_send({
        "to": bob_name,
        "subject": "Project Alpha",
        "content": "Hey Bob! Can you help me with the data analysis for Project Alpha?",
    })
    text = result[0].text
    if "Message sent" in text:
        session_id = text.split("ID:")[1].split(")")[0].strip()
        log(PASS, f"mailbox_send — message sent, session: {session_id[:8]}...")
    else:
        log(FAIL, "mailbox_send", text)
        return None, None

    # Tool 5: mailbox_check (Bob sees unread message)
    os.environ["MAILBOX_API_KEY"] = bob_key
    cfg_mod.settings = cfg_mod.Settings()
    mc_mod.settings = cfg_mod.settings
    srv_mod.mailbox = MailboxClient()

    result = await _handle_check()
    text = result[0].text
    if "unread" in text.lower() and "Project Alpha" in text:
        log(PASS, "mailbox_check — Bob sees unread message from Alice")
    else:
        log(FAIL, "mailbox_check (Bob inbox)", text)

    # Tool 5b: mailbox_read (Bob reads the session)
    result = await _handle_read({"session_id": session_id})
    text = result[0].text
    if "Project Alpha" in text and "data analysis" in text:
        log(PASS, "mailbox_read — Bob reads full session history")
    else:
        log(FAIL, "mailbox_read", text)

    # Tool 4b: mailbox_send (Bob replies to Alice)
    result = await _handle_send({
        "to": alice_name,
        "subject": "Project Alpha",
        "content": "Sure Alice! I can help with Project Alpha. What data do you need analyzed?",
        "session_id": session_id,
    })
    text = result[0].text
    if "Message sent" in text:
        log(PASS, "mailbox_send — Bob replies to Alice in same session")
    else:
        log(FAIL, "mailbox_send (Bob reply)", text)

    # Tool 5c: mailbox_check (Alice sees Bob's reply)
    os.environ["MAILBOX_API_KEY"] = alice_key
    cfg_mod.settings = cfg_mod.Settings()
    mc_mod.settings = cfg_mod.settings
    srv_mod.mailbox = MailboxClient()

    result = await _handle_check()
    text = result[0].text
    if "Project Alpha" in text:
        log(PASS, "mailbox_check — Alice sees Bob's reply in Project Alpha thread")
    else:
        log(FAIL, "mailbox_check (Alice sees reply)", text)

    return session_id, code


# ─── WebSocket push test ───────────────────────────────────────────────────────
async def test_websocket_push(alice_key, bob_key, bob_name):
    """Test that WebSocket pushes new_message events to recipient."""
    print("\n━━━ TEST GROUP 2: WebSocket Real-time Push ━━━")

    ws_url = f"ws://localhost:8000/ws?api_key={bob_key}"
    received_events = []

    async def bob_ws_listener():
        try:
            async with websockets.connect(ws_url) as ws:
                # Signal we're connected
                connected.set()
                async for raw in ws:
                    event = json.loads(raw)
                    received_events.append(event)
                    if event.get("type") == "new_message":
                        got_message.set()
                        break
        except Exception as e:
            received_events.append({"error": str(e)})
            connected.set()
            got_message.set()

    connected = asyncio.Event()
    got_message = asyncio.Event()

    # Start Bob's WS listener
    listener_task = asyncio.create_task(bob_ws_listener())
    await asyncio.wait_for(connected.wait(), timeout=5)

    # Alice sends a new message (will push via WS to Bob)
    resp = api("POST", "/messages/send",
        {"to": bob_name, "subject": "WS Push Test", "content": "Can you hear me Bob?"},
        key=alice_key)

    if "message_id" not in resp:
        log(FAIL, "WebSocket push — failed to send message", str(resp))
        listener_task.cancel()
        return

    # Wait for Bob to receive the push
    try:
        await asyncio.wait_for(got_message.wait(), timeout=5)
        event = received_events[-1] if received_events else {}
        if event.get("type") == "new_message" and event.get("content") == "Can you hear me Bob?":
            log(PASS, "WebSocket push — Bob received new_message event in real-time")
            log(PASS, f"   Event details: from={event.get('from_agent')}, subject={event.get('subject')}")
        else:
            log(FAIL, "WebSocket push — wrong event received", str(event))
    except asyncio.TimeoutError:
        log(FAIL, "WebSocket push — no event received within 5s", str(received_events))

    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass


# ─── Full A2A with WS Client + Mock OpenClaw ──────────────────────────────────
async def test_full_a2a_ws_client(alice_key, bob_key, alice_name, bob_name):
    """
    Full A2A simulation:
    1. Alice sends message to Bob via REST
    2. Bob's MailboxWSClient receives WS push
    3. WSClient forwards to mock OpenClaw (sessions_send)
    4. Mock OpenClaw returns a reply
    5. WSClient sends reply back via mailbox
    6. Alice sees Bob's auto-reply in her inbox
    """
    print("\n━━━ TEST GROUP 3: Full A2A (WS Client + Mock OpenClaw) ━━━")

    sys.path.insert(0, "/home/shlomo/agent-mailbox/mcp")
    import mailbox_mcp.config as cfg_mod
    import mailbox_mcp.mailbox_client as mc_mod
    import mailbox_mcp.openclaw as oc_mod
    import mailbox_mcp.ws_client as ws_mod
    from mailbox_mcp.mailbox_client import MailboxClient
    from mailbox_mcp.openclaw import OpenClawClient
    from mailbox_mcp.ws_client import MailboxWSClient

    def patch_settings(new_settings):
        """Patch settings in all MCP modules so new MailboxClient/OpenClawClient picks up changes."""
        cfg_mod.settings = new_settings
        mc_mod.settings = new_settings
        oc_mod.settings = new_settings
        ws_mod.settings = new_settings

    # Configure Bob's WS client to use mock OpenClaw
    os.environ["MAILBOX_SERVER_URL"] = "localhost:8000"
    os.environ["MAILBOX_API_KEY"] = bob_key
    os.environ["OPENCLAW_GATEWAY_URL"] = f"http://127.0.0.1:{MOCK_OPENCLAW_PORT}"
    os.environ["OPENCLAW_GATEWAY_TOKEN"] = MOCK_TOKEN
    patch_settings(cfg_mod.Settings())

    bob_mailbox = MailboxClient()
    bob_openclaw = OpenClawClient()
    bob_ws = MailboxWSClient(bob_mailbox, bob_openclaw)

    # Track when Bob's WS client forwards to OpenClaw and sends reply
    forwarded_to_openclaw = asyncio.Event()
    reply_sent_to_mailbox = asyncio.Event()
    original_handle = bob_ws._handle_new_message

    async def instrumented_handle(event):
        await original_handle(event)
        # Check if mock received the call
        if any(m["tool"] == "sessions_send" for m in received_by_mock):
            forwarded_to_openclaw.set()
        reply_sent_to_mailbox.set()

    bob_ws._handle_new_message = instrumented_handle

    # Start Bob's WS client loop in background
    ws_task = asyncio.create_task(bob_ws.connect_loop())

    # Wait a moment for WS connection to establish
    await asyncio.sleep(1.5)

    # Clear previous mock calls
    received_by_mock.clear()

    # Alice sends a message to Bob
    os.environ["MAILBOX_API_KEY"] = alice_key
    patch_settings(cfg_mod.Settings())
    alice_mailbox = MailboxClient()
    send_resp = await alice_mailbox.send_message(
        to=bob_name,
        subject="Automated Task",
        content="Bob, please run the quarterly report script and tell me the results.",
    )
    session_id = send_resp["session_id"]
    log(PASS, f"A2A — Alice sent message (session: {session_id[:8]}...)")

    # Wait for Bob's WS client to process it
    try:
        await asyncio.wait_for(reply_sent_to_mailbox.wait(), timeout=10)

        # Verify OpenClaw was called
        if any(m["tool"] == "sessions_send" for m in received_by_mock):
            call = next(m for m in received_by_mock if m["tool"] == "sessions_send")
            log(PASS, "A2A — Bob's WS client forwarded message to OpenClaw (sessions_send)")
            log(PASS, f"   sessionKey: {call['args'].get('sessionKey')}")
            msg_preview = call["args"].get("message", "")[:60]
            log(PASS, f"   message: {msg_preview}...")
        else:
            log(FAIL, "A2A — OpenClaw was NOT called", str(received_by_mock))

        # Check Alice's inbox for Bob's auto-reply
        await asyncio.sleep(1)  # Give reply a moment to arrive
        os.environ["MAILBOX_API_KEY"] = alice_key
        patch_settings(cfg_mod.Settings())
        alice_mailbox2 = MailboxClient()
        history = await alice_mailbox2.get_session_history(session_id, limit=10)
        messages = history.get("messages", [])
        bob_replies = [m for m in messages if m["sender_name"] == bob_name]

        if bob_replies:
            log(PASS, f"A2A — Alice received Bob's auto-reply: '{bob_replies[-1]['content'][:60]}...'")
        else:
            log(FAIL, "A2A — No auto-reply from Bob found in Alice's inbox",
                f"messages: {[m['sender_name'] for m in messages]}")

    except asyncio.TimeoutError:
        log(FAIL, "A2A — WS client did not process message within 10s")

    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass


# ─── connection_request WS event test ─────────────────────────────────────────
async def test_connection_request_push(charlie_key, dave_key, dave_name):
    """Test that connection_request events are pushed via WebSocket."""
    print("\n━━━ TEST GROUP 4: Connection Request WS Push ━━━")

    ws_url = f"ws://localhost:8000/ws?api_key={dave_key}"
    received_events = []
    connected = asyncio.Event()
    got_conn_request = asyncio.Event()

    async def dave_listener():
        try:
            async with websockets.connect(ws_url) as ws:
                connected.set()
                async for raw in ws:
                    event = json.loads(raw)
                    received_events.append(event)
                    if event.get("type") == "connection_request":
                        got_conn_request.set()
                        break
        except Exception as e:
            received_events.append({"error": str(e)})
            connected.set()
            got_conn_request.set()

    listener = asyncio.create_task(dave_listener())
    await asyncio.wait_for(connected.wait(), timeout=5)

    # Charlie requests connection to Dave
    resp = api("POST", "/connections/request",
        {"target_agent_name": dave_name, "message": "Hi Dave, let's connect!"},
        key=charlie_key)

    if "verification_code" not in resp:
        log(FAIL, "Connection request push — failed to create connection", str(resp))
        listener.cancel()
        return

    code = resp["verification_code"]
    try:
        await asyncio.wait_for(got_conn_request.wait(), timeout=5)
        event = received_events[-1] if received_events else {}
        if event.get("type") == "connection_request" and "verification_code" in event:
            log(PASS, f"Connection request push — Dave received WS push (code: {event['verification_code']})")
        else:
            log(FAIL, "Connection request push — wrong event", str(event))
    except asyncio.TimeoutError:
        log(FAIL, "Connection request push — no event within 5s")

    listener.cancel()
    try:
        await listener
    except asyncio.CancelledError:
        pass


# ─── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("Agent Mailbox System — Full E2E MCP Test Suite")
    print("=" * 60)

    # Check server is up
    try:
        resp = httpx.get(BASE + "/health", timeout=3)
        assert resp.json()["status"] == "ok"
        print(f"\n{PASS} Server is running at {BASE}\n")
    except Exception as e:
        print(f"{FAIL} Server not running: {e}")
        sys.exit(1)

    # Start mock OpenClaw
    mock_server = start_mock_openclaw()
    print(f"{PASS} Mock OpenClaw running on port {MOCK_OPENCLAW_PORT}\n")
    time.sleep(0.2)

    # ── Register fresh agents (unique suffix to avoid conflicts) ──────────────
    print("━━━ Setup: Registering Agents ━━━")
    ts = str(int(time.time()))[-5:]
    a_name, b_name, c_name, d_name = f"alice-{ts}", f"bob-{ts}", f"charlie-{ts}", f"dave-{ts}"
    alice = api("POST", "/agents/register", {"name": a_name})
    bob   = api("POST", "/agents/register", {"name": b_name})
    charlie = api("POST", "/agents/register", {"name": c_name})
    dave  = api("POST", "/agents/register", {"name": d_name})

    for agent, name in [(alice, a_name), (bob, b_name), (charlie, c_name), (dave, d_name)]:
        if "api_key" not in agent:
            print(f"{FAIL} Failed to register {name}: {agent}")
            sys.exit(1)

    alice_key = alice["api_key"]
    bob_key   = bob["api_key"]
    charlie_key = charlie["api_key"]
    dave_key  = dave["api_key"]
    print(f"{PASS} Registered: {a_name}, {b_name}, {c_name}, {d_name}\n")

    # ── Run test groups ────────────────────────────────────────────────────────
    await test_mcp_tools(alice_key, bob_key, a_name, b_name)
    await test_websocket_push(alice_key, bob_key, b_name)
    await test_full_a2a_ws_client(alice_key, bob_key, a_name, b_name)
    await test_connection_request_push(charlie_key, dave_key, d_name)

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for s, _ in results if s == PASS)
    failed = sum(1 for s, _ in results if s == FAIL)
    for symbol, test in results:
        print(f"  {symbol} {test}")
    print(f"\nTotal: {passed + failed} | Passed: {passed} | Failed: {failed}")
    print("=" * 60)

    mock_server.shutdown()
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
