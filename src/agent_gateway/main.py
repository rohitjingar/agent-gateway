"""FastAPI application entrypoint for the Agent Gateway.

`create_app` is a factory so tests can build isolated instances and inject fakes
(registry, rate limiter, audit sink). The lifespan builds the real collaborators
from settings, degrades gracefully if Redis/Postgres are absent, and closes them
on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent_gateway import __version__, telemetry
from agent_gateway.approvals import ApprovalStore, build_approval_store
from agent_gateway.audit import AuditSink, build_audit
from agent_gateway.config import DEV_INSECURE_SECRET, get_settings
from agent_gateway.policy import LivePolicy, build_policy, reload_into
from agent_gateway.rate_limit import RateLimiter, build_rate_limiter
from agent_gateway.registry import ToolRegistry
from agent_gateway.routers import admin as admin_router
from agent_gateway.routers import auth as auth_router
from agent_gateway.routers import gateway

log = logging.getLogger(__name__)


def _check_production_safety(settings) -> None:
    """Refuse to start on insecure config when env == 'production' (fail closed)."""
    if settings.env != "production":
        return
    problems = []
    if settings.jwt_algorithm == "HS256":
        if settings.jwt_secret == DEV_INSECURE_SECRET:
            problems.append("JWT secret is the insecure default (set GATEWAY_JWT_SECRET)")
        elif len(settings.jwt_secret) < 32:
            problems.append("JWT secret is shorter than 32 bytes")
    if settings.dev_auth:
        problems.append("dev token endpoint is enabled (set GATEWAY_DEV_AUTH=false)")
    if problems:
        raise RuntimeError("insecure production config: " + "; ".join(problems))


def create_app(
    registry: ToolRegistry | None = None,
    rate_limiter: RateLimiter | None = None,
    audit: AuditSink | None = None,
    approvals: ApprovalStore | None = None,
    policy: LivePolicy | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        app.state.settings = settings
        _check_production_safety(settings)
        if settings.env != "local" and settings.jwt_secret == DEV_INSECURE_SECRET:
            log.warning("GATEWAY_JWT_SECRET is the insecure default in a non-local env!")

        # Apply DB migrations on startup (best-effort; build_* also self-heal in
        # dev, so a missing DB degrades gracefully instead of crashing).
        try:
            from agent_gateway.migrate import run_migrations

            await run_migrations(settings.database_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("migrations skipped (%s)", exc)

        # Policy (servers + roles + high-risk) from the DB, seeded from code
        # defaults; read-only fallback if the DB is unreachable.
        policy_pool = None
        pol = policy
        repo = None
        if pol is None:
            pol, repo, policy_pool = await build_policy(settings)
        app.state.policy = pol
        app.state.policy_repo = repo

        reg = registry
        if reg is None:  # discover the tools of the policy's servers
            reg = ToolRegistry(pol.servers, timeout=settings.upstream_timeout_seconds)
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

        approval_pool = None
        store = approvals
        if store is None:
            store, approval_pool = await build_approval_store(settings)
        app.state.approvals = store

        # Multi-instance policy sync: when one instance edits config it PUBLISHes a
        # reload; every instance SUBSCRIBEs and reloads its snapshot. A periodic
        # reload is the fallback when Redis pub/sub is unavailable.
        sync_client = None
        sync_tasks: list[asyncio.Task] = []
        app.state.policy_sync = None
        if repo is not None:

            async def _do_reload() -> None:
                try:
                    await reload_into(app)
                except Exception:  # noqa: BLE001
                    log.exception("policy reload failed")

            async def _periodic() -> None:
                while True:
                    await asyncio.sleep(settings.policy_reload_interval_seconds)
                    await _do_reload()

            sync_tasks.append(asyncio.create_task(_periodic()))
            try:
                import redis.asyncio as redis_async

                sync_client = redis_async.from_url(settings.redis_url)
                await sync_client.ping()
                app.state.policy_sync = sync_client

                async def _subscribe(client) -> None:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(settings.policy_reload_channel)
                    async for msg in pubsub.listen():
                        if msg.get("type") == "message":
                            await _do_reload()

                sync_tasks.append(asyncio.create_task(_subscribe(sync_client)))
            except Exception as exc:  # noqa: BLE001
                log.warning("policy pub/sub unavailable (%s); periodic reload only", exc)

        try:
            yield
        finally:
            for task in sync_tasks:
                task.cancel()
            if sync_client is not None:
                await sync_client.aclose()
            if redis_client is not None:
                await redis_client.aclose()
            if pg_pool is not None:
                await pg_pool.close()
            if approval_pool is not None:
                await approval_pool.close()
            if policy_pool is not None:
                await policy_pool.close()

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

    @app.get("/ready", tags=["ops"])
    async def ready(request: Request):
        """Readiness probe: are our dependencies actually reachable? 200 or 503."""
        checks: dict[str, object] = {}
        ok = True

        repo = getattr(request.app.state, "policy_repo", None)
        if repo is not None:
            try:
                await repo.ping()
                checks["database"] = "ok"
            except Exception as exc:  # noqa: BLE001
                checks["database"] = f"error: {exc}"
                ok = False
        else:
            checks["database"] = "not_configured"

        rl = request.app.state.rate_limiter
        if hasattr(rl, "ping"):
            try:
                await rl.ping()
                checks["redis"] = "ok"
            except Exception as exc:  # noqa: BLE001
                checks["redis"] = f"error: {exc}"
                ok = False
        else:
            checks["redis"] = "not_configured"

        checks["tools"] = len(request.app.state.registry.list())
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "checks": checks})

    # Tracing is set up at construction time (before serving) so the FastAPI
    # ASGI middleware is in the stack. No-op unless GATEWAY_OTEL_ENABLED=true.
    otel_settings = get_settings()
    telemetry.setup_tracing(otel_settings)
    if otel_settings.otel_enabled:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
        except Exception:  # noqa: BLE001
            log.exception("FastAPI instrumentation failed")

    return app


# Module-level instance for `uvicorn agent_gateway.main:app`.
app = create_app()
