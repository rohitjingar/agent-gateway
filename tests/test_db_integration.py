"""Integration tests that exercise the REAL Postgres + Redis code paths.

Skipped automatically when no database/redis is reachable (local runs without
infra), and run in CI where service containers provide them. This covers the SQL
and Lua that the in-memory unit tests can't.
"""

from __future__ import annotations

import asyncio
import os

import pytest

DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://gateway:gateway@127.0.0.1:5432/gateway")
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/0")


def _db_available() -> bool:
    try:
        import asyncpg

        async def _chk():
            con = await asyncpg.connect(DB_URL)
            await con.execute("SELECT 1")
            await con.close()

        asyncio.run(_chk())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no Postgres reachable")


async def test_migrations_then_policy_roundtrip():
    import asyncpg

    from agent_gateway.migrate import run_migrations
    from agent_gateway.policy import PolicyRepo

    await run_migrations(DB_URL)
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    try:
        repo = PolicyRepo(pool)
        await repo.add_server("itest_srv", "http://x/mcp")
        await repo.set_role("itest_role", ["files.*"])
        pol = await repo.load()
        assert any(s.name == "itest_srv" for s in pol.servers)
        assert pol.is_allowed("itest_role", "files.read_file")
        assert not pol.is_allowed("itest_role", "github.delete_branch")
    finally:
        await repo.remove_server("itest_srv")
        await repo.remove_role("itest_role")
        await pool.close()


async def test_postgres_audit_sink_roundtrip():
    import asyncpg

    from agent_gateway.audit import PostgresAuditSink, build_record
    from agent_gateway.migrate import run_migrations

    await run_migrations(DB_URL)
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    try:
        sink = PostgresAuditSink(pool)
        await sink.record(build_record("itest", "admin", "files.read_file", {"p": 1}, "ok", 1.2))
        rows = await sink.recent(5)
        assert any(r["subject"] == "itest" and r["outcome"] == "ok" for r in rows)
        assert all(len(r["args_hash"]) == 64 for r in rows)  # hashed, not raw
    finally:
        await pool.close()


async def test_postgres_approval_exactly_once():
    import asyncpg

    from agent_gateway.approvals import PostgresApprovalStore
    from agent_gateway.migrate import run_migrations

    await run_migrations(DB_URL)
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
    try:
        store = PostgresApprovalStore(pool)
        rec = await store.create(
            subject="a", role="dev", tool="github.delete_branch", arguments={"name": "x"}
        )
        first = await store.claim(rec.id, decided_by="boss")
        second = await store.claim(rec.id, decided_by="boss2")
        assert first is not None  # first approver wins
        assert second is None  # second cannot re-claim -> exactly once
    finally:
        await pool.execute(
            "DELETE FROM approvals WHERE subject='a' AND tool='github.delete_branch'"
        )
        await pool.close()


async def test_redis_token_bucket_real():
    import redis.asyncio as redis_async

    from agent_gateway.rate_limit import RedisTokenBucket

    client = redis_async.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception:
        pytest.skip("no Redis reachable")
    try:
        await client.delete("rl:itest:bucket")
        bucket = RedisTokenBucket(client, capacity=2, refill_per_sec=0.01)
        assert await bucket.allow("itest:bucket") is True
        assert await bucket.allow("itest:bucket") is True
        assert await bucket.allow("itest:bucket") is False  # empty
    finally:
        await client.delete("rl:itest:bucket")
        await client.aclose()
