"""Synthetic HTTP acceptance of owner-confirmed discovered method commissions."""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest
import pytest_asyncio

from accounting_module_contract import ACCOUNTING_PERMISSION_KEYS
from accounting_onboarding import install_onboarding_routes
from payment_methods import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION


BASE = "/accounting-module/onboarding"


@pytest_asyncio.fixture
async def setup_api():
    db = AsyncMongoMockClient().payment_setup_http_synthetic
    await db.users.insert_many([
        {"id": "owner-a", "role": "owner", "is_active": True},
        {"id": "owner-b", "role": "owner", "is_active": True},
        {"id": "staff-a", "role": "employee", "created_by": "owner-a",
         "accounting_permissions": sorted(ACCOUNTING_PERMISSION_KEYS), "is_active": True},
    ])
    for owner in ("owner-a", "owner-b"):
        await db.settings.insert_one({
            "user_id": owner, "payment_methods": deepcopy(DEFAULT_PAYMENT_METHODS),
            "payment_fee_defaults_version": PAYMENT_FEE_DEFAULTS_VERSION,
            "shipping_companies": [{"name": "synthetic-carrier", "cost_per_order": 17}],
            "dashboard_hidden_cards": ["synthetic-preference"],
        })
    await db.unified_orders.insert_many([
        {"user_id": "owner-a", "order_number": "synthetic-a", "payment_method": "paypal"},
        {"user_id": "owner-b", "order_number": "synthetic-b", "payment_method": "other_gateway"},
    ])

    async def current_user(request: Request):
        # A stale/client role claim cannot authorize this route; the onboarding
        # actor resolver must reload the actual persisted user on every request.
        return {"id": request.headers.get("x-user", "owner-a"), "role": "owner"}

    app = FastAPI()
    router = APIRouter()
    install_onboarding_routes(router, db, current_user, {})
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(db=db, client=client)


async def candidates(env, user="owner-a"):
    response = await env.client.get(BASE + "/fee-policy-candidates", headers={"x-user": user})
    assert response.status_code == 200, response.text
    return response.json()


def commission(setup, **changes):
    return {"name": "paypal", "commission_percent": "3.4", "fixed_fee": "1.50",
            "vat_percent": "15", "expected_revision": setup["revision"], **changes}


@pytest.mark.asyncio
async def test_discovery_reuses_existing_candidates_and_save_changes_only_fee_settings(setup_api):
    env = setup_api
    before = await env.db.settings.find_one({"user_id": "owner-a"})
    body = await candidates(env)
    assert "items" in body and "conflicts" in body
    setup = body["payment_method_setup"]
    assert setup["complete"] is True
    assert [item["name"] for item in setup["items"] if item["status"] == "needs_commission"] == ["paypal"]
    assert await env.db.settings.find_one({"user_id": "owner-a"}) == before

    saved = await env.client.put(BASE + "/payment-method-commission", json=commission(setup))
    assert saved.status_code == 200, saved.text
    updated = saved.json()["payment_method_setup"]
    paypal = next(item for item in updated["items"] if item["name"] == "paypal")
    assert paypal["status"] == "configured"
    assert updated["pending_count"] == 0
    after = await env.db.settings.find_one({"user_id": "owner-a"})
    assert after["shipping_companies"] == before["shipping_companies"]
    assert after["dashboard_hidden_cards"] == before["dashboard_hidden_cards"]
    assert all(item in after["payment_methods"] for item in before["payment_methods"])
    assert await env.db.mz2_provider_fee_policies_v2.count_documents({}) == 0
    assert await env.db.accounts.count_documents({}) == 0
    assert await env.db.account_transactions.count_documents({}) == 0


@pytest.mark.asyncio
async def test_fresh_persisted_role_denies_employee_even_with_accounting_permissions(setup_api):
    env = setup_api
    setup = (await candidates(env))["payment_method_setup"]
    before = await env.db.settings.find_one({"user_id": "owner-a"})
    response = await env.client.put(BASE + "/payment-method-commission", headers={"x-user": "staff-a"},
                                    json=commission(setup))
    assert response.status_code == 403, response.text
    assert await env.db.settings.find_one({"user_id": "owner-a"}) == before
    # Existing fee-policy suggestion access stays independent of this new write.
    assert "payment_method_setup" not in await candidates(env, "staff-a")


@pytest.mark.asyncio
async def test_another_owner_cannot_configure_an_unobserved_foreign_method(setup_api):
    env = setup_api
    setup = (await candidates(env, "owner-b"))["payment_method_setup"]
    assert "paypal" not in {item["name"] for item in setup["items"]}
    before = await env.db.settings.find_one({"user_id": "owner-b"})
    response = await env.client.put(BASE + "/payment-method-commission", headers={"x-user": "owner-b"},
                                    json=commission(setup))
    assert response.status_code in (404, 409), response.text
    assert await env.db.settings.find_one({"user_id": "owner-b"}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"commission_percent": ""}, {"commission_percent": None}, {"fixed_fee": ""},
    {"vat_percent": None}, {"commission_percent": "101"}, {"fixed_fee": "-1"},
])
async def test_http_validation_never_accepts_missing_or_invalid_terms(setup_api, invalid):
    env = setup_api
    setup = (await candidates(env))["payment_method_setup"]
    before = await env.db.settings.find_one({"user_id": "owner-a"})
    response = await env.client.put(BASE + "/payment-method-commission", json=commission(setup, **invalid))
    assert response.status_code == 422, response.text
    assert await env.db.settings.find_one({"user_id": "owner-a"}) == before
