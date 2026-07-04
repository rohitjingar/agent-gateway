"""FastAPI application entrypoint for the Agent Gateway.

We use an application *factory* (`create_app`) rather than a bare module-level
app so tests can build a fresh, fully-isolated app instance, and so wiring
(routers, middleware, lifespan) has one obvious place to live as the gateway grows.
"""

from fastapi import FastAPI

from agent_gateway import __version__


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Gateway",
        version=__version__,
        summary="An MCP gateway with auth, observability, and human-in-the-loop approval.",
    )

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness probe. Proves the process is up and serving — nothing more."""
        return {"status": "ok", "version": __version__}

    return app


# Module-level instance for `uvicorn agent_gateway.main:app`.
app = create_app()
