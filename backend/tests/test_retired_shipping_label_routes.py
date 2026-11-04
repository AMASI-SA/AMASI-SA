"""Retired entry points cannot fetch, create, change or return any shipping label."""
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import fulfillment_v2_routes as fulfillment
from order_engine import _original_make_order_engine_router


PATHS = [
    "/orders-v2/123/shipping-label",
    "/orders-v2/123/shipping-label/refresh",
    *["/fulfillment-v2/completed/123/carrier-label" + suffix
      for suffix in ("", "/refresh", "/refresh-saved", "/amasi", "/amasi/refresh")],
]


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [True, False])
@pytest.mark.parametrize("path", PATHS)
async def test_retired_label_routes_stop_before_any_database_or_provider_access(path, authenticated, monkeypatch):
    class UnusedCollection:
        def __getattr__(self, operation):
            pytest.fail("Retired endpoint attempted a database operation: " + operation)

    class UnusedDatabase:
        unified_orders = UnusedCollection()

        def __getattr__(self, name):
            return UnusedCollection()

        __getitem__ = __getattr__

    async def user():
        if not authenticated:
            raise HTTPException(401)
        return {"id": "owner", "role": "owner"}

    def forbid(*args, **kwargs):
        pytest.fail("Retired endpoint called Salla")

    monkeypatch.setattr("order_engine.shipping_label_service.call_salla", forbid)
    monkeypatch.setattr("order_engine.amasi_shipping_compat.call_salla", forbid)
    db = UnusedDatabase()
    app = FastAPI()
    app.include_router(fulfillment.make_fulfillment_v2_router(db, user))
    app.include_router(_original_make_order_engine_router(db, user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path)
    assert response.status_code == (410 if authenticated else 401)
    if authenticated:
        assert response.json()["detail"] == {"code": "shipping_label_route_retired"}
