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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    env: str = "local"
    upstreams: list[Upstream] = DEFAULT_UPSTREAMS


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so config is parsed once per process."""
    return Settings()
