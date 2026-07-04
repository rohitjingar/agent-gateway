"""Phase 3: authentication (JWT) and per-tool authorization (RBAC)."""

from __future__ import annotations

from conftest import DEMO_TOOLS, FakeRegistry, FakeResult, auth_header
from fastapi.testclient import TestClient

from agent_gateway.main import create_app


def _client(results=None) -> TestClient:
    return TestClient(create_app(registry=FakeRegistry(DEMO_TOOLS, results)))


def test_missing_token_is_401():
    with _client() as c:
        assert c.get("/tools").status_code == 401
        assert (
            c.post("/tools/call", json={"name": "files.read_file", "arguments": {}}).status_code
            == 401
        )


def test_readonly_sees_only_readonly_tools():
    with _client() as c:
        resp = c.get("/tools", headers=auth_header("readonly"))
        names = {t["name"] for t in resp.json()}
        assert names == {"files.read_file", "files.list_dir", "github.list_branches"}


def test_readonly_cannot_call_write_403():
    with _client({"files.write_file": FakeResult()}) as c:
        resp = c.post(
            "/tools/call",
            json={"name": "files.write_file", "arguments": {"path": "a", "content": "b"}},
            headers=auth_header("readonly"),
        )
        assert resp.status_code == 403


def test_developer_can_call_write_200():
    with _client({"files.write_file": FakeResult(text="wrote")}) as c:
        resp = c.post(
            "/tools/call",
            json={"name": "files.write_file", "arguments": {"path": "a", "content": "b"}},
            headers=auth_header("developer"),
        )
        assert resp.status_code == 200


def test_admin_sees_all_tools():
    with _client() as c:
        resp = c.get("/tools", headers=auth_header("admin"))
        assert len(resp.json()) == len(DEMO_TOOLS)


def test_dev_token_endpoint_mints_usable_token():
    with _client() as c:
        minted = c.post("/auth/token", json={"subject": "alice", "role": "developer"})
        assert minted.status_code == 200
        token = minted.json()["access_token"]
        resp = c.get("/tools", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


def test_unknown_role_rejected_at_mint():
    with _client() as c:
        resp = c.post("/auth/token", json={"subject": "x", "role": "wizard"})
        assert resp.status_code == 400
