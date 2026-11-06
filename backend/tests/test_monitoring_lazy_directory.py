"""Monitoring entry reads identities only; no piece/order/delivery hydration."""
import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

import mobile_operations_monitoring_routes as monitoring


@pytest.mark.asyncio
async def test_monitoring_directories_do_not_read_operational_data(monkeypatch):
    db = AsyncMongoMockClient().lazy_monitoring
    await db[monitoring.EMPLOYEES].insert_many([
        {"user_id": "owner", "id": "employee", "account_user_id": "login", "display_name": "Synthetic worker", "status": "active"},
        {"user_id": "foreign", "id": "foreign", "account_user_id": "foreign-login", "display_name": "Foreign"},
    ])
    await db[monitoring.MOBILE_APP_ACCESS].insert_one({
        monitoring.MOBILE_APP_ACCESS_OWNER_FIELD: "owner", "user_id": "login", "enabled": True,
        "permissions": ["app.page.my_products"],
    })
    await db[monitoring.STORE_DRIVERS].insert_one({"user_id": "owner", "id": "driver", "name": "Synthetic courier"})
    async def access(*_args):
        return {"permissions": [monitoring.MONITORING_PERMISSION]}
    monkeypatch.setattr(monitoring, "mobile_app_access_for_user", access)
    class IdentitiesOnly:
        def __getitem__(self, name):
            assert name in {monitoring.EMPLOYEES, monitoring.MOBILE_APP_ACCESS, monitoring.STORE_DRIVERS, "users"}, name
            return db[name]
        def __getattr__(self, name):
            return self[name]
    app = FastAPI()
    app.include_router(monitoring.make_mobile_operations_monitoring_router(IdentitiesOnly(), lambda: {"id": "owner", "role": "owner"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        prep = await http.get("/mobile/operations-monitoring/preparation", params={"directory_only": "true"})
        assert prep.status_code == 200, prep.text
        assert prep.json()["employees"] == [{"employee_id": "employee", "employee_name": "Synthetic worker", "job_title": None, "department": None, "status": "active"}]
        courier = await http.get("/mobile/operations-monitoring/couriers", params={"directory_only": "true"})
        assert courier.status_code == 200, courier.text
        assert courier.json()["couriers"] == [{"driver_id": "driver", "driver_name": "Synthetic courier", "phone": None, "city": None, "status": None}]


@pytest.mark.asyncio
async def test_monitoring_directory_keeps_permission_and_owner_scope(monkeypatch):
    async def access(*_args):
        return {"permissions": []}
    monkeypatch.setattr(monitoring, "mobile_app_access_for_user", access)
    class NoReads:
        def __getitem__(self, name):
            raise AssertionError(name)
    app = FastAPI()
    app.include_router(monitoring.make_mobile_operations_monitoring_router(NoReads(), lambda: {"id": "worker", "created_by": "owner"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        for path in ("preparation", "couriers"):
            response = await http.get("/mobile/operations-monitoring/" + path, params={"directory_only": "true"})
            assert response.status_code == 403
