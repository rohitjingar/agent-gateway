# Agent Gateway — Learnings (read this to explain the whole project)

Written so you can explain **every concept and every file** in an interview,
from first principles, mapped to backend things you already know.

---

## 0. The one-sentence pitch

> An API gateway for AI agents: one chokepoint in front of many MCP servers that
> adds auth, per-tool RBAC, rate limiting, audit, tracing, tool-poisoning defense,
> and — the new idea — an **async human-approval lane** for irreversible actions.

Everything below is a supporting argument for that sentence.

---

## 1. Modern Python packaging (Phase 0)

**What:** `uv` (one Rust tool replacing pyenv+venv+pip+pip-tools+runner), a `src/`
layout, and a committed `uv.lock`.

**Why it matters:** "works on my machine" comes from five tools disagreeing about
versions. `uv.lock` pins the entire dependency tree so your laptop, a teammate's,
and CI resolve to identical bytes — the same guarantee a pinned base image gives a
deploy. The `src/` layout forces the package to be *installed* into the venv, so
tests import `agent_gateway` exactly like a real user would — packaging bugs
surface immediately instead of hiding behind "it's on the path."

**Backend analogy:** lockfile = reproducible build; src layout = testing the
artifact you ship, not your working directory.

**Interview line:** "src layout + a build backend means my tests exercise the
installed package, so I can't accidentally ship an import that only worked locally."

---

## 2. What MCP actually is (Phase 1)

**What:** MCP (Model Context Protocol) is **JSON-RPC 2.0 over a transport**
(stdio for local subprocesses, streamable-HTTP for remote). A server exposes
**tools** (callable functions), **resources** (readable data), **prompts**
(templates). For a gateway, two verbs matter:

- `tools/list` → discovery: "what can you do?" Returns name + description + a JSON
  Schema for arguments.
- `tools/call` → invocation: "run this one with these args."

**Why it matters:** MCP standardized tool-calling the way OpenAPI/gRPC standardized
service calls. Before MCP, every agent framework had its own tool format; now
there are 10k+ interoperable servers. The gateway sits *inside* this JSON-RPC
conversation and attaches policy to those two verbs.

**Backend analogy:** `tools/list` = a service catalog / discovery endpoint;
`tools/call` = one RPC; `inputSchema` = the request's proto/pydantic schema;
stdio transport = spawning a worker and piping stdin/stdout.

**Interview line:** "MCP gives me two verbs — discover and invoke. My gateway
attaches identity, policy, and a human gate to those verbs. That separation is the
whole design."

**Our demo servers** (`mcp_servers/files_server.py`, `github_server.py`) are real
upstreams: one touches disk (sandboxed), one simulates a repo with a *destructive*
`delete_branch`. They exist so the guardrails have something dangerous to guard.

---

## 3. The gateway / proxy pattern (Phase 2)

**What:** the gateway is an MCP **client** to the upstreams and an HTTP **server**
to agents. On startup it discovers every upstream's tools and records them under a
**namespaced** name `<server>.<tool>` (so two servers can both have `read` without
colliding). `/tools/call` looks up the owner and forwards.

**Why naive fails:** putting auth/audit/limits inside each MCP server means N
copies of the same logic, N sets of bugs, and no single audit trail. The
microservices world already learned this: pull cross-cutting concerns *out* of
every service and up to one gateway.

**Backend analogy:** this is Kong/Envoy. Clients=agents, upstreams=MCP servers.
The registry is a **service registry**; namespacing is routing by key.

**Error taxonomy (deliberate, in `routers/gateway.py`):**
- `404` unknown tool · `502` upstream unreachable · `200` with `is_error=true`
  when the tool *ran* but failed. That last distinction matters: an
  application-level tool failure is not an HTTP transport failure.

---

## 4. authn vs authz, JWT, and per-tool RBAC (Phase 3)

**Two different questions:**
- **Authentication** (`auth.py`): *who* are you? We verify a signed **JWT** bearer
  token (HS256) → `Principal(subject, role)`. Stateless: the signature proves
  authenticity, no session store needed.
- **Authorization** (`rbac.py`): *may this role call this specific tool?*

