"""Ops probes: /ready reflects dependency health."""

from __future__ import annotations

from conftest import DEMO_TOOLS, FakeRegistry
from fastapi.testclient import TestClient

from agent_gateway.approvals import InMemoryApprovalStore
from agent_gateway.audit import InMemoryAuditSink
from agent_gateway.main import create_app
from agent_gateway.rate_limit import NullRateLimiter


def test_ready_is_200_when_optional_deps_absent():
    app = create_app(
        registry=FakeRegistry(DEMO_TOOLS),
        rate_limiter=NullRateLimiter(),
        audit=InMemoryAuditSink(),
        approvals=InMemoryApprovalStore(),
    )
    with TestClient(app) as c:
        r = c.get("/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True
        assert body["checks"]["database"] == "not_configured"
        assert body["checks"]["redis"] == "not_configured"
