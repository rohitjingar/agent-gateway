"""Filesystem MCP server (demo upstream).

Exposes read/list/write tools scoped to a sandbox root. Every path is resolved
*inside* the root; anything escaping it (``../../etc/passwd``) is rejected. This
is a genuinely dangerous capability in miniature — exactly what the gateway
exists to put auth, audit, and rate limits in front of.

Run standalone:
    PORT=9101 uv run python -m agent_gateway.mcp_servers.files_server
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP(
    "files",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("PORT", "9101")),
)


def _root() -> Path:
    """Resolve (and create) the sandbox root. Read lazily so tests can override
    FILES_ROOT per-test via the environment."""
    root = Path(os.environ.get("FILES_ROOT", "/tmp/agent-gateway-files")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe(path: str) -> Path:
    """Resolve ``path`` within the sandbox, rejecting traversal outside it."""
    root = _root()
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes sandbox root: {path!r}")
    return resolved


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_dir(path: str = ".") -> list[str]:
    """List entries in a directory within the sandbox."""
    target = _safe(path)
    if not target.is_dir():
        raise ValueError(f"not a directory: {path!r}")
    return sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def read_file(path: str) -> str:
    """Read a UTF-8 text file within the sandbox."""
    target = _safe(path)
    if not target.is_file():
        raise ValueError(f"not a file: {path!r}")
    return target.read_text(encoding="utf-8")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def write_file(path: str, content: str) -> str:
    """Create or overwrite a UTF-8 text file within the sandbox."""
    target = _safe(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {path}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
