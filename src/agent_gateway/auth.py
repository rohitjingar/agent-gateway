"""Authentication: *who* is calling? (identity, not permission.)

We verify a signed JWT bearer token on every gateway request and return a
`Principal` (subject + role). Two modes:

- **HS256 (local dev):** tokens are minted by the dev `/auth/token` endpoint and
  verified with a shared secret. Simple, self-contained.
- **RS256/ES256 (real IdP):** tokens are minted by your identity provider
  (Okta/Auth0/Keycloak) and verified here with the IdP's *public key* — the
  gateway never holds a signing secret. Set `jwt_algorithm` + `jwt_public_key`
  (+ optionally `jwt_audience`) and point `jwt_issuer` at the IdP's issuer.

Authorization (may this role call this tool) is a separate question — see policy.py.
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


def _is_asymmetric(algorithm: str) -> bool:
    return algorithm.startswith(("RS", "ES", "PS"))


def _verification_key(settings: Settings) -> str:
    """The key used to VERIFY a token: the IdP's public key for asymmetric
    algorithms, or the shared secret for HS256."""
    if _is_asymmetric(settings.jwt_algorithm):
        if not settings.jwt_public_key:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "asymmetric JWT configured but GATEWAY_JWT_PUBLIC_KEY is unset",
            )
        return settings.jwt_public_key
    return settings.jwt_secret


def create_access_token(subject: str, role: str, settings: Settings | None = None) -> str:
    """Mint a demo token (always HS256). This backs the dev `/auth/token` endpoint
    only — in an IdP deployment you disable that endpoint and the IdP mints tokens."""
    settings = settings or get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


_bearer = HTTPBearer(auto_error=False)


def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """FastAPI dependency: verify the bearer token or reject the request."""
    settings: Settings = request.app.state.settings
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")

    options: dict = {"require": ["exp", "sub", "iss"]}
    decode_kwargs: dict = {}
    if settings.jwt_audience:
        decode_kwargs["audience"] = settings.jwt_audience
    else:
        options["verify_aud"] = False

    try:
        payload = jwt.decode(
            creds.credentials,
            _verification_key(settings),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options=options,
            **decode_kwargs,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc

    role = payload.get("role")
    if not role:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token missing role claim")
    return Principal(subject=payload["sub"], role=role)
