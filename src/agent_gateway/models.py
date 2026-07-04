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


def block_to_dict(block: Any) -> dict[str, Any]:
    """Normalize an MCP content block (pydantic model) to a plain dict."""
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json")
    return {"type": getattr(block, "type", "text"), "text": getattr(block, "text", None)}


def call_result_payload(result: Any) -> dict[str, Any]:
    """Serialize an MCP CallToolResult into a JSON-friendly dict."""
    return {
        "is_error": bool(result.isError),
        "content": [block_to_dict(b) for b in result.content],
        "structured": result.structuredContent,
    }


class PendingApprovalOut(BaseModel):
    """Returned (HTTP 202) when a high-risk call is queued for a human."""

    status: str = "pending_approval"
    approval_id: str
    tool: str
    message: str


class ApprovalOut(BaseModel):
    """A pending/decided approval, as the admin API exposes it."""

    id: str
    subject: str
    role: str
    tool: str
    arguments: dict[str, Any]
    status: str
    outcome: str | None = None
    result: dict[str, Any] | None = None
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None


def approval_to_out(rec: Any) -> ApprovalOut:
    return ApprovalOut(
        id=rec.id,
        subject=rec.subject,
        role=rec.role,
        tool=rec.tool,
        arguments=rec.arguments,
        status=rec.status,
        outcome=rec.outcome,
        result=rec.result,
        created_at=rec.created_at.isoformat(),
        decided_at=rec.decided_at.isoformat() if rec.decided_at else None,
        decided_by=rec.decided_by,
    )
