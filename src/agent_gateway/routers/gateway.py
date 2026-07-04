"""The gateway's core proxy surface: discover tools and invoke them.

    GET  /tools        -> list every namespaced tool across all upstreams
    POST /tools/call   -> forward one call to the owning upstream

Error mapping (deliberate):
    unknown tool          -> 404 (client asked for something that doesn't exist)
    upstream unreachable  -> 502 (we couldn't reach the real server)
    tool ran but failed   -> 200 with is_error=true (application-level failure)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agent_gateway.models import CallToolIn, CallToolOut, ToolInfo
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
async def list_tools(request: Request) -> list[ToolInfo]:
    reg = _registry(request)
    return [
        ToolInfo(
            name=t.namespaced_name,
            server=t.server,
            description=t.description,
            input_schema=t.input_schema,
            read_only=t.read_only,
            destructive=t.destructive,
        )
        for t in reg.list()
    ]


@router.post("/tools/call", tags=["gateway"], response_model=CallToolOut)
async def call_tool(request: Request, body: CallToolIn) -> CallToolOut:
    reg = _registry(request)
    if reg.get(body.name) is None:
        raise HTTPException(status_code=404, detail=f"unknown tool: {body.name}")
    try:
        result = await reg.call(body.name, body.arguments)
    except Exception as exc:  # noqa: BLE001 - map any upstream/transport failure to 502
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

    return CallToolOut(
        name=body.name,
        is_error=bool(result.isError),
        content=[_block_to_dict(b) for b in result.content],
        structured=result.structuredContent,
    )
