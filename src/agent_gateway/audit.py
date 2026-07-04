"""Audit log: an append-only record of every tool call the gateway processed.

What we store per call: who (subject, role), what (tool), a *hash* of the
arguments (not the raw args — keeps secrets/PII out of the log while still letting
you correlate identical calls), the outcome, and latency. This is the security
control that answers "what did agent X do, and when?" after the fact.

Implementations share an interface so tests use an in-memory sink and prod uses
Postgres — the handler doesn't care which.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

log = logging.getLogger(__name__)

# All the outcomes a call can end in — one vocabulary shared by handler + log.
OUTCOMES = (
    "ok",
    "tool_error",
    "denied",
    "rate_limited",
    "unknown_tool",
    "upstream_error",
    "pending_approval",
    "quarantined",
)


def hash_args(arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass
class AuditRecord:
    subject: str
    role: str
    tool: str
    args_hash: str
    outcome: str
    latency_ms: float
    created_at: datetime


def build_record(
    subject: str,
    role: str,
    tool: str,
    arguments: dict[str, Any],
    outcome: str,
    latency_ms: float,
) -> AuditRecord:
    return AuditRecord(
        subject=subject,
        role=role,
        tool=tool,
        args_hash=hash_args(arguments),
        outcome=outcome,
        latency_ms=latency_ms,
        created_at=datetime.now(UTC),
    )


def _to_dict(rec: AuditRecord) -> dict[str, Any]:
    return {
        "subject": rec.subject,
        "role": rec.role,
        "tool": rec.tool,
        "args_hash": rec.args_hash,
        "outcome": rec.outcome,
        "latency_ms": round(rec.latency_ms, 2),
        "created_at": rec.created_at.isoformat(),
    }


class AuditSink(Protocol):
    async def record(self, rec: AuditRecord) -> None: ...
    async def recent(self, limit: int = 50) -> list[dict[str, Any]]: ...


class NullAuditSink:
    """Drops records (only logs). Used when audit is disabled or Postgres is absent."""

    async def record(self, rec: AuditRecord) -> None:
        log.info("audit(null): %s %s %s -> %s", rec.subject, rec.role, rec.tool, rec.outcome)

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return []


class InMemoryAuditSink:
    """Keeps records in a list. Deterministic — used by tests."""

    def __init__(self) -> None:
        self.rows: list[AuditRecord] = []

    async def record(self, rec: AuditRecord) -> None:
        self.rows.append(rec)

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [_to_dict(r) for r in reversed(self.rows[-limit:])]


_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject     TEXT NOT NULL,
    role        TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args_hash   TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    latency_ms  DOUBLE PRECISION NOT NULL
);
"""


class PostgresAuditSink:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def record(self, rec: AuditRecord) -> None:
        await self._pool.execute(
            "INSERT INTO audit_log"
            " (created_at, subject, role, tool, args_hash, outcome, latency_ms)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            rec.created_at,
            rec.subject,
            rec.role,
            rec.tool,
            rec.args_hash,
            rec.outcome,
            rec.latency_ms,
        )

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT subject, role, tool, args_hash, outcome, latency_ms, created_at"
            " FROM audit_log ORDER BY id DESC LIMIT $1",
            limit,
        )
        return [
            {
                "subject": r["subject"],
                "role": r["role"],
                "tool": r["tool"],
                "args_hash": r["args_hash"],
                "outcome": r["outcome"],
                "latency_ms": round(r["latency_ms"], 2),
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]


async def build_audit(settings) -> tuple[AuditSink, object | None]:
    """Construct the production sink, falling back to Null if Postgres is down.

    Returns (sink, pool_or_None) so the caller can close the pool.
    """
    if not settings.audit_enabled:
        return NullAuditSink(), None
    try:
        import asyncpg

        pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
        await pool.execute(_DDL)
    except Exception as exc:  # noqa: BLE001 - fail open, keep serving
        log.warning("Postgres unavailable (%s); audit -> null sink", exc)
        return NullAuditSink(), None
    return PostgresAuditSink(pool), pool
