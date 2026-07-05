"""The gateway's core proxy surface: discover tools and invoke them.

Every endpoint requires a valid JWT (authentication) and enforces per-tool
authorization from the live policy (which is DB-backed and editable from the admin
UI). `/tools/call` also rate-limits, diverts HIGH-RISK tools to the approval queue
(202, not executed), traces the call, and audits every outcome.

Error / status mapping:
    no/invalid token      -> 401
    known tool, no access -> 403
    unknown tool          -> 404
    over quota            -> 429
    high-risk tool        -> 202 pending_approval (queued, not executed)
    upstream unreachable  -> 502
    tool ran but failed   -> 200 with is_error=true
"""

from __future__ import annotations

import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from opentelemetry.trace import Status, StatusCode

from agent_gateway.approvals import ApprovalStore
from agent_gateway.audit import AuditSink, build_record, hash_args
from agent_gateway.auth import Principal, get_principal
from agent_gateway.config import Settings
from agent_gateway.models import (
    CallToolIn,
    CallToolOut,
    PendingApprovalOut,
    ToolInfo,
    block_to_dict,
)
from agent_gateway.policy import LivePolicy
from agent_gateway.rate_limit import RateLimiter
from agent_gateway.registry import ToolRegistry
from agent_gateway.telemetry import get_tracer

log = logging.getLogger(__name__)
router = APIRouter()


def _registry(request: Request) -> ToolRegistry:
    return request.app.state.registry


def _rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def _audit(request: Request) -> AuditSink:
    return request.app.state.audit


def _approvals(request: Request) -> ApprovalStore:
    return request.app.state.approvals


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _policy(request: Request) -> LivePolicy:
    return request.app.state.policy


@router.get("/tools", tags=["gateway"], response_model=list[ToolInfo])
async def list_tools(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> list[ToolInfo]:
    reg = _registry(request)
    policy = _policy(request)
    settings = _settings(request)
    tools = reg.list()
    allowed = set(policy.allowed_tools(principal.role, [t.namespaced_name for t in tools]))
    return [
        ToolInfo(
            name=t.namespaced_name,
            server=t.server,
            description=t.description,
            input_schema=t.input_schema,
            read_only=t.read_only,
            destructive=t.destructive,
            high_risk=settings.approval_enabled and policy.is_tool_high_risk(t.namespaced_name),
        )
        for t in tools
        if t.namespaced_name in allowed and not t.quarantined
    ]


@router.get("/registry", tags=["gateway"])
async def registry_view(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict:
    """Discovery view: every upstream server and every tool, with risk + whether
    the caller's role may use it."""
    reg = _registry(request)
    policy = _policy(request)
    settings = _settings(request)
    return {
        "servers": reg.servers(),
        "tools": [
            {
                "name": t.namespaced_name,
                "server": t.server,
                "read_only": t.read_only,
                "destructive": t.destructive,
                "high_risk": settings.approval_enabled
                and policy.is_tool_high_risk(t.namespaced_name),
                "quarantined": t.quarantined,
                "warnings": t.warnings,
                "allowed": policy.is_allowed(principal.role, t.namespaced_name),
            }
            for t in reg.list()
        ],
    }


@router.post("/tools/call", tags=["gateway"])
async def call_tool(
    request: Request,
    body: CallToolIn,
    principal: Principal = Depends(get_principal),
):
    """Returns CallToolOut (200) for normal calls, or PendingApprovalOut (202)
    when a high-risk tool is queued for human approval."""
    reg = _registry(request)
    limiter = _rate_limiter(request)
    sink = _audit(request)
    settings = _settings(request)
    policy = _policy(request)
    tracer = get_tracer()

    with tracer.start_as_current_span("gateway.tools.call") as span:
        span.set_attribute("mcp.tool.name", body.name)
        span.set_attribute("auth.subject", principal.subject)
        span.set_attribute("auth.role", principal.role)
        span.set_attribute("mcp.args_hash", hash_args(body.arguments))

        started = perf_counter()
        outcome = "ok"
        try:
            tool = reg.get(body.name)
            if tool is None:
                outcome = "unknown_tool"
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown tool: {body.name}")
            span.set_attribute("mcp.tool.server", tool.server)

            if tool.quarantined:
                outcome = "quarantined"
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"tool {body.name!r} is quarantined (poisoning scan): {tool.warnings}",
                )
            if not policy.is_allowed(principal.role, body.name):
                outcome = "denied"
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"role {principal.role!r} may not call {body.name!r}",
                )
            if not await limiter.allow(principal.subject):
                outcome = "rate_limited"
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")

            # HIGH-RISK: queue for a human instead of executing. Return 202 now.
            if settings.approval_enabled and policy.is_tool_high_risk(body.name):
                outcome = "pending_approval"
                record = await _approvals(request).create(
                    subject=principal.subject,
                    role=principal.role,
                    tool=body.name,
                    arguments=body.arguments,
                )
                span.set_attribute("mcp.approval_id", record.id)
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content=PendingApprovalOut(
                        approval_id=record.id,
                        tool=body.name,
                        message="high-risk tool requires human approval",
                    ).model_dump(),
                )

            try:
                result = await reg.call(body.name, body.arguments)
            except TimeoutError as exc:
                outcome = "upstream_timeout"
                raise HTTPException(
                    status.HTTP_504_GATEWAY_TIMEOUT, f"upstream timed out: {body.name}"
                ) from exc
            except Exception as exc:  # noqa: BLE001 - any upstream/transport failure -> 502
                outcome = "upstream_error"
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"upstream error: {exc}") from exc

            is_error = bool(result.isError)
            outcome = "tool_error" if is_error else "ok"
            return CallToolOut(
                name=body.name,
                is_error=is_error,
                content=[block_to_dict(b) for b in result.content],
                structured=result.structuredContent,
            )
        finally:
            latency_ms = (perf_counter() - started) * 1000
            span.set_attribute("mcp.outcome", outcome)
            span.set_attribute("mcp.latency_ms", round(latency_ms, 2))
            if outcome not in ("ok", "pending_approval"):
                span.set_status(Status(StatusCode.ERROR, outcome))
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
