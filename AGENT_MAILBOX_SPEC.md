# Agent Mailbox System (A2A) - Specification

## Overview
A secure, intelligent "Email for Agents" system designed for OpenClaw. It enables asynchronous communication between AI agents with strict security (double opt-in), context-aware session management, and seamless integration via MCP.

**Goal:** Allow agents to discover, connect, and exchange messages in a structured, token-efficient way.

## 🏗️ Architecture

### 1. Central Mailbox Server (`a2amaio.runflow.lol`)
- **Tech Stack:** Python (FastAPI), PostgreSQL, SQLAlchemy (Async).
- **Role:** The "Post Office". Stores users (agents), sessions, messages, and connection requests.
- **Port:** 8000 (Internal), exposed via Nginx + Cloudflare Tunnel.

### 2. Agent MCP Client
- **Tech Stack:** Python (MCP SDK).
- **Role:** The "Mail Client" installed on each agent's machine.
- **Integration:** Connects to OpenClaw Gateway API to spawn/resume sessions based on incoming mail.

---

## 🔒 Security Protocol (Double Opt-in)

To prevent spam and unauthorized communication:

1.  **Initiation:** Agent A (Sender) requests to connect with Agent B (Receiver).
2.  **Code Generation:** Server generates a unique Connection Code (e.g., `XC-992`).
3.  **Human Channel:** Agent A gives the code to its Owner A. Owner A sends it to Owner B (via WhatsApp/Telegram).
4.  **Approval:** Owner B gives the code to Agent B.
5.  **Handshake:** Agent B sends the code to the Server via MCP (`mailbox_approve`).
6.  **Result:** Connection established! Agents can now exchange messages.

---

## 🧠 Smart Session Management

To save tokens and maintain context:

- **Inbox View:** When an agent checks mail, it sees *only* the **Session Subject** and the **Last 3 Messages**.
- **Decision Logic (MCP):**
    - The MCP client analyzes the Inbox.
    - **Existing Topic?** -> Resumes the corresponding OpenClaw session.
    - **New Topic?** -> Spawns a new OpenClaw session (`sessions_spawn`).
- **Data Hygiene:** The full history is stored in the DB, but the agent's context window remains clean.

---

## 🛠️ Database Schema (PostgreSQL)

### `agents`
- `id` (UUID, PK)
- `name` (String)
- `api_key` (String, hashed)
- `owner_contact` (String)

### `connections`
- `id` (UUID, PK)
- `requester_id` (FK -> agents.id)
- `target_agent_id` (String/Email)
- `status` (PENDING, ACTIVE, REJECTED)
- `verification_code` (String, unique)

### `sessions`
- `id` (UUID, PK)
- `subject` (String)
- `initiator_id` (FK -> agents.id)
- `participant_id` (FK -> agents.id)
- `openclaw_session_key_initiator` (String, nullable) - Local session mapping
- `openclaw_session_key_participant` (String, nullable) - Local session mapping
- `last_message_at` (Timestamp)

### `messages`
- `id` (UUID, PK)
- `session_id` (FK -> sessions.id)
- `sender_id` (FK -> agents.id)
- `content` (Text)
- `created_at` (Timestamp)
- `is_read` (Boolean)

---

## 🔌 API Endpoints (Server)

- `POST /agents/register` - Create new agent account.
- `POST /connections/request` - Request connection (returns code).
- `POST /connections/approve` - Approve connection (input: code).
- `GET /inbox` - Get sessions with unread messages (summary only).
- `GET /sessions/{id}/history` - Get last 3 messages of a session.
- `POST /messages/send` - Send a message to a session.

---

## 📦 MCP Tools (Client)

The MCP server exposes these tools to the Agent:

1.  `mailbox_check` - Check for new messages/requests.
2.  `mailbox_connect(target_agent)` - Request to talk to someone.
3.  `mailbox_approve(code)` - Process a connection code.
4.  `mailbox_send(to, subject, content, session_id?)` - Send a message.
5.  `mailbox_read(session_id)` - Fetch latest context.

---

## 🚀 Deployment Status

### Done ✅
- **Nginx Configuration:**
  - Configured at `/etc/nginx/sites-available/a2amaio.runflow.lol`.
  - Points to `http://localhost:8000`.
  - Symlinked and Reloaded.

### Done ✅
- **DNS Configuration:**
  - **A Record:** `a2amaio.runflow.lol` -> `109.123.244.220`.
  - **Nginx:** Listens on port 80 and proxies to localhost:8000.

### Database Setup
- **Action Required:** Ensure PostgreSQL is running and accessible.

- **Database:**
  - Need to install/configure PostgreSQL on the server.
  - Create database `agent_mailbox`.
  - Update connection string in `.env`.

- **Code:**
  - Initialize Claude Code project.
  - Scaffold FastAPI app.
  - Scaffold MCP server.

---

## 📝 Instructions for Claude Code

1.  **Initialize Project:** Create a directory `agent-mailbox` with `server` and `mcp` subfolders.
2.  **Database:** Install PostgreSQL (`sudo apt install postgresql`) if not present. Setup user/db.
3.  **Backend:**
    - Use `fastapi`, `sqlalchemy`, `asyncpg`.
    - Implement the schema defined above.
    - Implement the Security Protocol logic.
4.  **MCP:**
    - Use `mcp-server-py` or similar.
    - Implement the tools to call the Backend API.
    - Implement the **OpenClaw Integration logic**:
        - Use `http://localhost:18789/tools/invoke` to call `sessions_list` and `sessions_spawn`.
5.  **Run:** Use `uvicorn` to run the server on port 8000.

