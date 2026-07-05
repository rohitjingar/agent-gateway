"""Dev-only token endpoint so the demo can obtain a JWT without a real IdP.

Enabled by `settings.dev_auth` (true locally). In production you would delete or
disable this and mint tokens from your identity provider instead.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from agent_gateway.auth import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenRequest(BaseModel):
    subject: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/token", response_model=TokenResponse)
async def mint_token(request: Request, body: TokenRequest) -> TokenResponse:
    settings = request.app.state.settings
    if not settings.dev_auth:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dev token endpoint disabled")
    known_roles = request.app.state.policy.roles()
    if body.role not in known_roles:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown role {body.role!r}; known roles: {known_roles}",
        )
    token = create_access_token(body.subject, body.role, settings)
    return TokenResponse(access_token=token)
