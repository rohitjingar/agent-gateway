"""Lightweight, dependency-free SQL migration runner.

Versioned ``.sql`` files in ``agent_gateway/migrations/`` are applied in filename
order, each recorded in a ``schema_migrations`` table so it runs exactly once.
Apply them explicitly for production:

    uv run python -m agent_gateway.migrate

The app also applies pending migrations on startup, so `docker compose up` needs
no separate step. We use raw asyncpg + SQL rather than Alembic/SQLAlchemy to stay
consistent with the rest of the codebase — no ORM is pulled in for one concern.
"""

from __future__ import annotations

import asyncio
import logging
from importlib.resources import files

from agent_gateway.config import SCHEMA_LOCK_KEY, get_settings

log = logging.getLogger(__name__)


def _migration_sqls() -> list[tuple[str, str]]:
    """(version, sql) pairs, sorted by version (filename)."""
    entries = files("agent_gateway.migrations").iterdir()
    return [
        (e.name, e.read_text(encoding="utf-8"))
        for e in sorted(entries, key=lambda p: p.name)
        if e.name.endswith(".sql")
    ]


async def run_migrations(database_url: str) -> list[str]:
    """Apply pending migrations under an advisory lock. Returns versions applied."""
    import asyncpg

    conn = await asyncpg.connect(database_url)
    applied: list[str] = []
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", SCHEMA_LOCK_KEY)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        for version, sql in _migration_sqls():
            if version in done:
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations(version) VALUES($1)", version)
            applied.append(version)
            log.info("applied migration %s", version)
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", SCHEMA_LOCK_KEY)
        await conn.close()
    return applied


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    applied = asyncio.run(run_migrations(get_settings().database_url))
    print(f"migrations applied: {applied or 'none (already up to date)'}")


if __name__ == "__main__":
    main()
