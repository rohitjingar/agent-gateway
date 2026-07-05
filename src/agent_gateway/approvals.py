"""Human-in-the-loop approval lane.

Why async, not blocking: a high-risk tool (delete a branch, wire money) must not
run just because an agent asked. But making the agent BLOCK on a human is also
wrong — approval can take minutes, and a blocked request ties up a worker and
times out. So we persist a *pending* record and return 202 immediately; a human
approves/denies out-of-band and the agent (or a UI) polls for the outcome. Same
shape as submitting a job to a queue and polling for its result.

Exactly-once execution: approval `claim` transitions pending->approved atomically
(a conditional UPDATE in Postgres, a lock in memory), so a double-approve can
never run the tool twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from agent_gateway.config import SCHEMA_LOCK_KEY

log = logging.getLogger(__name__)


def is_high_risk(namespaced_name: str, destructive: bool, settings) -> bool:
    """Gateway-owned risk policy. We do NOT blindly trust the upstream's own
    'destructive' hint — that's attacker-influenceable — but we can opt into it."""
    if namespaced_name in settings.high_risk_tools:
        return True
    return settings.high_risk_auto_destructive and destructive


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ApprovalRecord:
    id: str
    subject: str
    role: str
    tool: str
    arguments: dict[str, Any]
    status: str  # pending | approved | denied
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    result: dict[str, Any] | None = None
    outcome: str | None = None  # ok | tool_error | upstream_error (once executed)


class ApprovalStore(Protocol):
    async def create(
        self, *, subject: str, role: str, tool: str, arguments: dict
    ) -> ApprovalRecord: ...
    async def get(self, approval_id: str) -> ApprovalRecord | None: ...
    async def list(self, status: str | None = None, limit: int = 50) -> list[ApprovalRecord]: ...
    async def claim(self, approval_id: str, decided_by: str) -> ApprovalRecord | None: ...
    async def deny(self, approval_id: str, decided_by: str) -> ApprovalRecord | None: ...
    async def set_result(
        self, approval_id: str, result: dict, outcome: str
    ) -> ApprovalRecord | None: ...


class InMemoryApprovalStore:
    """Process-local, non-durable. Used by tests and as a fallback if Postgres is down."""

    def __init__(self) -> None:
        self._rows: dict[str, ApprovalRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, subject, role, tool, arguments):
        rec = ApprovalRecord(
            id=uuid4().hex,
            subject=subject,
            role=role,
            tool=tool,
            arguments=arguments,
            status="pending",
            created_at=_now(),
        )
        self._rows[rec.id] = rec
        return rec

    async def get(self, approval_id):
        return self._rows.get(approval_id)

    async def list(self, status=None, limit=50):
        rows = [r for r in self._rows.values() if status is None or r.status == status]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]

    async def claim(self, approval_id, decided_by):
        async with self._lock:  # atomic pending -> approved
            rec = self._rows.get(approval_id)
            if rec is None or rec.status != "pending":
                return None
            rec.status = "approved"
            rec.decided_at = _now()
            rec.decided_by = decided_by
            return rec

    async def deny(self, approval_id, decided_by):
        async with self._lock:
            rec = self._rows.get(approval_id)
            if rec is None:
                return None
            if rec.status == "pending":
                rec.status = "denied"
                rec.decided_at = _now()
                rec.decided_by = decided_by
            return rec  # idempotent: already-decided returns as-is

    async def set_result(self, approval_id, result, outcome):
        rec = self._rows.get(approval_id)
        if rec is not None:
            rec.result = result
            rec.outcome = outcome
        return rec


_DDL = """
CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject     TEXT NOT NULL,
    role        TEXT NOT NULL,
    tool        TEXT NOT NULL,
    arguments   JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    decided_at  TIMESTAMPTZ,
    decided_by  TEXT,
    result      JSONB,
    outcome     TEXT
);
"""


def _maybe_json(value):
    return json.loads(value) if isinstance(value, str) else value


def _row_to_record(row) -> ApprovalRecord:
    return ApprovalRecord(
        id=row["id"],
        subject=row["subject"],
        role=row["role"],
        tool=row["tool"],
        arguments=_maybe_json(row["arguments"]),
        status=row["status"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        result=_maybe_json(row["result"]) if row["result"] is not None else None,
        outcome=row["outcome"],
    )


class PostgresApprovalStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def create(self, *, subject, role, tool, arguments):
        row = await self._pool.fetchrow(
            "INSERT INTO approvals (id, subject, role, tool, arguments, status)"
            " VALUES ($1, $2, $3, $4, $5::jsonb, 'pending') RETURNING *",
            uuid4().hex,
            subject,
            role,
            tool,
            json.dumps(arguments),
        )
        return _row_to_record(row)

    async def get(self, approval_id):
        row = await self._pool.fetchrow("SELECT * FROM approvals WHERE id=$1", approval_id)
        return _row_to_record(row) if row else None

    async def list(self, status=None, limit=50):
        rows = await self._pool.fetch(
            "SELECT * FROM approvals WHERE ($1::text IS NULL OR status=$1)"
            " ORDER BY created_at DESC LIMIT $2",
            status,
            limit,
        )
        return [_row_to_record(r) for r in rows]

    async def claim(self, approval_id, decided_by):
        # conditional UPDATE = atomic claim; only one approver can win.
        row = await self._pool.fetchrow(
            "UPDATE approvals SET status='approved', decided_at=now(), decided_by=$2"
            " WHERE id=$1 AND status='pending' RETURNING *",
            approval_id,
            decided_by,
        )
        return _row_to_record(row) if row else None

    async def deny(self, approval_id, decided_by):
        row = await self._pool.fetchrow(
            "UPDATE approvals SET status='denied', decided_at=now(), decided_by=$2"
            " WHERE id=$1 AND status='pending' RETURNING *",
            approval_id,
            decided_by,
        )
        return _row_to_record(row) if row else await self.get(approval_id)

    async def set_result(self, approval_id, result, outcome):
        row = await self._pool.fetchrow(
            "UPDATE approvals SET result=$2::jsonb, outcome=$3 WHERE id=$1 RETURNING *",
            approval_id,
            json.dumps(result),
            outcome,
        )
        return _row_to_record(row) if row else None


async def build_approval_store(settings) -> tuple[ApprovalStore, object | None]:
    """Construct the store; fall back to in-memory if Postgres is down.

    Returns (store, pool_or_None) so the caller can close the pool.
    """
    if not settings.approval_enabled:
        return InMemoryApprovalStore(), None
    try:
        import asyncpg

        pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
        async with pool.acquire() as con, con.transaction():
            await con.execute("SELECT pg_advisory_xact_lock($1)", SCHEMA_LOCK_KEY)
            await con.execute(_DDL)
    except Exception as exc:  # noqa: BLE001
        log.warning("Postgres unavailable (%s); approvals -> in-memory store", exc)
        return InMemoryApprovalStore(), None
    return PostgresApprovalStore(pool), pool
