"""Phase 2: the proxy surface (/tools, /tools/call) via a fake registry.

These use an admin token (all tools allowed) so they isolate proxy/error-mapping
behaviour; RBAC-specific behaviour is covered in test_auth_rbac.py.
"""

from __future__ import annotations

from conftest import FakeRegistry, FakeResult, auth_header, make_tool
from fastapi.testclient import TestClient

from agent_gateway.main import create_app


def _client(tools, results=None) -> TestClient:
    return TestClient(create_app(registry=FakeRegistry(tools, results)))


def test_list_tools_is_namespaced():
    tools = [make_tool("files.read_file", "files", "read_file", read_only=True)]
    with _client(tools) as c:
        resp = c.get("/tools", headers=auth_header("admin"))
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["name"] == "files.read_file"
        assert body[0]["server"] == "files"
        assert body[0]["read_only"] is True


def test_call_tool_success():
    tools = [make_tool("files.read_file", "files", "read_file")]
    results = {"files.read_file": FakeResult(text="file contents")}
    with _client(tools, results) as c:
        resp = c.post(
            "/tools/call",
            json={"name": "files.read_file", "arguments": {"path": "a"}},
            headers=auth_header("admin"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_error"] is False
        assert body["content"][0]["text"] == "file contents"


def test_unknown_tool_is_404():
    with _client([]) as c:
        resp = c.post(
            "/tools/call",
            json={"name": "nope.tool", "arguments": {}},
            headers=auth_header("admin"),
        )
        assert resp.status_code == 404


def test_tool_error_propagates_as_200_with_is_error():
    tools = [make_tool("github.delete_branch", "github", "delete_branch", destructive=True)]
    results = {"github.delete_branch": FakeResult(is_error=True, text="refused")}
    with _client(tools, results) as c:
        resp = c.post(
            "/tools/call",
            json={"name": "github.delete_branch", "arguments": {"name": "main"}},
            headers=auth_header("admin"),
        )
        assert resp.status_code == 200
        assert resp.json()["is_error"] is True


def test_upstream_failure_is_502():
    tools = [make_tool("files.boom", "files", "boom")]
    results = {"files.boom": RuntimeError("connection refused")}
    with _client(tools, results) as c:
        resp = c.post(
            "/tools/call",
            json={"name": "files.boom", "arguments": {}},
            headers=auth_header("admin"),
        )
        assert resp.status_code == 502
