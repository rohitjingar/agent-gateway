"""Admin surface: read the audit log. Admin role only.

(The human approval endpoints join this router in Phase 6.)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from agent_gateway.auth import Principal, get_principal

router = APIRouter(tags=["admin"])


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return principal


@router.get("/audit/recent")
async def audit_recent(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    _: Principal = Depends(require_admin),
) -> list[dict]:
    return await request.app.state.audit.recent(limit)
