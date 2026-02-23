# OpenClaw Gateway API Research

**Date:** 2026-02-22
**Purpose:** Implementation-ready reference for the Agent Mailbox System

---

## 1. tools/invoke HTTP API

### Endpoint

```
POST http://<gateway-host>:<port>/tools/invoke
```

Default: `http://127.0.0.1:18789/tools/invoke`

### Authentication

Bearer token via `Authorization` header. Token is configured via `gateway.auth.token` in `openclaw.json` or the `OPENCLAW_GATEWAY_TOKEN` environment variable.

```
Authorization: Bearer <token>
```

### Request Format

```json
{
  "tool": "<tool_name>",
  "action": "json",
  "args": { ... },
  "sessionKey": "main",
  "dryRun": false
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool` | string | Yes | Tool name to execute |
| `action` | string | No | Mapped to args if tool schema supports it. Use `"json"` for structured output |
| `args` | object | No | Tool-specific arguments |
| `sessionKey` | string | No | Target session; defaults to configured main session |
| `dryRun` | boolean | No | Reserved for future use |

**Additional Headers:**
- `Content-Type: application/json`
- `x-openclaw-message-channel: <channel>` (optional)
- `x-openclaw-account-id: <accountId>` (optional)

### Response Format

**Success (200):**
```json
{ "ok": true, "result": { ... } }
```

**Error codes:**
| Code | Meaning |
|------|---------|
| 400 | Invalid request or input error |
| 401 | Unauthorized (bad/missing token) |
| 404 | Tool unavailable or not in allowlist |
| 429 | Rate-limited (auth failures) |
| 500 | Execution error |

### HTTP Default Deny List

The following tools are **hard-denied by default** on the HTTP endpoint, even if session policy allows them:
- `sessions_spawn`
- `sessions_send`
- `gateway`
- `whatsapp_login`

To enable them, you must explicitly configure `gateway.tools.allow` (see Section 5).

---

## 2. sessions_spawn

Creates an isolated sub-agent session that runs a task asynchronously.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | Yes | Work description / instructions for the sub-agent |
| `label` | string | No | Display name for logs |
| `agentId` | string | No | Target agent (requires allowlist permission) |
| `model` | string | No | Override sub-agent model |
| `thinking` | string | No | Override thinking/reasoning level |
| `runTimeoutSeconds` | number | No | Abort timeout |
| `thread` | boolean | No | Enable thread-bound routing (default: false) |
| `mode` | string | No | `"run"` or `"session"` (requires `thread=true`) |
| `cleanup` | string | No | `"delete"` or `"keep"` (default: keep) |

### Return Value

Returns **immediately** (non-blocking):

```json
{
  "status": "accepted",
  "runId": "<uuid>",
  "childSessionKey": "subagent:<parentId>:d<depth>"
}
```

**Error case:**
```json
{
  "status": "error",
  "error": "...",
  "childSessionKey": "...",
  "runId": ""
}
```

### Session Key Format

Child sessions use hierarchical keys:
- Depth 1: `subagent:<parentId>:d1`
- Depth 2+: `subagent:<grandparentId>:sub:<parentId>:d<depth>`

Maximum depth is controlled by `tools.subagents.maxDepth` (default: 3).

### Result Delivery (Announce Mechanism)

| Mode | Behavior |
|------|----------|
| `REPLY_BACK` | Results delivered to parent via `sessions_send` |
| `ANNOUNCE` | Posted to parent's current channel |
| `ANNOUNCE_SKIP` | No automatic delivery |
| `REPLY_SKIP` | Skip reply; parent polls manually |

### Example curl

```bash
curl -sS http://127.0.0.1:18789/tools/invoke \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "tool": "sessions_spawn",
    "args": {
      "task": "Research the latest Node.js release notes",
      "label": "research-task",
      "model": "anthropic:claude-sonnet-4-20250514"
    }
  }'
```

**Important:** This will return 404 unless `sessions_spawn` is added to `gateway.tools.allow`.

### Key Behaviors

- Creates isolated `agent:<agentId>:subagent:<uuid>` sessions
- Sub-agents do NOT get session tools (no recursive spawning by default)
- Auto-archives after 60 minutes (configurable)
- Announces results to requester's chat channel by default
- Progressive tool restrictions by depth (depth 2+ loses write; depth 3+ is read-only)

---

## 3. sessions_send