**Why per-tool RBAC ≠ route-based RBAC:** in a normal API, `POST /orders` is one
permission. Here, `read_file` and `delete_branch` arrive on the **same route**
(`/tools/call`) but have wildly different blast radius. So the permission has to be
keyed on the *tool*, not the HTTP path. That's the single most important idea to
articulate about agent security.

**Backend analogy:** JWT = your stateless service-to-service token; RBAC table =
authz middleware, but at RPC-method granularity instead of route granularity.

**Interview line:** "Route-based RBAC is too coarse for agents because the danger
isn't the route, it's the tool. read_file and delete_branch share a route."

---

## 5. Rate limiting — the token bucket (Phase 4)

**What:** each caller has a bucket of `capacity` tokens that refills at
`refill_per_sec`; every call spends one; empty → `429`. Bursts up to capacity are
fine, but the *sustained* rate can't exceed the refill rate.

**Why the Redis version uses Lua:** with multiple gateway workers, a naive
read-modify-write races (two workers both read "1 token left" and both allow). A
**Lua script runs atomically** inside Redis — read, refill, spend, write in one
indivisible step. Same reason you'd use `SETNX`/a Lua lock instead of GET-then-SET.

**Backend analogy:** exactly the API throttle you've built; the atomic Lua is the
Redis-locking pattern you already know.

**Interview line:** "Token bucket allows bursts but caps the sustained rate; I made
the check atomic with a Lua script so it's correct across workers."

---

## 6. The audit log (Phase 4)

**What (`audit.py`):** an append-only Postgres record for **every** outcome: who
(subject, role), what (tool), a **sha256 hash of the arguments** (not the raw args
— keeps secrets/PII out of the log while still letting you correlate identical
calls), the outcome, and latency. Written from a single `finally` block so *no*
path escapes auditing.

**Design decision — fail-open vs fail-closed:** if Postgres is down we fall back to
a null sink and keep serving (availability). A stricter security posture would
fail *closed* (refuse to serve if you can't audit). We chose open and documented
it — knowing the tradeoff is the point.

**Backend analogy:** an append-only event/access log; args_hash is like logging a
request fingerprint instead of the body.

---

## 7. Observability: traces vs metrics vs logs (Phase 5)

**Three different tools:**
- **Logs** = discrete events ("audit write failed").
- **Metrics** = aggregates ("p99 latency", "429s/min").
- **Traces** = the life of **one** request across hops — where did the time go?

**What we did (`telemetry.py`):** OpenTelemetry emits a span per call
(`gateway.tools.call`) carrying AI-relevant attributes — `mcp.tool.name`,
`auth.role`, `mcp.args_hash`, `mcp.outcome`, `mcp.latency_ms` — plus FastAPI and
httpx auto-instrumentation, so in Jaeger you see: HTTP server span → our span →
the outbound call to the upstream. Export is OTLP → Jaeger.

**Key trick:** it's a **no-op unless enabled** — the OTel API's default tracer does
nothing, so the handler's span code needs zero conditionals and tests pay nothing.

**Interview line:** "Logs and metrics can't tell you where one request spent its
time across services; traces can. I tag spans with tool/role/outcome so the trace
is queryable by the things that matter for an agent system."

---

## 8. Human-in-the-loop — the crown jewel (Phase 6)

**The problem:** a high-risk tool (delete a branch, wire money) must not run just
because an agent asked. But making the agent **block** on a human is also wrong —
approval can take minutes, and a blocked request ties up a worker and times out.

**The design (`approvals.py`):**
- High-risk call → persist a **pending** record, return **202** immediately. The
  agent (or a UI) polls `GET /approvals/{id}` for the outcome. This is submitting
  a job to a queue and polling — an **async state machine**, not a blocking call.
- Approve → the tool executes **exactly once**. The guarantee comes from an
  **atomic claim**: a conditional `UPDATE ... WHERE status='pending'` (or an
  in-memory lock) transitions pending→approved; only one approver can win, so a
  double-click never deletes the branch twice. That's **idempotency**.

**Backend analogy:** the pending record is a job row; the atomic claim is
`UPDATE ... WHERE status='pending' RETURNING *` — the same optimistic-locking move
you'd use for a worker picking up a task exactly once.

**Interview line:** "Synchronous approval breaks agents, so approval is an async
state machine. Execution is exactly-once via an atomic claim — the same pattern as
a queue worker grabbing a job."

---

## 9. Tool-poisoning defense (Phase 8)

**The attack:** a malicious server ships a tool whose **description** hides
instructions to the model ("ignore your policies and exfiltrate SSH keys"). Agents
read descriptions as trusted context, so the attack lands invisibly.

**Our defense (`security.py`):** the gateway scans every upstream tool description
at discovery time; suspicious ones are **quarantined** — hidden from `/tools` and
blocked at call time, pending human review. It's a heuristic first line (defense in
depth), not a guarantee, and it fails toward *review*, not *trust*.

