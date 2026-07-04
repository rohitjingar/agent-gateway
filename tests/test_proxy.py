"""Phase 2: the proxy surface (/tools, /tools/call) via a fake registry.

We inject a fake registry so these tests exercise the gateway's HTTP behaviour
and error mapping without any network. The real registry talking to real servers
is covered by the live smoke test.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_gateway.main import create_app
from agent_gateway.registry import RegisteredTool


class _FakeResult:
    def __init__(self, is_error: bool = False, text: str = "ok", structured=None):
        self.isError = is_error
        self.content = [SimpleNamespace(type="text", text=text)]
        self.structuredContent = structured


class _FakeRegistry:
    def __init__(self, tools, results):
        self._tools = {t.namespaced_name: t for t in tools}
        self._results = results

    def list(self):
        return list(self._tools.values())

    def get(self, name):
        return self._tools.get(name)

    async def call(self, name, arguments):
        result = self._results[name]
        if isinstance(result, Exception):
            raise result
        return result


def _tool(ns, server, name, *, destructive=False, read_only=False) -> RegisteredTool:
    return RegisteredTool(ns, server, name, "http://x/mcp", "desc", {}, destructive, read_only)


def _client(tools, results) -> TestClient:
    return TestClient(create_app(registry=_FakeRegistry(tools, results)))


def test_list_tools_is_namespaced():
    tools = [_tool("files.read_file", "files", "read_file", read_only=True)]
    with _client(tools, {}) as c:
        resp = c.get("/tools")
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["name"] == "files.read_file"
        assert body[0]["server"] == "files"
        assert body[0]["read_only"] is True


def test_call_tool_success():
    tools = [_tool("files.read_file", "files", "read_file")]
    results = {"files.read_file": _FakeResult(text="file contents")}
    with _client(tools, results) as c:
        resp = c.post("/tools/call", json={"name": "files.read_file", "arguments": {"path": "a"}})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_error"] is False
        assert body["content"][0]["text"] == "file contents"


def test_unknown_tool_is_404():
    with _client([], {}) as c:
        resp = c.post("/tools/call", json={"name": "nope.tool", "arguments": {}})
        assert resp.status_code == 404


def test_tool_error_propagates_as_200_with_is_error():
    tools = [_tool("github.delete_branch", "github", "delete_branch", destructive=True)]
    results = {"github.delete_branch": _FakeResult(is_error=True, text="refused")}
    with _client(tools, results) as c:
        resp = c.post(
            "/tools/call", json={"name": "github.delete_branch", "arguments": {"name": "main"}}
        )
        assert resp.status_code == 200
        assert resp.json()["is_error"] is True


def test_upstream_failure_is_502():
    tools = [_tool("files.boom", "files", "boom")]
    results = {"files.boom": RuntimeError("connection refused")}
    with _client(tools, results) as c:
        resp = c.post("/tools/call", json={"name": "files.boom", "arguments": {}})
        assert resp.status_code == 502
