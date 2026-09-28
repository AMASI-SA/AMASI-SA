"""Authority/tenant tests use only in-memory data; no application startup."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from mongomock_motor import AsyncMongoMockClient

from employee_lookup_diagnostic_routes import make_employee_lookup_diagnostic_router
from security_sensitive_routes import require_qoyod_security_owner, require_product_permission


@pytest.mark.asyncio
@pytest.mark.parametrize("role,actor_id,link,expected", [
    ("owner", "owner-a", "owner-a", 200),
    ("owner", "owner-b", "owner-a", 403),
    ("employee", "owner-a", "owner-a", 403),
    ("admin", "owner-a", "owner-a", 403),
    ("owner", "owner-a", None, 403),
    ("owner", "owner-a", {"$ne": None}, 403),
])
async def test_qoyod_explicit_owner_authority_is_read_only(role, actor_id, link, expected):
    users = SimpleNamespace(find_one=AsyncMock(return_value={"id": actor_id, "role": role}))
    settings = SimpleNamespace(find_one=AsyncMock(return_value={} if link is None else {"security_owner_id": link}))
    db = SimpleNamespace(users=users, qoyod_settings=settings)
    if expected == 403:
        with pytest.raises(HTTPException) as failure:
            await require_qoyod_security_owner(db, {"id": actor_id, "role": "owner", "is_owner": True})
        assert failure.value.status_code == 403
    else:
        result = await require_qoyod_security_owner(db, {"id": actor_id})
        assert result["id"] == actor_id
    users.find_one.assert_awaited_once()
    if settings.find_one.await_count:
        assert settings.find_one.call_args.args == (
            {"user_id": "main"}, {"_id": 0, "security_owner_id": 1})
    # These doubles expose reads only; an attempted write would fail.


@pytest.mark.asyncio
async def test_native_owner_context_cannot_elevate_employee():
    db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value={
        "id": "employee-a", "role": "employee", "created_by": "owner-a"})))
    with pytest.raises(HTTPException) as failure:
        await require_qoyod_security_owner(db, {"id": "owner-a", "role": "owner", "_mobile_actor_id": "employee-a"})
    assert failure.value.status_code == 403
    assert db.users.find_one.call_args.args[0] == {"id": "employee-a"}


@pytest.mark.asyncio
async def test_product_permission_is_tenant_scoped():
    db = AsyncMongoMockClient().offline
    await db.users.insert_one({"id": "employee-a", "role": "employee", "created_by": "owner-a"})
    from ai_store_access_contract import ROLE_ASSIGNMENTS
    await db[ROLE_ASSIGNMENTS].insert_one({
        "user_id": "employee-a", "owner_user_id": "owner-b", "enabled": True,
        "extra_permissions": ["products.cost.write"],
    })
    with pytest.raises(HTTPException):
        await require_product_permission(db, {"id": "employee-a"}, "products.cost.write")
    await db[ROLE_ASSIGNMENTS].insert_one({
        "user_id": "employee-a", "owner_user_id": "owner-a", "enabled": True,
        "extra_permissions": ["products.cost.write"],
    })
    assert await require_product_permission(db, {"id": "employee-a"}, "products.cost.write") == "owner-a"


async def employee_client(db, actor):
    async def current():
        if actor is None:
            raise HTTPException(401, "authentication_required")
        return actor
    app = FastAPI()
    app.include_router(make_employee_lookup_diagnostic_router(db, current))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline.test")


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,status", [
    (None, 401), ({"id": "employee-a", "role": "employee"}, 403),
    ({"id": "admin-a", "role": "admin"}, 403),
])
async def test_employee_diagnostic_denies_unprivileged(actor, status):
    db = AsyncMongoMockClient().offline
    if actor:
        await db.users.insert_one(actor)
    async with await employee_client(db, actor) as client:
        response = await client.get("/audit/employee-lookup?entity_id=target")
    assert response.status_code == status


@pytest.mark.asyncio
async def test_employee_diagnostic_no_cross_tenant_or_unbounded_metadata():
    db = AsyncMongoMockClient().offline
    await db.users.insert_one({"id": "owner-a", "role": "owner"})
    await db.employees.insert_many([
        {"id": "target", "user_id": "owner-a", "name": "intended", "metadata": {"token": "PRIVATE_META"}, "bank_iban": "PRIVATE_BANK"},
        {"id": "target", "user_id": "owner-b", "name": "PRIVATE_OTHER_TENANT"},
        {"id": "target", "name": "PRIVATE_ORPHAN"},
    ])
    async with await employee_client(db, {"id": "owner-a", "role": "owner"}) as client:
        response = await client.get("/audit/employee-lookup?entity_id=target")
    assert response.status_code == 200
    assert "intended" in response.text
    assert "PRIVATE_" not in response.text
    assert len(response.json()["section_1_id_hits_by_collection"]["employees"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role,expected", [("owner", 200), ("employee", 403), ("admin", 403)])
async def test_refund_repair_authority_precedes_mutation(monkeypatch, role, expected):
    from fastapi import APIRouter
    import bnpl.auto_sync_routes as module
    db = AsyncMongoMockClient().offline
    await db.users.insert_one({"id": "actor", "role": role})
    mutation = AsyncMock(side_effect=RuntimeError("PRIVATE_DRIVER_TRACE /srv/private token=synthetic-only"))
    monkeypatch.setattr(module, "_propagate_refunds_to_unified", mutation)
    async def current():
        return {"id": "actor", "role": "owner"}
    app, router = FastAPI(), APIRouter()
    module.attach_bnpl_auto_sync_routes(router, db=db, get_current_user=current)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline.test") as client:
        response = await client.post("/bnpl/auto-sync/fix-unified-refunds")
    assert response.status_code == expected
    assert "PRIVATE_" not in response.text
    if expected == 200:
        assert response.json() == {"success": False, "error": "operation_failed"}
        mutation.assert_awaited_once_with(db, "actor", "tabby")
    else:
        mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_employee_probe_failures_do_not_return_exception_details(monkeypatch):
    import employee_lookup_diagnostic_routes as module
    db = AsyncMongoMockClient().offline
    await db.users.insert_one({"id": "owner-a", "role": "owner"})
    monkeypatch.setattr(module, "_safe_find", AsyncMock(side_effect=RuntimeError("PRIVATE_DRIVER_TRACE")))
    async with await employee_client(db, {"id": "owner-a"}) as client:
        response = await client.get("/audit/employee-lookup?entity_id=target")
    assert response.status_code == 200
    assert "PRIVATE_" not in response.text
    assert set(response.json()["probe_errors"]) == {"diagnostic_failed"}

