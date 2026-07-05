"""Migration files are discoverable and ordered (no DB needed)."""

from __future__ import annotations

from agent_gateway.migrate import _migration_sqls


def test_initial_migration_is_discovered_and_ordered():
    pairs = _migration_sqls()
    versions = [v for v, _ in pairs]
    assert versions == sorted(versions)  # applied in filename order
    assert any(v.startswith("0001") for v in versions)

    initial = next(sql for v, sql in pairs if v.startswith("0001"))
    for table in ("servers", "role_policies", "tool_risk", "audit_log", "approvals"):
        assert table in initial