Routes messages between sessions with optional response waiting.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sessionKey` | string | Yes | Target session identifier |
| `message` | string | Yes | Content to send |
| `timeoutSeconds` | number | No | Wait duration; 0 = fire-and-forget, default > 0 |

### Return Values

**Fire-and-forget (timeoutSeconds=0):**
```json
{ "runId": "<uuid>", "status": "accepted" }
```

**Success (waited for reply):**
```json
{ "runId": "<uuid>", "status": "ok", "reply": "..." }
```

**Timeout:**
```json
{ "runId": "<uuid>", "status": "timeout", "error": "..." }
```

**Error:**
```json
{ "runId": "<uuid>", "status": "error", "error": "..." }
```

### Reply-Back Loop

`sessions_send` implements an automatic "reply-back loop" allowing up to 5 alternating exchanges between agents. The target agent can reply `REPLY_SKIP` to terminate the conversation early.

### Example curl

```bash
curl -sS http://127.0.0.1:18789/tools/invoke \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "tool": "sessions_send",
    "args": {
      "sessionKey": "agent:main:main",
      "message": "What is the status of the deployment?",
      "timeoutSeconds": 30
    }
  }'
```

**Important:** Returns 404 unless `sessions_send` is added to `gateway.tools.allow`.

---

## 4. sessions_list

Lists active sessions across the agent system.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `kinds` | string | No | Filter: `"main"`, `"group"`, `"cron"`, `"hook"`, `"node"`, `"other"` |
| `limit` | number | No | Maximum rows returned (default ~200) |
| `activeMinutes` | number | No | Sessions updated within N minutes |
| `messageLimit` | number | No | Include last N messages (0 = none, default 0) |

### Return Value

Array of session objects:

```json
[
  {
    "key": "agent:main:main",
    "kind": "main",
    "channel": "cli",
    "displayName": "Main Session",
    "updatedAt": "2026-02-22T10:00:00Z",
    "sessionId": "uuid",
    "model": "anthropic:claude-sonnet-4-20250514",
    "contextTokens": 12000,
    "totalTokens": 45000,
    "thinkingLevel": "normal",
    "verboseLevel": 1,
    "sendPolicy": null,
    "lastChannel": "cli",
    "lastTo": null,
    "deliveryContext": null,
    "transcriptPath": "/path/to/transcript.jsonl",
    "messages": []
  }
]
```

### Example curl (no HTTP deny restriction)

```bash
curl -sS http://127.0.0.1:18789/tools/invoke \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"tool": "sessions_list", "action": "json", "args": {}}'
```

`sessions_list` is **NOT** in the default HTTP deny list and works without `gateway.tools.allow` override.

---

## 5. openclaw.json Configuration

### File Location

```
~/.openclaw/openclaw.json
```

### Enabling Session Tools for HTTP API

The critical configuration to allow `sessions_spawn` and `sessions_send` over HTTP:

```json5
{
  // Gateway configuration
  "gateway": {
    "port": 18789,
    "bind": "loopback",
    "auth": {
      "mode": "token",
      "token": "your-secret-token-here"
    },
    "tools": {
      // Override the HTTP hard-deny list
      "allow": ["sessions_spawn", "sessions_send"]
    }
  },

  // Global tool policy
  "tools": {
    "allow": ["group:sessions"],
    "sessions": {
      "visibility": "tree"  // "self" | "tree" | "agent" | "all"
    }
  }
}
```

**Two layers of configuration are needed:**
1. `tools.allow: ["group:sessions"]` -- enables session tools globally for the agent
2. `gateway.tools.allow: ["sessions_spawn", "sessions_send"]` -- overrides the HTTP hard-deny list

### Adding an MCP Server

MCP servers are configured per-agent within `openclaw.json`:

```json5
{
  "agents": {
    "list": [
      {
        "id": "main",
        "model": "anthropic:claude-sonnet-4-20250514",
        // Option A: mcp.servers array (newer format from GitHub issue #4834)
        "mcp": {
          "servers": [
            {
              "name": "my-mailbox-server",
              "command": "node",
              "args": ["/path/to/mcp-server.js"],
              "env": {
                "SOME_KEY": "value"
              }
            }
          ]
        }
      }
    ]
  }
}
```

Alternative format seen in community configs (older/simplified):

```json5
{
  "agents": {
    "main": {
      "model": "anthropic:claude-sonnet-4-20250514",
      "mcpServers": {
        "my-mailbox-server": {
          "command": "node",
          "args": ["/path/to/mcp-server.js"],
          "env": {
            "SOME_KEY": "value"
          }
        }
      }
    }
  }
}
```

Or at the provider level:

```json5
{
  "provider": {
    "mcpServers": {
      "my-server": {
        "command": "npx",
        "args": ["-y", "@my/mcp-server"]
      }
    }
  }
}
```

**Note:** The exact format may depend on the OpenClaw version. The `agents.list[].mcp.servers[]` format was proposed in issue #4834 but the community uses `mcpServers` as a key-value object in practice.

### Full Example Configuration

```json5
{
  "gateway": {
    "port": 18789,
    "bind": "loopback",
    "auth": {
      "mode": "token",
      "token": "your-secret-token"
    },
    "tools": {
      "allow": ["sessions_spawn", "sessions_send"]
    }
  },
  "tools": {
    "allow": ["group:sessions"],
    "sessions": {
      "visibility": "agent"
    }
  },
  "agents": {
    "list": [
      {
        "id": "main",
        "model": "anthropic:claude-sonnet-4-20250514",
        "mcp": {
          "servers": [
            {
              "name": "agent-mailbox",
              "command": "node",
              "args": ["./mcp-servers/agent-mailbox/index.js"]
            }
          ]
        }
      }
    ]
  }
}
```

### Tool Groups Reference

| Group | Tools Included |
|-------|---------------|
| `group:sessions` | `sessions_list`, `sessions_history`, `sessions_send`, `sessions_spawn`, `session_status` |
| `group:runtime` | `exec`, `bash`, `process` |
| `group:fs` | `read`, `write`, `edit`, `apply_patch` |
| `group:memory` | `memory_search`, `memory_get` |
| `group:web` | `web_search`, `web_fetch` |
| `group:ui` | `browser`, `canvas` |

### Tool Profiles

| Profile | Access Level |
|---------|-------------|
| `"minimal"` | Only `session_status` |
| `"coding"` | File I/O + execution + memory |
| `"messaging"` | Messaging + session tools |
| `"full"` (default) | No restrictions |

---

## 6. Authentication

### Auth Modes

| Mode | Config Key | Description |
|------|-----------|-------------|
| `token` | `gateway.auth.token` | Shared secret Bearer token (recommended) |
| `password` | `gateway.auth.password` | Password-based auth |
| `trusted-proxy` | `gateway.auth.mode` | Delegates to identity-aware reverse proxy |

### Token Configuration

**Via openclaw.json:**
```json5
{
  "gateway": {
    "auth": {
      "mode": "token",
      "token": "your-long-random-token"
    }
  }
}
```

**Via environment variable:**
```bash
export OPENCLAW_GATEWAY_TOKEN="your-long-random-token"
```

### Rate Limiting

```json5
{
  "gateway": {
    "auth": {
      "rateLimit": {
        "maxAttempts": 10,
        "windowMs": 60000,
        "lockoutMs": 300000
      }
    }
  }
}
```

### Tailscale Auth Bypass

When `gateway.auth.allowTailscale: true` and binding to tailnet, Tailscale-sourced connections skip authentication. Note: HTTP `/tools/invoke` still requires token/password even with Tailscale.

### Important Notes

- Gateway auth is **required by default** -- if no token/password is configured, the Gateway refuses connections (fail-closed)
- Token rotation requires restarting the Gateway
- The same token is used for both WebSocket connections and HTTP `/tools/invoke` calls

---

## 7. WebSocket Protocol & Events

### Connection

```
ws://127.0.0.1:18789
```

The Gateway multiplexes WebSocket and HTTP on the same port (18789).

### Message Format

Three frame types, all JSON text frames:

**Request:**
```json
{ "type": "req", "id": "<unique-id>", "method": "<command>", "params": { ... } }
```

**Response:**
```json
{ "type": "res", "id": "<matching-id>", "ok": true, "payload": { ... } }
```

**Event (server-push):**
```json
{ "type": "event", "event": "<event-name>", "payload": { ... }, "seq": 1, "stateVersion": "..." }
```

### Connection Handshake

1. Server sends challenge: `{"type":"event","event":"connect.challenge","payload":{"nonce":"...","ts":1737264000000}}`
2. Client sends `connect` request with protocol version, role, scopes, auth token, device info
3. Server responds with `hello-ok` including device token

### Key WebSocket RPC Commands

| Category | Commands |
|----------|----------|
| **Config** | `config.get`, `config.patch`, `config.apply` |
| **Agent** | `agent.request`, `agent.wait`, `agent.stop` |
| **Sessions** | `sessions.list`, `sessions.patch`, `sessions.delete`, `sessions.history`, `sessions.send` |
| **Channels** | `message.send`, `channels.list`, `pairing.approve` |
| **Nodes** | `node.list`, `node.describe`, `node.invoke` |
| **Cron** | `cron.add`, `cron.edit`, `cron.run` |

### Real-Time Events

The Gateway broadcasts events to connected WebSocket clients:

- **`connect.challenge`** -- initial handshake
- **`shutdown`** -- gateway shutting down, includes `reason` and `restartExpectedMs`
- **Agent status events** -- includes `status:"cancelled"` on abort
- **Session transcript updates** -- persisted to JSONL files
- **`exec.approval.requested`** -- tool execution approval needed
- **Presence events** -- device connection/disconnection

### Event Subscription Model

The WebSocket protocol appears to use **role-based event delivery** rather than explicit subscriptions. Clients declare their role (`operator` or `node`) and scopes during handshake, and receive events relevant to their role. Operators with `operator.read` scope receive session and agent events.

### Limitations for Real-Time Message Delivery

There is **no documented explicit event subscription for "new message in session X"** as a push event. The pattern for monitoring sessions appears to be:
1. Connect as an `operator` WebSocket client
2. Receive agent/session events as they occur
3. Use `sessions.history` to poll for new messages
4. Or use `sessions_send` with `timeoutSeconds > 0` for synchronous wait

---

## 8. Gaps and Unknowns

### High Confidence

- **tools/invoke API format**: Well-documented, exact JSON schema confirmed
- **sessions_spawn/send/list parameters**: Fully documented with return types
- **Authentication**: Token-based auth is straightforward
- **HTTP deny list override**: `gateway.tools.allow` is the mechanism

### Medium Confidence

- **MCP server configuration format**: Multiple formats seen in docs (agents.list[].mcp.servers[] vs agents.main.mcpServers vs provider.mcpServers). The exact format may vary by OpenClaw version. Need to test against the running instance.
- **WebSocket event types for real-time messages**: Events exist but no explicit "new message in session" subscription documented. Real-time monitoring may require being an operator WebSocket client.
- **sessions_spawn childSessionKey exact format**: Documented as `subagent:<parentId>:d<depth>` but actual UUIDs and exact format should be verified against a running instance.

### Low Confidence / Unknown

- **Explicit WebSocket subscription for incoming messages**: No documented mechanism to subscribe to "new message arrives in session X" events. This is a significant gap for the Agent Mailbox System -- we may need to poll via `sessions_list` with `activeMinutes` or `sessions_history`.
- **MCP server env variable passthrough**: Whether environment variables configured in `mcp.servers[].env` are properly passed through to the spawned process is assumed but not explicitly documented.
- **Webhook/callback for session events**: No documented webhook mechanism for push notifications when a session receives a message. The REST endpoint feature request (GitHub issue #20934) is still open, suggesting HTTP-based session management is incomplete.
- **Max payload size**: The docs mention 2MB max payload for /tools/invoke but do not specify limits for session message content.

### Critical Implementation Considerations

1. **HTTP deny list is the main obstacle**: `sessions_spawn` and `sessions_send` are denied by default on HTTP. Must configure `gateway.tools.allow` before any HTTP-based agent communication works.

2. **Two-layer config needed**: Both `tools.allow: ["group:sessions"]` (global) AND `gateway.tools.allow: ["sessions_spawn", "sessions_send"]` (HTTP-specific) must be set.

3. **sessions_spawn is async**: It returns immediately with `childSessionKey`. To get results, you must either:
   - Wait for the announce mechanism to deliver results
   - Poll with `sessions_history` using the `childSessionKey`
   - Use `sessions_send` to communicate with the child session

4. **No REST session management**: The HTTP `/v1/chat/completions` endpoint lacks session control. Session management is primarily a WebSocket operation. The `/tools/invoke` endpoint is the HTTP bridge for tool-level access.

5. **Security audit warning**: OpenClaw warns when `gateway.tools.allow` re-enables default-denied HTTP tools, as it increases RCE blast radius if the Gateway is network-reachable.

---

## Sources

- [Tools Invoke API](https://docs.openclaw.ai/gateway/tools-invoke-http-api)
- [Session Tools](https://docs.openclaw.ai/concepts/session-tool)
- [Configuration Reference](https://docs.openclaw.ai/gateway/configuration-reference)
- [Security](https://docs.openclaw.ai/gateway/security)
- [Gateway Protocol](https://docs.openclaw.ai/gateway/protocol)
- [Session Management](https://docs.openclaw.ai/concepts/session)
- [Subagent Management (DeepWiki)](https://deepwiki.com/openclaw/openclaw/9.6-subagent-management)
- [Gateway Configuration (DeepWiki)](https://deepwiki.com/openclaw/openclaw/3.1-gateway-configuration)
- [Gateway Commands (DeepWiki)](https://deepwiki.com/openclaw/openclaw/12.1-gateway-commands)
- [OpenClaw Config Guide (BetterLink)](https://eastondev.com/blog/en/posts/ai/20260205-openclaw-config-guide/)
- [Native MCP Support Issue #4834](https://github.com/openclaw/openclaw/issues/4834)
- [REST Endpoint Feature Request Issue #20934](https://github.com/openclaw/openclaw/issues/20934)
- [Security Advisory OC-02](https://github.com/openclaw/openclaw/security/advisories/GHSA-943q-mwmv-hhvh)
- [OpenClaw Architecture Overview (ppaolo)](https://ppaolo.substack.com/p/openclaw-system-architecture-overview)
