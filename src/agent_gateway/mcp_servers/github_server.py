"""Demo "GitHub" MCP server (in-memory, no real API or token).

Simulates a repo so we can demonstrate a HIGH-RISK, destructive tool
(``delete_branch``) travelling through the gateway's human-approval lane. State
is in-memory and resets on restart — that's fine; the point is the risk surface,
not persistence.

Run standalone:
    PORT=9102 uv run python -m agent_gateway.mcp_servers.github_server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP(
    "github",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("PORT", "9102")),
)

# Protected branches the server itself refuses to delete. Defense in depth: the
# gateway's approval gate is the *primary* control; this is a server-side backstop.
_PROTECTED = {"main"}
_branches: set[str] = {"main", "develop", "feature/login"}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_branches() -> list[str]:
    """List branch names in the demo repo."""
    return sorted(_branches)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def create_branch(name: str) -> str:
    """Create a new branch."""
    if name in _branches:
        raise ValueError(f"branch already exists: {name}")
    _branches.add(name)
    return f"created branch {name}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def delete_branch(name: str) -> str:
    """Delete a branch. DESTRUCTIVE and irreversible in a real repo."""
    if name in _PROTECTED:
        raise ValueError(f"refusing to delete protected branch: {name}")
    if name not in _branches:
        raise ValueError(f"no such branch: {name}")
    _branches.discard(name)
    return f"deleted branch {name}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
