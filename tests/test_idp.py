"""IdP integration: verify RS256 tokens signed by an external private key,
using only the public key (the gateway holds no signing secret)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from agent_gateway.auth import get_principal
from agent_gateway.config import Settings


def _rsa_pem_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return priv, pub


def _request_with(settings: Settings):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_rs256_token_from_idp_is_accepted():
    priv, pub = _rsa_pem_pair()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "idp-user",
            "role": "admin",
            "iss": "https://idp.example",
            "exp": now + timedelta(minutes=5),
        },
        priv,
        algorithm="RS256",
    )
    settings = Settings(jwt_algorithm="RS256", jwt_public_key=pub, jwt_issuer="https://idp.example")
    principal = get_principal(_request_with(settings), _bearer(token))
    assert principal.subject == "idp-user"
    assert principal.role == "admin"


def test_rs256_token_signed_by_wrong_key_is_rejected():
    priv_attacker, _ = _rsa_pem_pair()
    _, pub_real = _rsa_pem_pair()
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": "x",
            "role": "admin",
            "iss": "https://idp.example",
            "exp": now + timedelta(minutes=5),
        },
        priv_attacker,
        algorithm="RS256",
    )
    settings = Settings(
        jwt_algorithm="RS256", jwt_public_key=pub_real, jwt_issuer="https://idp.example"
    )
    with pytest.raises(HTTPException) as exc:
        get_principal(_request_with(settings), _bearer(forged))
    assert exc.value.status_code == 401
