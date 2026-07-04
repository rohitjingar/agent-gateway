"""Phase 5: tracing setup is safe and a true no-op when disabled."""

from __future__ import annotations

from agent_gateway.config import Settings
from agent_gateway.telemetry import get_tracer, setup_tracing


def test_setup_tracing_is_noop_when_disabled():
    setup_tracing(Settings(otel_enabled=False))  # must not raise, must not install a provider
    tracer = get_tracer()
    with tracer.start_as_current_span("unit") as span:
        span.set_attribute("k", "v")  # no provider -> no-op, must not raise
