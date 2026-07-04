"""FastAPI application entrypoint for the Agent Gateway.

`create_app` is a factory so tests can build isolated instances and inject a
fake registry. The lifespan builds the tool registry once at startup (discovering
upstream MCP servers) and stores it on `app.state`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_gateway import __version__
from agent_gateway.config import get_settings
from agent_gateway.registry import ToolRegistry
from agent_gateway.routers import gateway


def create_app(registry: ToolRegistry | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        reg = registry
        if reg is None:  # production path: build from config + discover upstreams
            settings = get_settings()
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

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness probe. Proves the process is up and serving — nothing more."""
        return {"status": "ok", "version": __version__}

    return app


# Module-level instance for `uvicorn agent_gateway.main:app`.
app = create_app()
