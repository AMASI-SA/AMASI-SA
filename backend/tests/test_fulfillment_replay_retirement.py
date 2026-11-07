from fastapi import FastAPI
from fastapi.testclient import TestClient

from fulfillment_experiment_routes import make_fulfillment_experiment_router


def test_retired_replay_cannot_read_or_mutate_operational_data():
    class NoDatabaseAccess:
        def __getitem__(self, name):
            raise AssertionError(f"Retired endpoint accessed {name}")

    app = FastAPI()
    app.include_router(make_fulfillment_experiment_router(
        NoDatabaseAccess(), lambda: {"id": "owner", "role": "owner"},
    ), prefix="/api")
    with TestClient(app) as client:
        assert client.get("/api/fulfillment-experiments-v1/orders/123").status_code == 404
        assert client.post("/api/fulfillment-experiments-v1/orders/123/reset", json={
            "confirmation": "RESET 123",
        }).status_code == 404
    routes = set(app.openapi()["paths"])
    assert "/api/fulfillment-experiments-v1/orders/{order_number}/holds" in routes
    assert "/api/fulfillment-experiments-v1/holds/{hold_id}/release" in routes
