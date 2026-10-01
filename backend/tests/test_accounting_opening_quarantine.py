"""Public old opening routes cannot reach the retained internal engines."""
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

import accounting_financial_accounts as canonical
import accounting_module_opening_balances as historical


class NoDatabase:
    def __getattr__(self, name):
        raise AssertionError("Quarantine must not read or mutate database: " + name)


async def owner():
    return {"id": "synthetic-owner", "role": "owner",
            "accounting_permissions": list(canonical.PERMISSIONS.values())}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/accounting-module/opening-balances/preview",
    "/accounting-module/opening-balances/approve",
    "/accounting-module/opening-balances/activate",
    "/accounting-module/financial-accounts/opening-balances/drafts",
    *["/accounting-module/financial-accounts/opening-balances/drafts/synthetic/" + action
      for action in ("preview", "review", "post", "reverse")],
])
async def test_registered_old_mutations_are_quarantined_without_engine_or_database(path, monkeypatch):
    async def actor(db, user):
        return await owner()
    monkeypatch.setattr(canonical, "fresh_actor", actor)
    monkeypatch.setattr(historical, "fresh_actor", actor)
    router = APIRouter()
    historical.install_opening_balance_routes(router, NoDatabase(), owner)
    engines = canonical.install_financial_account_routes(router, NoDatabase(), owner)
    assert set(engines) == {"create", "preview", "review", "post", "reverse", "transition"}
    public = {route.endpoint for route in router.routes}
    assert not public.intersection(engines.values())
    app = FastAPI()
    app.include_router(router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api" + path, json={"attempt": "bypass", "role": "owner"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "opening_onboarding_required"


@pytest.mark.asyncio
async def test_public_activation_transition_is_locked_without_database(monkeypatch):
    async def actor(db, user):
        return await owner()
    monkeypatch.setattr(canonical, "fresh_actor", actor)
    router = APIRouter()
    canonical.install_financial_account_routes(router, NoDatabase(), owner)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/accounting-module/financial-accounts/transition", json={
            "target": "v2_active", "expected_revision": 0, "activation_ref": "synthetic-reference"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "onboarding_activation_locked"


def test_retained_historical_evidence_and_setup_routes_have_no_duplicate_mutations():
    router = APIRouter()
    historical.install_opening_balance_routes(router, NoDatabase(), owner)
    canonical.install_financial_account_routes(router, NoDatabase(), owner)
    signatures = [(route.path, method) for route in router.routes for method in route.methods]
    assert len(signatures) == len(set(signatures))
    assert ("/accounting-module/opening-balances", "GET") in signatures
    assert ("/accounting-module/financial-accounts/opening-balances/drafts", "GET") in signatures
    assert ("/accounting-module/financial-accounts/opening-balances/evidence", "POST") in signatures


def test_legacy_report_pages_are_visibly_diagnostic():
    root = Path(__file__).resolve().parents[2]
    for name in ("FinancialPosition", "FinancialPositionLedger"):
        source = (root / "frontend/src/pages" / (name + ".jsx")).read_text(encoding="utf-8")
        assert "LEGACY" in source
    source = (root / "backend/universal_accounting_routes.py").read_text(encoding="utf-8")
    assert '\"report_scope\": \"LEGACY\"' in source
    assert '\"mz2_authoritative\": False' in source


@pytest.mark.asyncio
async def test_production_router_registration_cannot_shadow_quarantine(monkeypatch):
    import accounting_atomic
    import accounting_write_control
    from financial_provider_apps import make_financial_provider_apps_router

    async def actor(db, user):
        return await owner()

    async def isolated_transaction(db, merchant, operation):
        # Actual pause serialization has its own Mongo contracts. This test
        # exercises assembled public routing with no database access permitted.
        return await operation(db)

    monkeypatch.setattr(canonical, "fresh_actor", actor)
    monkeypatch.setattr(historical, "fresh_actor", actor)
    monkeypatch.setattr(accounting_write_control, "fresh_actor", actor)
    monkeypatch.setattr(accounting_atomic, "atomic_owner", isolated_transaction)
    app = FastAPI()
    router = make_financial_provider_apps_router(NoDatabase(), owner)
    quarantines = [route for route in router.routes if route.endpoint.__name__ == "quarantined_opening"]
    assert len(quarantines) == 8
    for route in quarantines:
        assert len([other for other in router.routes if other.path == route.path
                    and "POST" in other.methods]) == 1
    app.include_router(router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for route in quarantines:
            path = "/api" + route.path.replace("{draft_id}", "synthetic")
            response = await client.post(path, json={})
            assert response.status_code == 409, (path, response.text)
            assert response.json()["detail"]["code"] == "opening_onboarding_required"
