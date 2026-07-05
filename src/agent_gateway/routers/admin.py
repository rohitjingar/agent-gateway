"""Admin surface: the UI page, config (servers / roles / high-risk), approvals,
and audit. Everything is admin-only except GET /admin/ui, which serves the page
shell (the page then asks the operator for a token).

Config edits persist to the DB via PolicyRepo, then reload the in-memory policy
snapshot and re-discover the registry — so changes take effect immediately with
no restart. If no database is configured, config is read-only (edits return 400).
"""

from __future__ import annotations

import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent_gateway.admin_ui import ADMIN_HTML
from agent_gateway.audit import build_record
from agent_gateway.auth import Principal, get_principal
from agent_gateway.models import ApprovalOut, approval_to_out, call_result_payload

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return principal


def _repo(request: Request):
    repo = request.app.state.policy_repo
    if repo is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "config is read-only (no database configured)"
        )
    return repo


async def _reload_policy(request: Request) -> None:
    """After a persisted edit: reload the snapshot and re-discover servers."""
    request.app.state.policy = await request.app.state.policy_repo.load()
    reg = request.app.state.registry
    reg.set_upstreams(request.app.state.policy.servers)
    await reg.refresh()


# --------------------------- the admin UI page ---------------------------
@router.get("/admin/ui", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui() -> HTMLResponse:
    return HTMLResponse(ADMIN_HTML)


# --------------------------- config: servers / roles / risk ---------------------------
class ServerIn(BaseModel):
    name: str
    url: str


class RoleIn(BaseModel):
    patterns: list[str]


class HighRiskIn(BaseModel):
    high: bool


@router.get("/admin/config")
async def get_config(request: Request, _: Principal = Depends(require_admin)) -> dict:
    policy = request.app.state.policy
    reg = request.app.state.registry
    return {
        "servers": [{"name": s.name, "url": s.url} for s in policy.servers],
        "roles": policy.role_patterns,
        "high_risk": sorted(policy.high_risk),
        "all_tools": [t.namespaced_name for t in reg.list()],
        "editable": request.app.state.policy_repo is not None,
    }


@router.post("/admin/servers")
async def add_server(
    request: Request, body: ServerIn, _: Principal = Depends(require_admin)
) -> dict:
    await _repo(request).add_server(body.name, body.url)
    await _reload_policy(request)
    return {"ok": True}


@router.delete("/admin/servers/{name}")
async def remove_server(request: Request, name: str, _: Principal = Depends(require_admin)) -> dict:
    await _repo(request).remove_server(name)
    await _reload_policy(request)
    return {"ok": True}


@router.put("/admin/roles/{role}")
async def set_role(
    request: Request, role: str, body: RoleIn, _: Principal = Depends(require_admin)
) -> dict:
    if role == "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the 'admin' role is protected")
    await _repo(request).set_role(role, body.patterns)
    await _reload_policy(request)
    return {"ok": True}


@router.delete("/admin/roles/{role}")
async def remove_role(request: Request, role: str, _: Principal = Depends(require_admin)) -> dict:
    if role == "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the 'admin' role is protected")
    await _repo(request).remove_role(role)
    await _reload_policy(request)
    return {"ok": True}


@router.put("/admin/tools/{tool}/high-risk")
async def set_high_risk(
    request: Request, tool: str, body: HighRiskIn, _: Principal = Depends(require_admin)
) -> dict:
    await _repo(request).set_high_risk(tool, body.high)
    await _reload_policy(request)
    return {"ok": True}


# --------------------------- registry refresh ---------------------------
@router.post("/registry/refresh")
async def refresh_registry(request: Request, _: Principal = Depends(require_admin)) -> dict:
    reg = request.app.state.registry
    await reg.refresh()
    return {"tools": len(reg.list())}


# --------------------------- audit ---------------------------
@router.get("/audit/recent")
async def audit_recent(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    _: Principal = Depends(require_admin),
) -> list[dict]:
    return await request.app.state.audit.recent(limit)


# --------------------------- approvals ---------------------------
@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    _: Principal = Depends(require_admin),
) -> list[ApprovalOut]:
    rows = await request.app.state.approvals.list(status=status_filter, limit=limit)
    return [approval_to_out(r) for r in rows]


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    request: Request, approval_id: str, _: Principal = Depends(require_admin)
) -> ApprovalOut:
    rec = await request.app.state.approvals.get(approval_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    return approval_to_out(rec)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    request: Request, approval_id: str, principal: Principal = Depends(require_admin)
) -> ApprovalOut:
    store = request.app.state.approvals
    reg = request.app.state.registry

    rec = await store.get(approval_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    if rec.status != "pending":
        return approval_to_out(rec)  # idempotent: already decided, do not re-execute

    claimed = await store.claim(approval_id, decided_by=principal.subject)
    if claimed is None:  # lost the race to another approver
        return approval_to_out(await store.get(approval_id))

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
    request: Request, approval_id: str, principal: Principal = Depends(require_admin)
) -> ApprovalOut:
    rec = await request.app.state.approvals.deny(approval_id, decided_by=principal.subject)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    await _record_decision(request, principal, rec.tool, rec.arguments, "denied", perf_counter())
    return approval_to_out(rec)


async def _record_decision(request, principal, tool, arguments, outcome, started) -> None:
    """Audit the human decision (who approved/denied what)."""
    try:
        await request.app.state.audit.record(
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
