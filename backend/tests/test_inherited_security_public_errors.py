"""Offline fault-injection tests for inherited public error boundaries."""
import importlib
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

CANARY = "db-driver CANARY_PRIVATE token=synthetic-only /srv/private/source.py Traceback customer@example.invalid"


def test_public_error_never_formats_exception_or_unknown_detail():
    from security_public_errors import public_error

    class SensitiveError(Exception):
        def __str__(self):
            raise AssertionError("public boundary must not format exceptions")

    assert public_error(SensitiveError()) == "operation_failed"
    assert public_error(CANARY) == "operation_failed"
    assert public_error({"error": CANARY}) == "operation_failed"
    assert public_error("diagnostic_failed") == "diagnostic_failed"

CASES = [
    ("bnpl.auto_sync_routes", "attach_bnpl_auto_sync_routes", "get_auto_sync_status", "GET", "/bnpl/auto-sync/status"),
    ("bnpl.refund_audit_routes", "attach_bnpl_refund_audit_routes", "_audit_provider", "GET", "/bnpl/refund-audit"),
    ("bnpl.refund_audit_routes", "attach_bnpl_refund_audit_routes", "_diagnose_provider_delta", "GET", "/bnpl/refund-audit/diagnose/tabby"),
    ("bnpl.settlements_routes", "attach_bnpl_settlements_routes", "compute_all_settlements", "GET", "/bnpl/settlements/summary"),
    ("bnpl.settlements_routes", "attach_bnpl_settlements_routes", "_compute_period_items", "GET", "/bnpl/settlements/items/tabby?from=2026-01-01&to=2026-01-31"),
    ("bnpl.settlements_routes", "attach_bnpl_settlements_routes", "compute_settlement_for_provider", "GET", "/bnpl/settlements/tabby"),
    ("bnpl.settlements_routes", "attach_bnpl_settlements_routes", "compute_weekly_settlements", "GET", "/bnpl/settlements/weekly/tabby"),
    ("bnpl.balance_service", "attach_bnpl_settlements_routes", "get_all_bnpl_balances", "GET", "/bnpl/settlements/balances/canonical"),
    ("bnpl.matching_service", "attach_bnpl_settlements_routes", "compute_matches_for_provider", "GET", "/bnpl/settlements/matching/tabby"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name,attach,helper,method,path", CASES)
async def test_nested_failures_are_public_codes(monkeypatch, module_name, attach, helper, method, path):
    module = importlib.import_module(module_name)
    failure = AsyncMock(side_effect=RuntimeError(CANARY))
    monkeypatch.setattr(module, helper, failure)
    route_module = (importlib.import_module("bnpl.settlements_routes")
                    if module_name in {"bnpl.balance_service", "bnpl.matching_service"} else module)

    async def actor():
        return {"id": "owner-a", "role": "owner"}

    app, router = FastAPI(), APIRouter()
    getattr(route_module, attach)(router, db=object(), get_current_user=actor)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline.test") as client:
        response = await client.request(method, path)
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["error"] in {"operation_failed", "diagnostic_failed", "provider_operation_failed"}
    assert not any(value in response.text for value in ("CANARY_PRIVATE", "Traceback", "RuntimeError", "customer@", "/srv/private"))
    failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_authentication_precedes_status_helper(monkeypatch):
    module = importlib.import_module("bnpl.auto_sync_routes")
    helper = AsyncMock()
    monkeypatch.setattr(module, "get_auto_sync_status", helper)

    async def anonymous():
        raise HTTPException(401, "authentication_required")

    app, router = FastAPI(), APIRouter()
    module.attach_bnpl_auto_sync_routes(router, db=object(), get_current_user=anonymous)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline.test") as client:
        response = await client.get("/bnpl/auto-sync/status")
    assert response.status_code == 401
    helper.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_success_contract_unchanged(monkeypatch):
    module = importlib.import_module("bnpl.auto_sync_routes")
    monkeypatch.setattr(module, "get_auto_sync_status", AsyncMock(return_value={"enabled": False, "providers": []}))

    async def actor():
        return {"id": "owner-a"}

    app, router = FastAPI(), APIRouter()
    module.attach_bnpl_auto_sync_routes(router, db=object(), get_current_user=actor)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline.test") as client:
        response = await client.get("/bnpl/auto-sync/status")
    assert response.json() == {"success": True, "enabled": False, "providers": [], "interval_seconds": module.SYNC_INTERVAL_SECONDS}


@pytest.mark.asyncio
async def test_status_redacts_persisted_error_without_mutating_settings():
    from mongomock_motor import AsyncMongoMockClient
    module = importlib.import_module("bnpl.auto_sync_routes")
    db = AsyncMongoMockClient().offline
    await db.bnpl_settings.insert_many([
        {"user_id": "owner-a", "provider": "tabby", "enabled": True, "last_auto_sync_error": CANARY},
        {"user_id": "owner-a", "provider": "tamara", "last_auto_sync_error": ""},
        {"user_id": "owner-b", "provider": "tabby", "last_auto_sync_error": "OTHER_TENANT"},
    ])
    before = await db.bnpl_settings.find({}).to_list(10)
    async def actor():
        return {"id": "owner-a"}
    app, router = FastAPI(), APIRouter()
    module.attach_bnpl_auto_sync_routes(router, db=db, get_current_user=actor)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline.test") as client:
        response = await client.get("/bnpl/auto-sync/status")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True and data["disabled"] is True
    assert {p["provider"]: p["last_auto_sync_error"] for p in data["providers"]} == {
        "tabby": "operation_failed", "tamara": ""}
    assert "OTHER_TENANT" not in response.text and "CANARY_PRIVATE" not in response.text
    assert await db.bnpl_settings.find({}).to_list(10) == before
