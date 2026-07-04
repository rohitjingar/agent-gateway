"""Phase 7: registry discovery view + runtime refresh."""

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


def test_registry_view_shows_servers_and_risk():
    with _client() as c:
        data = c.get("/registry", headers=auth_header("readonly")).json()
        assert {s["name"] for s in data["servers"]} == {"files", "github"}
        by_name = {t["name"]: t for t in data["tools"]}
        assert by_name["github.delete_branch"]["high_risk"] is True
        assert by_name["github.delete_branch"]["allowed"] is False  # readonly can't
        assert by_name["files.read_file"]["allowed"] is True
        assert by_name["files.read_file"]["high_risk"] is False


def test_tools_endpoint_marks_high_risk():
    with _client() as c:
        by_name = {t["name"]: t for t in c.get("/tools", headers=auth_header("developer")).json()}
        assert by_name["github.delete_branch"]["high_risk"] is True
        assert by_name["files.read_file"]["high_risk"] is False


def test_registry_refresh_requires_admin():
    with _client() as c:
        assert c.post("/registry/refresh", headers=auth_header("developer")).status_code == 403
        resp = c.post("/registry/refresh", headers=auth_header("admin"))
        assert resp.status_code == 200
        assert resp.json()["tools"] == len(DEMO_TOOLS)
