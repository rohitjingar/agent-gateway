"""Prometheus metrics, exposed at /metrics.

Labels are kept low-cardinality on purpose (tools/roles are a bounded set here);
in a bigger system you'd watch label cardinality carefully.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

TOOL_CALLS = Counter(
    "gateway_tool_calls_total",
    "Tool calls, by tool / outcome / role",
    ["tool", "outcome", "role"],
)
TOOL_LATENCY = Histogram(
    "gateway_tool_call_latency_seconds",
    "Tool-call latency in seconds, by tool",
    ["tool"],
)
APPROVALS = Counter(
    "gateway_approvals_total",
    "Approval lifecycle events",
    ["action"],  # queued | approved | denied
)


def record_tool_call(tool: str, outcome: str, role: str, latency_ms: float) -> None:
    TOOL_CALLS.labels(tool=tool, outcome=outcome, role=role).inc()
    TOOL_LATENCY.labels(tool=tool).observe(latency_ms / 1000.0)


def record_approval(action: str) -> None:
    APPROVALS.labels(action=action).inc()


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
