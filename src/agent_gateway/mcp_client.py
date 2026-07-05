"""Thin async client for talking to an upstream MCP server over streamable HTTP.

Each call opens a short-lived session (connect -> initialize -> call -> close).
Keeping session lifetime this simple makes correctness trivial; a persistent,
pooled session is the documented production upgrade (see README tradeoffs).

The gateway (Phase 2+) reuses these helpers to proxy tool calls.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, Tool


@asynccontextmanager
async def open_session(url: str):
    """Open an initialized MCP client session against ``url`` (e.g. .../mcp)."""
    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()  # the MCP handshake (capabilities exchange)
            yield session


async def list_tools(url: str, timeout: float = 15.0) -> list[Tool]:
    """The MCP ``tools/list`` verb: discover what an upstream can do."""
    async with asyncio.timeout(timeout), open_session(url) as session:
        return (await session.list_tools()).tools


async def call_tool(
    url: str, name: str, arguments: dict[str, Any], timeout: float = 15.0
) -> CallToolResult:
    """The MCP ``tools/call`` verb: invoke one tool with arguments.

    Bounded by ``timeout`` so a hung upstream can't hang the request (raises
    TimeoutError, which the gateway maps to 504)."""
    async with asyncio.timeout(timeout), open_session(url) as session:
        return await session.call_tool(name, arguments)
