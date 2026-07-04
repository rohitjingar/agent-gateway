# Agent Gateway

**One gateway in front of many MCP servers** — adding the controls that raw
agent→server wiring lacks: JWT auth, per-tool RBAC, rate limiting, an audit log,
end-to-end tracing, tool-poisoning defense, and a **human-approval lane for
high-risk tools**.

It's an API gateway (Kong/Envoy) for the agent era: the *clients* are LLM agents,
the *upstreams* are MCP servers, and there's one thing traditional gateways never
needed — an async human gate on irreversible actions.

---

## Why this exists

Agents call tools, and tools are dangerous: they read files, touch databases,
delete branches, move money. The common wiring connects an LLM **directly** to
MCP servers — no identity, no audit, no quota, no human check. The 2025-26 MCP
security incidents (tool poisoning, cross-tenant leaks, 30+ CVEs) are what that
absence looks like in production. Enterprises solved the same problem for
microservices years ago by moving these concerns to **one chokepoint** at the
edge. This project applies that proven pattern to MCP.

---

## Architecture

```
                         ┌───────────────────────────────────────────────┐
   LLM agent  ──JWT──▶   │                 AGENT GATEWAY                  │
   (client)             │                                                │
                         │  authn → RBAC → rate limit → poisoning check   │
                         │       → HIGH-RISK? ─yes─▶ approval queue        │
                         │                    │no                          │
                         │                    ▼                            │
                         │              proxy tools/call ───────┐          │
                         │  (every step: OTel span + audit row)  │          │
                         └───────────────────────────────────────┼─────────┘
                                    │            │                │
                        ┌───────────┘     ┌──────┘         ┌──────┴───────┐
                        ▼                 ▼                ▼              ▼
                    Postgres           Redis            Jaeger      MCP servers
                  (audit +          (token-bucket     (traces)    (files, github,
                   approvals)        rate limit)                    … namespaced)
```

**Request lifecycle for `POST /tools/call`** (fail-fast, ordered by cost):

1. **Authenticate** the JWT → `Principal(subject, role)` — else `401`.
2. **Resolve** the namespaced tool (`files.read_file`) — unknown → `404`.
3. **Quarantine** check — poisoned tool description → `403`.
4. **Authorize** (per-tool RBAC) — role not allowed → `403`.
5. **Rate limit** (Redis token bucket, per caller) — over quota → `429`.
6. **High-risk?** → create a pending approval, return `202` (do **not** execute).
7. **Proxy** to the owning MCP server; propagate the result.
8. Always: emit an **OpenTelemetry span** and write an **audit record** (every outcome).

---

## Features

| Capability | What it does | Where |
|---|---|---|
| MCP proxy | Discovers upstreams, namespaces tools `<server>.<tool>`, forwards calls | `registry.py`, `routers/gateway.py` |
| JWT auth | HS256 bearer verification → principal | `auth.py` |
| Per-tool RBAC | `role → allowed tools` (wildcards), finer than route-based | `rbac.py` |
| Rate limiting | Redis token bucket (atomic Lua); fail-open | `rate_limit.py` |
| Audit log | Append-only Postgres record (args **hashed**), every outcome | `audit.py` |
| Tracing | OTLP → Jaeger; span per call with AI attributes | `telemetry.py` |
| Human approval | High-risk tools queue for approve/deny; exactly-once execution | `approvals.py`, `routers/admin.py` |
| Poisoning defense | Scan tool descriptions; quarantine suspicious tools | `security.py` |

---

## Quickstart

```bash
# 1. Bring up the whole stack (gateway + 2 MCP servers + Postgres + Redis + Jaeger)
docker compose up --build -d

# 2. Walk through auth, RBAC, rate limiting, and the approval flow
./scripts/demo.sh

# 3. See the traces
open http://localhost:16686        # Jaeger UI, service "agent-gateway"

# 4. Tear down
docker compose down -v
```

Local dev without Docker (uv):

```bash
uv sync
# terminal 1 + 2: the upstream MCP servers
PORT=9101 uv run python -m agent_gateway.mcp_servers.files_server
PORT=9102 uv run python -m agent_gateway.mcp_servers.github_server
# terminal 3: the gateway (rate limit/audit/approvals fall back to no-op if Redis/PG absent)
uv run uvicorn agent_gateway.main:app --reload
```

---

## Roles & policy (demo)

| Role | May call |
|---|---|
| `admin` | everything |
| `developer` | `files.*`, `github.*` (delete_branch still needs approval) |
| `readonly` | `files.read_file`, `files.list_dir`, `github.list_branches` |

Mint a token: `uv run python scripts/mint_token.py alice developer`, or
`POST /auth/token {"subject","role"}` (dev-only endpoint).

## API

| Method + path | Role | Purpose |
|---|---|---|
| `GET /health` | public | liveness |
| `POST /auth/token` | public (dev) | mint a demo JWT |
| `GET /tools` | any | tools the caller may use (+ `high_risk` flag) |
| `POST /tools/call` | any | invoke a tool (or queue it if high-risk) |
| `GET /registry` | any | all servers + tools + risk + allowed |
| `POST /registry/refresh` | admin | re-discover upstreams |
| `GET /audit/recent` | admin | recent audit records |
| `GET /approvals?status=pending` | admin | the approval queue |
| `POST /approvals/{id}/approve` \| `/deny` | admin | decide a pending call |

## Configuration

All via `GATEWAY_*` env vars (see `config.py`): `GATEWAY_JWT_SECRET`,
`GATEWAY_UPSTREAMS` (JSON), `GATEWAY_REDIS_URL`, `GATEWAY_DATABASE_URL`,
`GATEWAY_OTEL_ENABLED`/`_ENDPOINT`, `GATEWAY_RATE_LIMIT_*`,
`GATEWAY_HIGH_RISK_TOOLS`, `GATEWAY_APPROVAL_ENABLED`.

## Tests

```bash
uv run pytest          # in-memory, no infra needed
uv run ruff check .    # lint
```

---

## Tradeoffs & honest limitations

- **Per-call upstream connections.** The proxy opens a fresh MCP session per call
  (correctness over throughput). Production would pool persistent sessions.
- **Fail-open on Redis/Postgres.** If Redis is down, rate limiting is skipped; if
  Postgres is down, audit falls back to a null sink. This favors availability. A
  stricter deployment would fail *closed* for audit (a security control).
- **RBAC policy is a code dict**, not a database/policy engine. Fine for one team;
  a real multi-tenant system wants a DB table or OPA.
- **Poisoning scan is heuristic**, not a guarantee — a first line of defense that
  can false-positive; it quarantines for human review rather than auto-trusting.
- **Approval polling, not push.** The agent polls `GET /approvals/{id}`; a
  production system might use webhooks/websockets.
- **No schema-level argument validation yet** beyond what MCP tools declare.

## Where it breaks at 100× scale (and what changes)

- Per-call MCP handshakes dominate latency → **pool upstream sessions**.
- One Postgres for audit + approvals becomes the bottleneck → **partition audit
  to an append-only store (Kafka/ClickHouse)**, keep approvals transactional.
- In-process registry refresh → **push-based service discovery** + health checks.
- Single gateway instance → horizontal scale (the Redis token bucket is already
  cross-worker-safe; JWT is stateless, so this mostly Just Works).

## Interview narratives

- Why a gateway instead of direct agent→server connections? (one chokepoint)
- Why per-tool RBAC ≠ route-based RBAC? (blast radius is the tool, not the path)
- What did the MCP incidents teach about tool trust? (descriptions are attack surface)
- Where does this break at 100×, and what changes? (see above)
