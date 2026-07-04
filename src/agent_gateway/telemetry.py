"""OpenTelemetry tracing setup.

Traces answer a question logs and metrics can't: for THIS request, where did the
time go across the hop (gateway -> upstream MCP server -> back)? We emit one span
per tool call carrying AI-relevant attributes (tool, role, outcome, latency), and
let FastAPI + httpx auto-instrumentation supply the surrounding HTTP spans. Export
is OTLP over HTTP to Jaeger.

Everything is a no-op unless GATEWAY_OTEL_ENABLED=true: the API's default tracer
is a no-op, so the handler's span code needs no conditionals, and tests / infra-
less dev runs pay nothing.
"""

from __future__ import annotations

import logging

from opentelemetry import trace

log = logging.getLogger(__name__)
_configured = False


def setup_tracing(settings) -> None:
    """Install a real TracerProvider + OTLP exporter. Idempotent; off by default."""
    global _configured
    if _configured or not settings.otel_enabled:
        return

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()  # spans for gateway -> upstream calls
    except Exception as exc:  # noqa: BLE001
        log.warning("httpx instrumentation failed: %s", exc)

    _configured = True
    log.info("OpenTelemetry tracing enabled -> %s", settings.otel_endpoint)


def get_tracer():
    return trace.get_tracer("agent_gateway")
