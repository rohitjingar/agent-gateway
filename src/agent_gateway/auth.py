"""Authentication: *who* is calling? (identity, not permission.)

We verify a signed JWT bearer token on every gateway request and return a
`Principal` (subject + role). Whether that role may call a given tool is a
separate question answered in `rbac.py` — keeping authn and authz apart is the
whole reason per-tool control is even expressible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from agent_gateway.config import Settings, get_settings


class Principal(BaseModel):
    """The authenticated caller, distilled from a verified token."""

    subject: str
    role: str


def create_access_token(subject: str, role: str, settings: Settings | None = None) -> str:
    """Mint a short-lived HS256 token. In production the token would come from a
    real IdP; this helper exists so the demo (and tests) can issue one."""
    settings = settings or get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


_bearer = HTTPBearer(auto_error=False)


def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """FastAPI dependency: verify the bearer token or reject the request."""
    settings: Settings = request.app.state.settings
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = jwt.decode(
            creds.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc

    role = payload.get("role")
    if not role:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token missing role claim")
    return Principal(subject=payload["sub"], role=role)
