"""FastAPI application entrypoint for the Agent Gateway.

`create_app` is a factory so tests can build isolated instances and inject fakes
(registry, rate limiter, audit sink). The lifespan builds the real collaborators
from settings, degrades gracefully if Redis/Postgres are absent, and closes them
on shutdown.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_gateway import __version__
from agent_gateway.audit import AuditSink, build_audit
from agent_gateway.config import DEV_INSECURE_SECRET, get_settings
from agent_gateway.rate_limit import RateLimiter, build_rate_limiter
from agent_gateway.registry import ToolRegistry
from agent_gateway.routers import admin as admin_router
from agent_gateway.routers import auth as auth_router
from agent_gateway.routers import gateway

log = logging.getLogger(__name__)


def create_app(
    registry: ToolRegistry | None = None,
    rate_limiter: RateLimiter | None = None,
    audit: AuditSink | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        app.state.settings = settings
        if settings.env != "local" and settings.jwt_secret == DEV_INSECURE_SECRET:
            log.warning("GATEWAY_JWT_SECRET is the insecure default in a non-local env!")

        reg = registry
        if reg is None:  # production path: discover upstreams
            reg = ToolRegistry(settings.upstreams)
            await reg.refresh()
        app.state.registry = reg

        redis_client = None
        rl = rate_limiter
        if rl is None:
            rl, redis_client = await build_rate_limiter(settings)
        app.state.rate_limiter = rl

        pg_pool = None
        sink = audit
        if sink is None:
            sink, pg_pool = await build_audit(settings)
        app.state.audit = sink

        try:
            yield
        finally:
            if redis_client is not None:
                await redis_client.aclose()
            if pg_pool is not None:
                await pg_pool.close()

    app = FastAPI(
        title="Agent Gateway",
        version=__version__,
        summary="An MCP gateway with auth, observability, and human-in-the-loop approval.",
        lifespan=lifespan,
    )
    app.include_router(gateway.router)
    app.include_router(auth_router.router)
    app.include_router(admin_router.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness probe. Proves the process is up and serving — nothing more."""
        return {"status": "ok", "version": __version__}

    return app


# Module-level instance for `uvicorn agent_gateway.main:app`.
app = create_app()
