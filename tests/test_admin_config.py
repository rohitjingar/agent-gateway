"""Admin config surface: the UI page, auth-gating, and read-only without a DB.

(The full edit-happy-path is verified live against Postgres — these keep the
no-infra unit suite deterministic.)
"""

from __future__ import annotations

from conftest import DEMO_TOOLS, FakeRegistry, auth_header
from fastapi.testclient import TestClient

from agent_gateway.approvals import InMemoryApprovalStore
from agent_gateway.audit import InMemoryAuditSink
from agent_gateway.main import create_app
from agent_gateway.rate_limit import NullRateLimiter


def _client():
    return TestClient(
        create_app(
            registry=FakeRegistry(DEMO_TOOLS),
            rate_limiter=NullRateLimiter(),
            audit=InMemoryAuditSink(),
            approvals=InMemoryApprovalStore(),
        )
    )


def test_admin_ui_page_is_served():
    with _client() as c:
        r = c.get("/admin/ui")
        assert r.status_code == 200
        assert "Agent Gateway" in r.text


def test_config_requires_admin_and_reports_current_rules():
    with _client() as c:
        assert c.get("/admin/config", headers=auth_header("developer")).status_code == 403
        body = c.get("/admin/config", headers=auth_header("admin")).json()
        assert set(body["roles"]) == {"admin", "developer", "readonly"}
        assert "github.delete_branch" in body["high_risk"]
        assert body["editable"] is False  # no DB in the unit suite


def test_edits_are_read_only_without_a_database():
    with _client() as c:
        r = c.post(
            "/admin/servers",
            json={"name": "x", "url": "http://x/mcp"},
            headers=auth_header("admin"),
        )
        assert r.status_code == 400  # read-only: no database configured


def test_admin_role_is_protected():
    with _client() as c:
        r = c.put("/admin/roles/admin", json={"patterns": ["*"]}, headers=auth_header("admin"))
        assert r.status_code == 400  # cannot edit the admin role (lockout guard)
