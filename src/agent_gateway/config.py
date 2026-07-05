"""Typed configuration, loaded from environment (and .env) via pydantic-settings.

Every knob the gateway needs lives here as a typed field, so there are no
stray ``os.environ`` reads scattered through the code and no secrets in source.
Env vars are prefixed ``GATEWAY_`` (e.g. GATEWAY_ENV, GATEWAY_UPSTREAMS).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Upstream(BaseModel):
    """One MCP server the gateway proxies to."""

    name: str  # namespace prefix, e.g. "files"
    url: str  # streamable-HTTP endpoint, e.g. http://127.0.0.1:9101/mcp


# Sensible local defaults (match the two demo servers). Override in Docker via
# GATEWAY_UPSTREAMS='[{"name":"files","url":"http://files:9101/mcp"}, ...]'.
DEFAULT_UPSTREAMS: list[Upstream] = [
    Upstream(name="files", url="http://127.0.0.1:9101/mcp"),
    Upstream(name="github", url="http://127.0.0.1:9102/mcp"),
]

# Placeholder secret for LOCAL DEV ONLY (>=32 bytes to satisfy HS256 guidance).
# Production MUST override via GATEWAY_JWT_SECRET.
DEV_INSECURE_SECRET = "dev-insecure-change-me-not-for-production!"

# Advisory-lock key so concurrent replicas don't race on CREATE TABLE at startup.
SCHEMA_LOCK_KEY = 776622


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    env: str = "local"
    upstreams: list[Upstream] = DEFAULT_UPSTREAMS

    # --- auth (JWT / HS256) ---
    jwt_secret: str = DEV_INSECURE_SECRET  # OVERRIDE via GATEWAY_JWT_SECRET in prod
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "agent-gateway"
    access_token_ttl_minutes: int = 60
    dev_auth: bool = True  # expose POST /auth/token to mint demo tokens

    # --- rate limiting (Redis token bucket) ---
    redis_url: str = "redis://127.0.0.1:6379/0"
    rate_limit_enabled: bool = True
    rate_limit_capacity: int = 20  # burst size
    rate_limit_refill_per_sec: float = 5.0  # sustained rate

    # --- audit log (Postgres) ---
    database_url: str = "postgresql://gateway:gateway@127.0.0.1:5432/gateway"
    audit_enabled: bool = True

    # --- observability (OpenTelemetry) ---
    otel_enabled: bool = False  # set true in Docker; off locally so no collector is needed
    otel_endpoint: str = "http://127.0.0.1:4318"  # OTLP/HTTP; Jaeger accepts this
    otel_service_name: str = "agent-gateway"

    # --- human-in-the-loop approval ---
    approval_enabled: bool = True
    high_risk_tools: list[str] = ["github.delete_branch"]
    high_risk_auto_destructive: bool = False  # also gate any destructive-hinted tool

    # --- reliability / timeouts ---
    upstream_timeout_seconds: float = 15.0  # bound each upstream MCP call

    # --- identity provider (asymmetric verification) ---
    # Set jwt_algorithm="RS256" + jwt_public_key=<PEM> to verify tokens minted by a
    # real IdP (Okta/Auth0/Keycloak). HS256 + jwt_secret stays the local-dev default.
    jwt_public_key: str | None = None
    jwt_audience: str | None = None  # verify the `aud` claim when your IdP sets one

    # --- multi-instance policy sync ---
    policy_reload_channel: str = "agent-gateway:policy-reload"
    policy_reload_interval_seconds: float = 30.0  # periodic fallback reload

    # --- observability ---
    metrics_enabled: bool = True
    log_format: str = "text"  # "text" | "json"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so config is parsed once per process."""
    return Settings()
