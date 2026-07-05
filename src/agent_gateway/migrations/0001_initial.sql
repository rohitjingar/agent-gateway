-- Initial schema for the Agent Gateway.

-- Policy (DB-backed config, edited from the admin UI)
CREATE TABLE IF NOT EXISTS servers (
    name    TEXT PRIMARY KEY,
    url     TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS role_policies (
    role    TEXT NOT NULL,
    pattern TEXT NOT NULL,
    PRIMARY KEY (role, pattern)
);

CREATE TABLE IF NOT EXISTS tool_risk (
    tool_name TEXT PRIMARY KEY,
    high_risk BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS policy_meta (k TEXT PRIMARY KEY, v TEXT);

-- Audit log (append-only)
CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject    TEXT NOT NULL,
    role       TEXT NOT NULL,
    tool       TEXT NOT NULL,
    args_hash  TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_created_at_idx ON audit_log (created_at DESC);

-- Human-in-the-loop approval queue
CREATE TABLE IF NOT EXISTS approvals (
    id         TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject    TEXT NOT NULL,
    role       TEXT NOT NULL,
    tool       TEXT NOT NULL,
    arguments  JSONB NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    result     JSONB,
    outcome    TEXT
);
CREATE INDEX IF NOT EXISTS approvals_status_idx ON approvals (status);
