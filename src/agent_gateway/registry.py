"""Tool registry: the gateway's map of every upstream tool it can proxy.

On refresh it connects to each configured MCP server, runs ``tools/list``, and
records each tool under a namespaced name (``<server>.<tool>``) so identical tool
names on different servers never collide. This is the classic *service registry*
pattern applied to MCP tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent_gateway import mcp_client
from agent_gateway.config import Upstream
from agent_gateway.security import scan_description

log = logging.getLogger(__name__)

NAMESPACE_SEP = "."


@dataclass
class RegisteredTool:
    namespaced_name: str  # "files.read_file"
    server: str  # "files"
    tool_name: str  # "read_file" (as the upstream knows it)
    url: str  # upstream endpoint
    description: str
    input_schema: dict
    destructive: bool
    read_only: bool
    warnings: list[str] = field(default_factory=list)  # poisoning-scan hits
    quarantined: bool = False  # hidden from discovery + blocked at call time


class ToolRegistry:
    def __init__(self, upstreams: list[Upstream], timeout: float = 15.0) -> None:
        self._upstreams = upstreams
        self._timeout = timeout
        self._tools: dict[str, RegisteredTool] = {}

    async def refresh(self) -> None:
        """(Re)discover tools from all upstreams. Unreachable servers are logged
        and skipped, not fatal — the gateway stays up on partial availability."""
        discovered: dict[str, RegisteredTool] = {}
        for up in self._upstreams:
            try:
                tools = await mcp_client.list_tools(up.url, timeout=self._timeout)
            except Exception as exc:  # noqa: BLE001 - resilience: tolerate a down upstream
                log.warning("upstream %r (%s) unavailable: %s", up.name, up.url, exc)
                continue
            for t in tools:
                ns = f"{up.name}{NAMESPACE_SEP}{t.name}"
                ann = t.annotations
                warnings = scan_description(t.description or "")
                if warnings:
                    log.warning("tool %r quarantined by poisoning scan: %s", ns, warnings)
                discovered[ns] = RegisteredTool(
                    namespaced_name=ns,
                    server=up.name,
                    tool_name=t.name,
                    url=up.url,
                    description=t.description or "",
                    input_schema=t.inputSchema or {},
                    destructive=bool(getattr(ann, "destructiveHint", False)) if ann else False,
                    read_only=bool(getattr(ann, "readOnlyHint", False)) if ann else False,
                    warnings=warnings,
                    quarantined=bool(warnings),
                )
            log.info("registered %d tools from upstream %r", len(tools), up.name)
        self._tools = discovered

    def set_upstreams(self, upstreams: list[Upstream]) -> None:
        """Replace the upstream list (used when the admin edits servers at runtime).
        Call refresh() afterwards to re-discover tools."""
        self._upstreams = upstreams

    def list(self) -> list[RegisteredTool]:
        return sorted(self._tools.values(), key=lambda t: t.namespaced_name)

    def servers(self) -> list[dict]:
        """Per-upstream summary for the discovery endpoint (name, url, tool count)."""
        counts: dict[str, int] = {}
        for tool in self._tools.values():
            counts[tool.server] = counts.get(tool.server, 0) + 1
        return [
            {"name": up.name, "url": up.url, "tool_count": counts.get(up.name, 0)}
            for up in self._upstreams
        ]

    def get(self, namespaced_name: str) -> RegisteredTool | None:
        return self._tools.get(namespaced_name)

    async def call(self, namespaced_name: str, arguments: dict):
        """Forward a namespaced call to the owning upstream. Raises KeyError if
        unknown; upstream/transport failures propagate to the caller."""
        tool = self.get(namespaced_name)
        if tool is None:
            raise KeyError(namespaced_name)
        return await mcp_client.call_tool(
            tool.url, tool.tool_name, arguments, timeout=self._timeout
        )
