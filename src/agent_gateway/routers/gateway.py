"""The gateway's core proxy surface: discover tools and invoke them.

Every endpoint requires a valid JWT (authentication) and enforces per-tool RBAC
(authorization) before anything reaches an upstream.

    GET  /tools        -> tools the caller's role may use (namespaced)
    POST /tools/call   -> forward one call, if the role is allowed

Error mapping (deliberate):
    no/invalid token      -> 401
    known tool, no access -> 403
    unknown tool          -> 404
    upstream unreachable  -> 502
    tool ran but failed   -> 200 with is_error=true
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent_gateway.auth import Principal, get_principal
from agent_gateway.models import CallToolIn, CallToolOut, ToolInfo
from agent_gateway.rbac import allowed_tools, is_allowed
from agent_gateway.registry import ToolRegistry

router = APIRouter()


def _registry(request: Request) -> ToolRegistry:
    return request.app.state.registry


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
    if reg.get(body.name) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown tool: {body.name}")
    if not is_allowed(principal.role, body.name):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"role {principal.role!r} may not call {body.name!r}",
        )
    try:
        result = await reg.call(body.name, body.arguments)
    except Exception as exc:  # noqa: BLE001 - map any upstream/transport failure to 502
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"upstream error: {exc}") from exc

    return CallToolOut(
        name=body.name,
        is_error=bool(result.isError),
        content=[_block_to_dict(b) for b in result.content],
        structured=result.structuredContent,
    )
