"""Phase 0 smoke test: the app boots and /health answers.

`TestClient` runs the ASGI app in-process (no real network, no uvicorn) — fast
and deterministic, the standard way to test FastAPI.
"""

from fastapi.testclient import TestClient

from agent_gateway.main import create_app


def test_health_ok() -> None:
    client = TestClient(create_app())
    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
