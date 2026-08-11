from integrations.qoyod.orders_owner import orders_owner_id

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def test_qoyod_uses_store_owner_for_employee_salla_access():
    assert orders_owner_id({
        "id": "employee-1",
        "role": "operations",
        "created_by": "owner-1",
    }) == "owner-1"


def test_qoyod_keeps_owner_actor_as_orders_owner():
    assert orders_owner_id({
        "id": "owner-1",
        "role": "owner",
    }) == "owner-1"


@pytest.mark.asyncio
async def test_salla_status_picker_uses_store_owner_not_qoyod_tenant(
    monkeypatch,
):
    from integrations.qoyod import routes as qroutes

    called_with = []

    async def fake_call_salla(db, user_id, method, path):
        called_with.append((user_id, method, path))
        return {
            "data": [{
                "id": 1,
                "slug": "completed",
                "name": "مكتمل",
                "type": "system",
            }],
        }

    async def current_user():
        return {
            "id": "employee-1",
            "role": "operations",
            "created_by": "owner-1",
        }

    monkeypatch.setattr(qroutes, "call_salla", fake_call_salla)
    app = FastAPI()
    app.include_router(
        qroutes.make_qoyod_router(object(), current_user),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/integrations/qoyod/salla-order-statuses")

    assert response.status_code == 200
    assert response.json()["statuses"][0]["slug"] == "completed"
    assert called_with == [
        ("owner-1", "GET", "/orders/statuses"),
    ]
