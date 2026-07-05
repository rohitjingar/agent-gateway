# Production readiness

An honest map of what's production-grade **in this repo** vs. what must be wired to
**your environment**. "Production ready" is a spectrum; this is where the project sits
and how to close the remaining gaps.

## ✅ Done in-repo

| Area | What's here | Where |
|---|---|---|
| Auth (dev) | JWT HS256 + dev token endpoint | `auth.py`, `routers/auth.py` |
| Auth (prod) | RS256/ES256 verification with an IdP public key (Okta/Auth0/Keycloak) | `auth.py` (`jwt_public_key`, `jwt_audience`) |
| Fail-closed startup | Refuses to boot on insecure prod config (dev secret / dev_auth) | `main._check_production_safety` |
| Per-tool RBAC | Role→tool policy, DB-backed, UI-editable | `policy.py`, `routers/admin.py` |
| Rate limiting | Redis token bucket (atomic Lua), cross-worker safe | `rate_limit.py` |
| Audit log | Append-only Postgres, args hashed | `audit.py` |
| Human approval | Async pending queue, exactly-once execution | `approvals.py` |
| Tracing | OpenTelemetry → OTLP → Jaeger | `telemetry.py` |
| Metrics | Prometheus `/metrics` (calls, latency, approvals) | `metrics.py` |
| Structured logs | JSON logs via `GATEWAY_LOG_FORMAT=json` | `logging_setup.py` |
| Readiness | `/ready` checks DB + Redis; `/health` liveness | `main.py` |
| Timeouts | Every upstream MCP call is time-bounded (504 on hang) | `mcp_client.py`, `registry.py` |
| Migrations | Versioned SQL, exactly-once, `python -m agent_gateway.migrate` | `migrate.py`, `migrations/` |
| Horizontal scale | Stateless JWT + shared Redis bucket + **config sync across replicas** (Redis pub/sub) | `main.py`, `policy.py` |
| Poisoning defense | Tool-description scan + quarantine | `security.py` |
| CI | Ruff + pytest incl. real Postgres/Redis integration tests | `.github/workflows/ci.yml` |

## 🔌 Needs YOUR environment (hooks are in place; wire them up)

- **TLS / HTTPS** — terminate TLS at a reverse proxy (nginx/Caddy/ALB) or ingress in
  front of the gateway. The app speaks plain HTTP behind it.
- **Real identity provider** — set `GATEWAY_JWT_ALGORITHM=RS256`,
  `GATEWAY_JWT_PUBLIC_KEY=<PEM>` (or wire a JWKS fetch), `GATEWAY_JWT_ISSUER=<your issuer>`,
  `GATEWAY_JWT_AUDIENCE=<your api>`, and `GATEWAY_DEV_AUTH=false`. Your IdP mints tokens;
  the gateway only verifies. (Claim→role mapping may need adjusting to your IdP's claims.)
- **Secrets management** — inject `GATEWAY_JWT_SECRET`, DB, and Redis creds from a
  secrets manager (Vault, AWS/GCP Secrets Manager) — never commit them.
- **Kubernetes** — use `/health` (liveness) and `/ready` (readiness) probes; run
  migrations as an init container/Job (`python -m agent_gateway.migrate`); scale the
  gateway Deployment (config sync already keeps replicas consistent).
- **Managed Postgres/Redis** — point `GATEWAY_DATABASE_URL` / `GATEWAY_REDIS_URL` at
  managed instances; keep audit retention/partitioning in mind at high volume.

## Turning on production mode

```bash
GATEWAY_ENV=production            # enables fail-closed startup checks
GATEWAY_DEV_AUTH=false            # disable the dev token endpoint
GATEWAY_JWT_ALGORITHM=RS256       # verify IdP tokens (or a strong HS256 secret)
GATEWAY_JWT_PUBLIC_KEY=<PEM>
GATEWAY_JWT_ISSUER=<idp-issuer>
GATEWAY_JWT_AUDIENCE=<api-audience>
GATEWAY_OTEL_ENABLED=true
GATEWAY_LOG_FORMAT=json
GATEWAY_DATABASE_URL=<managed-postgres>
GATEWAY_REDIS_URL=<managed-redis>
# run migrations once before/with rollout:
#   python -m agent_gateway.migrate
```

With `GATEWAY_ENV=production`, the gateway **refuses to start** if the JWT secret is the
insecure default or the dev token endpoint is still on — so a misconfigured deploy fails
loudly instead of silently shipping an open door.

## Known limitations (deliberate, documented)

- **Per-call upstream MCP sessions** — correctness over throughput; a pooled persistent
  session is the next optimization for high call volume.
- **Fail-open on infra loss** — if Redis/Postgres are down, rate limiting/audit degrade
  rather than blocking traffic. Flip to fail-closed if audit is a hard compliance control.
- **Poisoning scan is heuristic** — a first line of defense (quarantine for review), not a
  guarantee.
- **Audit is a single table** — fine to moderate volume; partition or offload to an
  append-only store (Kafka/ClickHouse) at very high volume.
