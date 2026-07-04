"""Tiny MCP client demo (Phase 1) — proves the raw protocol before any gateway.

Start a server first, e.g.:
    PORT=9101 uv run python -m agent_gateway.mcp_servers.files_server
then, in another shell:
    uv run python scripts/mcp_demo.py http://127.0.0.1:9101/mcp

You should see the two verbs that matter: tools/list (discovery) and
tools/call (invocation).
"""

from __future__ import annotations

import asyncio
import sys

from agent_gateway.mcp_client import call_tool, list_tools


def _text(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


async def main(url: str) -> None:
    print(f"[demo] connecting to MCP server at {url}")

    tools = await list_tools(url)
    print(f"[demo] tools/list -> {[t.name for t in tools]}")
    for t in tools:
        print(f"        - {t.name}: {t.description}")

    print("[demo] tools/call list_dir(path='.')")
    result = await call_tool(url, "list_dir", {"path": "."})
    print(f"[demo] result (isError={result.isError}): {_text(result)!r}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9101/mcp"
    asyncio.run(main(target))
