"""Phase 4: rate limiting (token bucket) and audit logging."""

from __future__ import annotations

from conftest import DEMO_TOOLS, FakeRegistry, FakeResult, auth_header
from fastapi.testclient import TestClient

from agent_gateway.audit import InMemoryAuditSink
from agent_gateway.main import create_app
from agent_gateway.rate_limit import InMemoryTokenBucket, NullRateLimiter


async def test_token_bucket_bursts_then_refills():
    clock = {"t": 0.0}
    bucket = InMemoryTokenBucket(capacity=2, refill_per_sec=1.0, now=lambda: clock["t"])
    assert await bucket.allow("k") is True
    assert await bucket.allow("k") is True
    assert await bucket.allow("k") is False  # bucket empty
    clock["t"] = 1.0  # one second elapses -> +1 token
    assert await bucket.allow("k") is True
    assert await bucket.allow("k") is False


def _app(rate_limiter=None, audit=None, results=None):
    return create_app(
        registry=FakeRegistry(DEMO_TOOLS, results),
        rate_limiter=rate_limiter,
        audit=audit,
    )


def test_gateway_returns_429_when_bucket_empty():
    limiter = InMemoryTokenBucket(capacity=2, refill_per_sec=0.0)
    sink = InMemoryAuditSink()
    results = {"files.read_file": FakeResult(text="x")}
    body = {"name": "files.read_file", "arguments": {"path": "a"}}
    with TestClient(_app(limiter, sink, results)) as c:
        h = auth_header("developer")
        assert c.post("/tools/call", json=body, headers=h).status_code == 200
        assert c.post("/tools/call", json=body, headers=h).status_code == 200
        assert c.post("/tools/call", json=body, headers=h).status_code == 429
    assert [r.outcome for r in sink.rows] == ["ok", "ok", "rate_limited"]


def test_denied_call_is_audited_with_hashed_args():
    sink = InMemoryAuditSink()
    with TestClient(_app(NullRateLimiter(), sink)) as c:
        resp = c.post(
            "/tools/call",
            json={"name": "github.delete_branch", "arguments": {"name": "develop"}},
            headers=auth_header("readonly"),
        )
        assert resp.status_code == 403
    last = sink.rows[-1]
    assert last.outcome == "denied"
    assert len(last.args_hash) == 64  # sha256 hex, not raw args


def test_admin_reads_audit_but_non_admin_cannot():
    sink = InMemoryAuditSink()
    results = {"files.read_file": FakeResult(text="x")}
    with TestClient(_app(NullRateLimiter(), sink, results)) as c:
        c.post(
            "/tools/call",
            json={"name": "files.read_file", "arguments": {"path": "a"}},
            headers=auth_header("developer"),
        )
        assert c.get("/audit/recent", headers=auth_header("readonly")).status_code == 403
        resp = c.get("/audit/recent", headers=auth_header("admin"))
        assert resp.status_code == 200
        row = resp.json()[0]
        assert row["tool"] == "files.read_file"
        assert row["outcome"] == "ok"
