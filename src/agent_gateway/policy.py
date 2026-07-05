"""Database-backed, UI-editable policy: which servers exist, what each role may
call, and which tools are high-risk.

Today these rules live in code (config.py + rbac.py). This module moves them onto
a "clipboard in the database" so they can be edited from the admin UI with no code
change. Point DATABASE_URL at your own Postgres and all of this config lives in
YOUR database, seeded once from the code defaults.

Shape:
- LivePolicy: an in-memory snapshot the request path reads on every call (fast).
- PolicyRepo: reads/writes the snapshot to Postgres.
- After any edit, the app reloads the snapshot from the DB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent_gateway.config import SCHEMA_LOCK_KEY, Upstream

log = logging.getLogger(__name__)


def _match(pattern: str, tool: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*") and tool.startswith(pattern[:-1]):
        return True
    return pattern == tool


@dataclass
class LivePolicy:
    """In-memory snapshot of the rulebook. The gateway reads this per request."""

    servers: list[Upstream] = field(default_factory=list)
    role_patterns: dict[str, list[str]] = field(default_factory=dict)
    high_risk: set[str] = field(default_factory=set)

    def is_allowed(self, role: str, tool: str) -> bool:
        return any(_match(p, tool) for p in self.role_patterns.get(role, []))

    def allowed_tools(self, role: str, tool_names: list[str]) -> list[str]:
        return [n for n in tool_names if self.is_allowed(role, n)]

    def is_tool_high_risk(self, tool: str) -> bool:
        return tool in self.high_risk

    def roles(self) -> list[str]:
        return sorted(self.role_patterns)


def default_policy(settings) -> LivePolicy:
    """The seed: today's code defaults (config.py upstreams, rbac roles, high-risk)."""
    from agent_gateway.rbac import ROLE_POLICY

    return LivePolicy(
        servers=list(settings.upstreams),
        role_patterns={role: list(patterns) for role, patterns in ROLE_POLICY.items()},
        high_risk=set(settings.high_risk_tools),
    )


_DDL = """
CREATE TABLE IF NOT EXISTS servers (
    name TEXT PRIMARY KEY, url TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS role_policies (
    role TEXT NOT NULL, pattern TEXT NOT NULL, PRIMARY KEY (role, pattern)
);
CREATE TABLE IF NOT EXISTS tool_risk (
    tool_name TEXT PRIMARY KEY, high_risk BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS policy_meta (k TEXT PRIMARY KEY, v TEXT);
"""


class PolicyRepo:
    def __init__(self, pool) -> None:
        self._pool = pool

    @classmethod
    async def create(cls, pool, seed: LivePolicy) -> PolicyRepo:
        """Create tables and, on a fresh DB, seed the rulebook from code defaults."""
        async with pool.acquire() as con, con.transaction():
            # serialize DDL across replicas (concurrent CREATE TABLE IF NOT EXISTS can race)
            await con.execute("SELECT pg_advisory_xact_lock($1)", SCHEMA_LOCK_KEY)
            await con.execute(_DDL)
            already = await con.fetchval("SELECT v FROM policy_meta WHERE k = 'seeded'")
            if not already:
                for s in seed.servers:
                    await con.execute(
                        "INSERT INTO servers(name, url) VALUES($1, $2)"
                        " ON CONFLICT (name) DO NOTHING",
                        s.name,
                        s.url,
                    )
                for role, patterns in seed.role_patterns.items():
                    for pattern in patterns:
                        await con.execute(
                            "INSERT INTO role_policies(role, pattern) VALUES($1, $2)"
                            " ON CONFLICT DO NOTHING",
                            role,
                            pattern,
                        )
                for tool in seed.high_risk:
                    await con.execute(
                        "INSERT INTO tool_risk(tool_name) VALUES($1)"
                        " ON CONFLICT (tool_name) DO NOTHING",
                        tool,
                    )
                await con.execute(
                    "INSERT INTO policy_meta(k, v) VALUES('seeded', '1') ON CONFLICT (k) DO NOTHING"
                )
        return cls(pool)

    async def ping(self) -> None:
        await self._pool.execute("SELECT 1")

    async def load(self) -> LivePolicy:
        servers = await self._pool.fetch(
            "SELECT name, url FROM servers WHERE enabled ORDER BY name"
        )
        roles = await self._pool.fetch("SELECT role, pattern FROM role_policies")
        risks = await self._pool.fetch("SELECT tool_name FROM tool_risk WHERE high_risk")
        role_patterns: dict[str, list[str]] = {}
        for r in roles:
            role_patterns.setdefault(r["role"], []).append(r["pattern"])
        return LivePolicy(
            servers=[Upstream(name=s["name"], url=s["url"]) for s in servers],
            role_patterns=role_patterns,
            high_risk={r["tool_name"] for r in risks},
        )

    async def add_server(self, name: str, url: str) -> None:
        await self._pool.execute(
            "INSERT INTO servers(name, url, enabled) VALUES($1, $2, true)"
            " ON CONFLICT (name) DO UPDATE SET url = $2, enabled = true",
            name,
            url,
        )

    async def remove_server(self, name: str) -> None:
        await self._pool.execute("DELETE FROM servers WHERE name = $1", name)

    async def set_role(self, role: str, patterns: list[str]) -> None:
        async with self._pool.acquire() as con, con.transaction():
            await con.execute("DELETE FROM role_policies WHERE role = $1", role)
            for pattern in patterns:
                await con.execute(
                    "INSERT INTO role_policies(role, pattern) VALUES($1, $2)", role, pattern
                )

    async def remove_role(self, role: str) -> None:
        await self._pool.execute("DELETE FROM role_policies WHERE role = $1", role)

    async def set_high_risk(self, tool: str, high: bool) -> None:
        if high:
            await self._pool.execute(
                "INSERT INTO tool_risk(tool_name, high_risk) VALUES($1, true)"
                " ON CONFLICT (tool_name) DO UPDATE SET high_risk = true",
                tool,
            )
        else:
            await self._pool.execute("DELETE FROM tool_risk WHERE tool_name = $1", tool)


async def reload_into(app) -> None:
    """Reload the policy snapshot from the DB and re-discover servers. Shared by
    the admin edit path and the multi-instance sync tasks (no HTTP context)."""
    repo = getattr(app.state, "policy_repo", None)
    if repo is None:
        return
    app.state.policy = await repo.load()
    reg = app.state.registry
    reg.set_upstreams(app.state.policy.servers)
    await reg.refresh()


async def build_policy(settings) -> tuple[LivePolicy, PolicyRepo | None, object | None]:
    """Load the rulebook from Postgres (seeding on first run). If the DB is down,
    fall back to read-only code defaults so the gateway still serves."""
    seed = default_policy(settings)
    try:
        import asyncpg

        pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=3)
        repo = await PolicyRepo.create(pool, seed)
        policy = await repo.load()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Postgres unavailable (%s); policy = read-only defaults (UI edits won't persist)", exc
        )
        return seed, None, None
    return policy, repo, pool