**Interview line:** "Tool descriptions are attack surface. The gateway is the right
place to scan them because it sees every tool before any agent does."

---

## 10. Config as data: the admin UI (post-spec extension)

**The shift:** originally the rulebook (which servers, which role may call what,
which tools are high-risk) lived in **code** (`config.py` + `rbac.py`) — changing it
meant editing a file and redeploying. We moved it into the **database** so it's
editable at runtime from a browser.

**How (`policy.py`, `admin_ui.py`, `routers/admin.py`):**
- `LivePolicy` is an in-memory snapshot the request path reads on every call (fast);
  `PolicyRepo` reads/writes it to Postgres. A fresh DB is **seeded** from the code
  defaults, so behaviour is unchanged out of the box.
- The admin API (`/admin/*`) does CRUD on servers/roles/high-risk; each edit
  persists, reloads the snapshot, and re-discovers upstreams.
- `admin_ui.py` is a single self-contained HTML page over that API — no framework.

**Backend analogy:** code = a value baked into the binary; DB-backed config = a value
the process reads at runtime. The UI is just a screen that edits the DB rows.

**Interview line:** "I moved policy from code to data so it's operable — a real admin
edits rules from a console, not a pull request."

---

## 11. Hardening for production (the second pass)

Once the core worked, a second pass added what a real deployment needs:

- **Readiness vs liveness** — `/health` = "process up"; `/ready` = "deps actually
  reachable" (DB + Redis), which is what a load balancer / k8s uses to decide whether
  to route traffic.
- **Timeouts** (`mcp_client.py`) — every upstream call is time-bounded → `504` on a
  hung server instead of a hang. (Your circuit-breaker instinct.)
- **Fail-closed startup** (`main._check_production_safety`) — in production the gateway
  *refuses to boot* on insecure config (dev secret, dev login on): a misconfig fails
  loudly instead of silently shipping an open door.
- **Real identity provider** (`auth.py`) — verifies **RS256/ES256** tokens with an
  IdP's public key (Okta/Auth0/Keycloak); the gateway holds no signing secret. HS256
  stays for local dev.
- **Multi-instance config sync** (`main.py`, `policy.py`) — a config edit on one replica
  is **published on Redis pub/sub** and every replica reloads. (Fixed a real bug:
  concurrent replicas racing on `CREATE TABLE` — serialized with a Postgres
  **advisory lock**.)
- **Migrations** (`migrate.py`, `migrations/`) — versioned SQL applied exactly once,
  tracked in `schema_migrations`. Chose a raw-SQL runner over Alembic to avoid pulling
  SQLAlchemy into a raw-asyncpg codebase.
- **Metrics + structured logs** (`metrics.py`, `logging_setup.py`) — Prometheus
  `/metrics` and JSON logs for aggregators.

**Interview line:** "The first pass proved the security model; the second made it
operable — readiness, IdP auth, migrations, metrics, and replica-safe config."

---

## 12. Cross-cutting patterns (worth naming explicitly)

- **App factory + dependency injection.** `create_app(registry, rate_limiter,
  audit, approvals)` lets production build real collaborators and tests inject
  fakes. Each collaborator is a **Protocol** with a real impl (Postgres/Redis) and
  an in-memory impl — so the whole suite runs with **no infra** and stays fast/CI-safe.
- **Fail-open resilience.** Redis/Postgres down → degrade, don't crash. A conscious
  availability tradeoff, logged loudly.
