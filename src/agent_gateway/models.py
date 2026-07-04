"""Pydantic models for the gateway's own HTTP API (its request/response boundary).

These are deliberately separate from MCP's wire types: the gateway presents a
clean, stable REST surface to agents and translates to/from MCP underneath.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolInfo(BaseModel):
    """One tool as the gateway advertises it (namespaced, with risk hints)."""

    name: str  # namespaced: "<server>.<tool>", e.g. "files.read_file"
    server: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = False
    destructive: bool = False


class CallToolIn(BaseModel):
    """Request body for invoking a tool."""

    name: str  # namespaced tool name
    arguments: dict[str, Any] = Field(default_factory=dict)


class CallToolOut(BaseModel):
    """Result of a tool call. `is_error=True` means the tool ran and reported a
    failure (application-level) — distinct from an HTTP 4xx/5xx from the gateway."""

    name: str
    is_error: bool
    content: list[dict[str, Any]]
    structured: dict[str, Any] | None = None
