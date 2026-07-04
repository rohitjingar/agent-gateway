"""Shared test helpers: a fake registry + auth header minting, so proxy/auth
tests run with no real MCP server and no network."""

from __future__ import annotations

from types import SimpleNamespace

from agent_gateway.auth import create_access_token
from agent_gateway.registry import RegisteredTool


class FakeResult:
    """Stand-in for mcp.types.CallToolResult."""

    def __init__(self, is_error: bool = False, text: str = "ok", structured=None):
        self.isError = is_error
        self.content = [SimpleNamespace(type="text", text=text)]
        self.structuredContent = structured


class FakeRegistry:
    """Duck-typed ToolRegistry: same .list()/.get()/.call() the router uses."""

    def __init__(self, tools, results=None):
        self._tools = {t.namespaced_name: t for t in tools}
        self._results = results or {}
        self.calls: list[tuple[str, dict]] = []  # records every executed call

    def list(self):
        return list(self._tools.values())

    def servers(self):
        counts: dict[str, int] = {}
        for t in self._tools.values():
            counts[t.server] = counts.get(t.server, 0) + 1
        return [{"name": s, "url": f"http://{s}/mcp", "tool_count": n} for s, n in counts.items()]

    async def refresh(self):
        return None

    def get(self, name):
        return self._tools.get(name)

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        result = self._results.get(name, FakeResult())
        if isinstance(result, Exception):
            raise result
        return result


def make_tool(ns, server, name, *, destructive=False, read_only=False, quarantined=False):
    return RegisteredTool(
        ns,
        server,
        name,
        "http://x/mcp",
        "desc",
        {},
        destructive,
        read_only,
        warnings=(["poison"] if quarantined else []),
        quarantined=quarantined,
    )


DEMO_TOOLS = [
    make_tool("files.read_file", "files", "read_file", read_only=True),
    make_tool("files.list_dir", "files", "list_dir", read_only=True),
    make_tool("files.write_file", "files", "write_file"),
    make_tool("github.list_branches", "github", "list_branches", read_only=True),
    make_tool("github.delete_branch", "github", "delete_branch", destructive=True),
]


def auth_header(role: str = "admin", subject: str = "tester") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject, role)}"}