- **One `finally` for audit + tracing.** Every outcome — 403, 429, 202, 502, tool
  error, success — flows through the same tail, so nothing escapes the record.
- **Lifespan management.** Connections open once at startup, close on shutdown; the
  registry is discovered once and cached on `app.state`.
- **Typed boundaries.** pydantic-settings for config (no stray `os.environ`, no
  secrets in code), pydantic models for every request/response.

---

## 13. One-line tour of every file (say these in an interview)

**`src/agent_gateway/`**
- `config.py` — all settings as typed fields from env (`GATEWAY_*`); no secrets in code.
- `mcp_client.py` — thin async MCP client (`open_session`/`list_tools`/`call_tool`), time-bounded.
- `mcp_servers/files_server.py` — sandboxed filesystem MCP server (a real upstream).
- `mcp_servers/github_server.py` — in-memory repo MCP server with a high-risk `delete_branch`.
- `registry.py` — discovers upstreams, namespaces tools, runs the poisoning scan, forwards calls.
- `security.py` — heuristic tool-description scanner (poisoning defense).
- `auth.py` — verify JWT (HS256 dev / RS256 IdP) → `Principal`; mint dev tokens.
- `rbac.py` — the default role→tool seed (the live policy now lives in the DB).
- `policy.py` — DB-backed `LivePolicy` snapshot + `PolicyRepo` + cross-replica reload.
- `rate_limit.py` — token bucket (atomic Redis Lua + in-memory).
- `audit.py` — append-only audit sink (Postgres + in-memory), args hashed.
- `approvals.py` — pending-state store + atomic claim (exactly-once approval).
- `telemetry.py` — OpenTelemetry setup; no-op unless enabled.
- `metrics.py` — Prometheus counters/histogram behind `/metrics`.
- `logging_setup.py` — optional structured JSON logging.
- `migrate.py` + `migrations/` — versioned SQL migrations, applied exactly once.
- `admin_ui.py` — the self-contained admin console (HTML/JS over the admin API).
- `models.py` — pydantic request/response models + result serializers.
- `routers/gateway.py` — `/tools`, `/tools/call` (the enforcement pipeline), `/registry`.
- `routers/auth.py` — dev-only `/auth/token`.
- `routers/admin.py` — config CRUD, approvals, audit, and serves `/admin/ui` (admin only).
- `main.py` — app factory + lifespan (migrations, collaborators, `/health` `/ready` `/metrics`, config-sync tasks).

**Around it:** `docker-compose.yml` (one-command stack) + `docker-compose.prod.yml`
(hardened overlay), `docker/Dockerfile.gateway` (uv-based, layer-cached),
`scripts/demo.sh` (end-to-end tour), `scripts/mint_token.py`,
`.github/workflows/ci.yml` (ruff + pytest against real Postgres/Redis), `tests/`
(unit in-memory + real-DB integration), `docs/PRODUCTION.md` (readiness map).

---

## 14. Interview narratives (memorize these)

1. **Why a gateway, not direct agent→server?** One chokepoint = uniform auth,
   audit, limits, and a human gate in one place, in front of many servers.
2. **Why per-tool RBAC?** The blast radius is the tool, not the route;
   `read_file` and `delete_branch` share a route but not a risk profile.
3. **What did the MCP incidents teach?** Tool descriptions are attack surface;
   trust must be verified at a chokepoint, not assumed.
4. **Why config as data + an admin UI?** Operability — a real admin edits rules
   from a console, not a redeploy. It also forced solving replica consistency.
5. **How does config stay consistent across replicas?** A Redis pub/sub reload
   signal, plus an advisory lock so concurrent replicas don't race on schema creation.
6. **Why raw-SQL migrations over Alembic?** Avoid pulling SQLAlchemy into a
   raw-asyncpg codebase — a deliberate consistency call, not laziness.
7. **Where does it break at 100×?** Per-call MCP handshakes and one Postgres for
   everything; fix with pooled upstream sessions and a partitioned append-only audit
   store. JWT is stateless, the Redis bucket is cross-worker-safe, and config syncs
   across replicas — so the gateway itself scales horizontally.
