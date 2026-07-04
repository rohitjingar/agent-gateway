"""The gateway's core proxy surface: discover tools and invoke them.

Every endpoint requires a valid JWT (authentication) and enforces per-tool RBAC
(authorization). `/tools/call` additionally rate-limits per caller and writes an
audit record for *every* outcome via a single `finally` path.

Error mapping (deliberate):
    no/invalid token      -> 401
    known tool, no access -> 403
    unknown tool          -> 404
    over quota            -> 429
    upstream unreachable  -> 502
    tool ran but failed   -> 200 with is_error=true
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent_gateway.audit import AuditSink, build_record
from agent_gateway.auth import Principal, get_principal
from agent_gateway.models import CallToolIn, CallToolOut, ToolInfo
from agent_gateway.rate_limit import RateLimiter
from agent_gateway.rbac import allowed_tools, is_allowed
from agent_gateway.registry import ToolRegistry

log = logging.getLogger(__name__)
router = APIRouter()


def _registry(request: Request) -> ToolRegistry:
    return request.app.state.registry


def _rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def _audit(request: Request) -> AuditSink:
    return request.app.state.audit


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Normalize an MCP content block (pydantic model) to a plain dict."""
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json")
    return {"type": getattr(block, "type", "text"), "text": getattr(block, "text", None)}


@router.get("/tools", tags=["gateway"], response_model=list[ToolInfo])
async def list_tools(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> list[ToolInfo]:
    reg = _registry(request)
    tools = reg.list()
    allowed = set(allowed_tools(principal.role, [t.namespaced_name for t in tools]))
    return [
        ToolInfo(
            name=t.namespaced_name,
            server=t.server,
            description=t.description,
            input_schema=t.input_schema,
            read_only=t.read_only,
            destructive=t.destructive,
        )
        for t in tools
        if t.namespaced_name in allowed
    ]


@router.post("/tools/call", tags=["gateway"], response_model=CallToolOut)
async def call_tool(
    request: Request,
    body: CallToolIn,
    principal: Principal = Depends(get_principal),
) -> CallToolOut:
    reg = _registry(request)
    limiter = _rate_limiter(request)
    sink = _audit(request)

    started = perf_counter()
    outcome = "ok"
    try:
        if reg.get(body.name) is None:
            outcome = "unknown_tool"
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown tool: {body.name}")
        if not is_allowed(principal.role, body.name):
            outcome = "denied"
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"role {principal.role!r} may not call {body.name!r}",
            )
        if not await limiter.allow(principal.subject):
            outcome = "rate_limited"
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")

        try:
            result = await reg.call(body.name, body.arguments)
        except Exception as exc:  # noqa: BLE001 - any upstream/transport failure -> 502
            outcome = "upstream_error"
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"upstream error: {exc}") from exc

        is_error = bool(result.isError)
        outcome = "tool_error" if is_error else "ok"
        return CallToolOut(
            name=body.name,
            is_error=is_error,
            content=[_block_to_dict(b) for b in result.content],
            structured=result.structuredContent,
        )
    finally:
        latency_ms = (perf_counter() - started) * 1000
        try:
            await sink.record(
                build_record(
                    principal.subject,
                    principal.role,
                    body.name,
                    body.arguments,
                    outcome,
                    latency_ms,
                )
            )
        except Exception:  # noqa: BLE001 - audit must never break the request path
            log.exception("audit write failed")
