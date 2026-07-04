"""FastAPI application entrypoint for the Agent Gateway.

`create_app` is a factory so tests can build isolated instances and inject a
fake registry. The lifespan loads settings, warns on an insecure secret outside
local, and builds the tool registry (discovering upstream MCP servers) once.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_gateway import __version__
from agent_gateway.config import DEV_INSECURE_SECRET, get_settings
from agent_gateway.registry import ToolRegistry
from agent_gateway.routers import auth as auth_router
from agent_gateway.routers import gateway

log = logging.getLogger(__name__)


def create_app(registry: ToolRegistry | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        app.state.settings = settings
        if settings.env != "local" and settings.jwt_secret == DEV_INSECURE_SECRET:
            log.warning("GATEWAY_JWT_SECRET is the insecure default in a non-local env!")

        reg = registry
        if reg is None:  # production path: build from config + discover upstreams
            reg = ToolRegistry(settings.upstreams)
            await reg.refresh()
        app.state.registry = reg
        yield

    app = FastAPI(
        title="Agent Gateway",
        version=__version__,
        summary="An MCP gateway with auth, observability, and human-in-the-loop approval.",
        lifespan=lifespan,
    )
    app.include_router(gateway.router)
    app.include_router(auth_router.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness probe. Proves the process is up and serving — nothing more."""
        return {"status": "ok", "version": __version__}

    return app


# Module-level instance for `uvicorn agent_gateway.main:app`.
app = create_app()
