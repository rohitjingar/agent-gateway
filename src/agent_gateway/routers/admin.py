"""Admin surface: read the audit log and drive the approval lane. Admin only.

    GET  /audit/recent
    GET  /approvals?status=pending
    GET  /approvals/{id}
    POST /approvals/{id}/approve   -> execute the tool now (exactly once), record result
    POST /approvals/{id}/deny      -> reject; tool never runs

Approve/deny are idempotent: a call on an already-decided request returns its
current state without re-executing.
"""

from __future__ import annotations

import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from agent_gateway.approvals import ApprovalStore
from agent_gateway.audit import AuditSink, build_record
from agent_gateway.auth import Principal, get_principal
from agent_gateway.models import ApprovalOut, approval_to_out, call_result_payload
from agent_gateway.registry import ToolRegistry

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return principal


def _approvals(request: Request) -> ApprovalStore:
    return request.app.state.approvals


def _registry(request: Request) -> ToolRegistry:
    return request.app.state.registry


def _audit(request: Request) -> AuditSink:
    return request.app.state.audit


@router.get("/audit/recent")
async def audit_recent(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    _: Principal = Depends(require_admin),
) -> list[dict]:
    return await request.app.state.audit.recent(limit)


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    _: Principal = Depends(require_admin),
) -> list[ApprovalOut]:
    rows = await _approvals(request).list(status=status_filter, limit=limit)
    return [approval_to_out(r) for r in rows]


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    request: Request,
    approval_id: str,
    _: Principal = Depends(require_admin),
) -> ApprovalOut:
    rec = await _approvals(request).get(approval_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    return approval_to_out(rec)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    request: Request,
    approval_id: str,
    principal: Principal = Depends(require_admin),
) -> ApprovalOut:
    store = _approvals(request)
    reg = _registry(request)

    rec = await store.get(approval_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    if rec.status != "pending":
        return approval_to_out(rec)  # idempotent: already decided, do not re-execute

    claimed = await store.claim(approval_id, decided_by=principal.subject)
    if claimed is None:  # lost the race to another approver
        current = await store.get(approval_id)
        return approval_to_out(current)

    # A human approved: NOW we execute the tool, exactly once.
    started = perf_counter()
    try:
        result = await reg.call(claimed.tool, claimed.arguments)
        outcome = "tool_error" if result.isError else "ok"
        payload = call_result_payload(result)
    except Exception as exc:  # noqa: BLE001
        outcome = "upstream_error"
        payload = {"error": str(exc)}
    updated = await store.set_result(approval_id, payload, outcome)

    await _record_decision(request, principal, claimed.tool, claimed.arguments, outcome, started)
    return approval_to_out(updated)


@router.post("/approvals/{approval_id}/deny", response_model=ApprovalOut)
async def deny(
    request: Request,
    approval_id: str,
    principal: Principal = Depends(require_admin),
) -> ApprovalOut:
    rec = await _approvals(request).deny(approval_id, decided_by=principal.subject)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    await _record_decision(request, principal, rec.tool, rec.arguments, "denied", perf_counter())
    return approval_to_out(rec)


async def _record_decision(request, principal, tool, arguments, outcome, started) -> None:
    """Audit the human decision (who approved/denied what)."""
    try:
        await _audit(request).record(
            build_record(
                principal.subject,
                principal.role,
                tool,
                arguments,
                outcome,
                (perf_counter() - started) * 1000,
            )
        )
    except Exception:  # noqa: BLE001
        log.exception("audit write failed")
