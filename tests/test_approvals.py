"""Phase 6: human-in-the-loop approval lane."""

from __future__ import annotations

from conftest import DEMO_TOOLS, FakeRegistry, FakeResult, auth_header
from fastapi.testclient import TestClient

from agent_gateway.approvals import InMemoryApprovalStore
from agent_gateway.audit import InMemoryAuditSink
from agent_gateway.main import create_app
from agent_gateway.rate_limit import NullRateLimiter

HIGH_RISK = {"name": "github.delete_branch", "arguments": {"name": "develop"}}


def _build(results=None):
    reg = FakeRegistry(DEMO_TOOLS, results)
    audit = InMemoryAuditSink()
    store = InMemoryApprovalStore()
    app = create_app(registry=reg, rate_limiter=NullRateLimiter(), audit=audit, approvals=store)
    return app, reg, audit


def test_non_high_risk_executes_directly():
    app, reg, _ = _build({"files.read_file": FakeResult(text="hi")})
    with TestClient(app) as c:
        r = c.post(
            "/tools/call",
            json={"name": "files.read_file", "arguments": {"path": "a"}},
            headers=auth_header("readonly"),
        )
        assert r.status_code == 200
        assert r.json()["is_error"] is False
    assert reg.calls == [("files.read_file", {"path": "a"})]


def test_high_risk_tool_is_queued_not_executed():
    app, reg, audit = _build({"github.delete_branch": FakeResult(text="deleted")})
    with TestClient(app) as c:
        r = c.post("/tools/call", json=HIGH_RISK, headers=auth_header("developer"))
        assert r.status_code == 202
        assert r.json()["status"] == "pending_approval"
        assert r.json()["approval_id"]
    assert reg.calls == []  # tool did NOT run
    assert audit.rows[-1].outcome == "pending_approval"


def test_approve_executes_exactly_once_and_records_result():
    app, reg, _ = _build({"github.delete_branch": FakeResult(text="deleted branch develop")})
    with TestClient(app) as c:
        aid = c.post("/tools/call", json=HIGH_RISK, headers=auth_header("developer")).json()[
            "approval_id"
        ]
        first = c.post(f"/approvals/{aid}/approve", headers=auth_header("admin"))
        assert first.status_code == 200
        assert first.json()["status"] == "approved"
        assert first.json()["outcome"] == "ok"
        assert "deleted branch develop" in str(first.json()["result"])
        # idempotent: approving again must NOT execute the tool a second time
        again = c.post(f"/approvals/{aid}/approve", headers=auth_header("admin"))
        assert again.status_code == 200
        assert again.json()["status"] == "approved"
    assert reg.calls == [("github.delete_branch", {"name": "develop"})]


def test_deny_never_executes():
    app, reg, _ = _build({"github.delete_branch": FakeResult(text="deleted")})
    with TestClient(app) as c:
        aid = c.post("/tools/call", json=HIGH_RISK, headers=auth_header("developer")).json()[
            "approval_id"
        ]
        r = c.post(f"/approvals/{aid}/deny", headers=auth_header("admin"))
        assert r.status_code == 200
        assert r.json()["status"] == "denied"
    assert reg.calls == []


def test_list_and_get_pending_require_admin():
    app, _, _ = _build({"github.delete_branch": FakeResult()})
    with TestClient(app) as c:
        aid = c.post("/tools/call", json=HIGH_RISK, headers=auth_header("developer")).json()[
            "approval_id"
        ]
        assert c.get("/approvals", headers=auth_header("developer")).status_code == 403
        assert (
            c.post(f"/approvals/{aid}/approve", headers=auth_header("readonly")).status_code == 403
        )
        listing = c.get("/approvals?status=pending", headers=auth_header("admin"))
        assert listing.status_code == 200
        assert any(a["id"] == aid for a in listing.json())
        one = c.get(f"/approvals/{aid}", headers=auth_header("admin"))
        assert one.status_code == 200
        assert one.json()["tool"] == "github.delete_branch"
