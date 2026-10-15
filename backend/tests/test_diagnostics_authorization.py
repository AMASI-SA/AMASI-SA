from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import runtime_diagnostics_routes as diagnostics_routes


def _app(monkeypatch):
    app = FastAPI()
    client = SimpleNamespace(
        options=SimpleNamespace(pool_options=SimpleNamespace(max_pool_size=5))
    )
    diagnostics_routes.attach_diagnostics_routes(
        app, mongo_client=client,
        state=lambda: {"readiness": "ready", "startup_phase": "ready"},
    )
    monkeypatch.setenv("INTERNAL_DIAGNOSTICS_TOKEN", "test-secret")
    return app


def test_unauthenticated_diagnostics_is_forbidden(monkeypatch):
    with TestClient(_app(monkeypatch)) as client:
        assert client.get("/health/diagnostics").status_code == 403


def test_authorized_diagnostics_is_database_free(monkeypatch):
    calls = 0

    def local_diagnostics(*, mongo_client):
        nonlocal calls
        calls += 1
        return {"mongo": {"configured_max_pool_size": 5}}

    monkeypatch.setattr(diagnostics_routes, "diagnostics", local_diagnostics)
    with TestClient(_app(monkeypatch)) as client:
        response = client.get(
            "/api/health/diagnostics",
            headers={"X-Mezan-Diagnostics-Token": "test-secret"},
        )
    assert response.status_code == 200
    assert response.json()["mongo"]["configured_max_pool_size"] == 5
    assert calls == 1  # no auth DB call or other Mongo operation
