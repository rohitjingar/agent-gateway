"""Phase 1: exercise the demo MCP servers over an in-memory client session.

`create_connected_server_and_client_session` wires a real MCP client to the
server through in-memory streams — the full protocol (initialize/list/call) with
no sockets, so it's fast and CI-safe.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

import agent_gateway.mcp_servers.github_server as gh
from agent_gateway.mcp_servers.files_server import mcp as files_server
from agent_gateway.mcp_servers.github_server import mcp as github_server


def _text(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


async def test_files_write_then_read(tmp_path, monkeypatch):
    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    async with connect(files_server) as session:
        names = {t.name for t in (await session.list_tools()).tools}
        assert {"read_file", "write_file", "list_dir"} <= names

        await session.call_tool("write_file", {"path": "note.txt", "content": "hi"})
        result = await session.call_tool("read_file", {"path": "note.txt"})
        assert result.isError is False
        assert _text(result) == "hi"


async def test_files_sandbox_escape_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    async with connect(files_server) as session:
        result = await session.call_tool("read_file", {"path": "../../../etc/passwd"})
        assert result.isError is True  # traversal rejected -> tool error, not a leak


@pytest.fixture(autouse=True)
def _reset_branches():
    gh._branches = {"main", "develop", "feature/login"}
    yield


async def test_github_delete_branch_and_protect_main():
    async with connect(github_server) as session:
        ok = await session.call_tool("delete_branch", {"name": "develop"})
        assert ok.isError is False

        listed = await session.call_tool("list_branches", {})
        assert "develop" not in _text(listed)
        assert "main" in _text(listed)

        protected = await session.call_tool("delete_branch", {"name": "main"})
        assert protected.isError is True  # server-side backstop for protected branch
