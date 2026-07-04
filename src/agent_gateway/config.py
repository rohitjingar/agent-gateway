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


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so config is parsed once per process."""
    return Settings()
