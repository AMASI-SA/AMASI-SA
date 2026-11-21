"""Actual Mongo caller-path regressions for the guarded unpaid-order cohort."""
from copy import deepcopy
from datetime import date
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

import dashboard_v2_routes as dashboard
import operational_profit_sources as profit
from auth import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION
from awaiting_payment_eligibility import ELIGIBILITY_FIELD
from dashboard_ads_costs import DashboardAdsCostCollector
from operational_payment_evidence import _instant
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS
from tests.test_awaiting_payment_eligibility import fixture as unpaid_fixture
from tests.test_dashboard_bundle_cost_authority import ReadOnlyDB


OWNER = "owner"
START, END = "2026-11-01T00:00:00+03:00", "2026-11-22T00:00:00+03:00"
PARAMS = {"from_date": "2026-11-20", "to_date": "2026-11-21"}


@pytest_asyncio.fixture
async def cohort_app(monkeypatch):
    uri = os.environ.get("OPERATIONAL_TEST_MONGO_URI", "mongodb://127.0.0.1:27305")
    assert uri.startswith("mongodb://127.0.0.1:"), "Only disposable local test Mongo is permitted"
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = client["awaiting_callers_" + uuid4().hex]
    await client.admin.command("ping")
    source, raw = unpaid_fixture("wating", "300000001")
    source.update(currency="SAR", total_amount=100, order_status="under_review", order_date="2026-11-20",
                  products=[{"product_id": "p", "order_item_id": "item-unpaid", "quantity": 1, "total": 100}])
    unpaid = {**source, "raw_by_source": {"salla_direct": raw}}
    paid = deepcopy(unpaid)
    paid.update(order_number="300000002", order_id="native-300000002", payment_method="mada")
    paid["products"][0]["order_item_id"] = "item-paid"
    paid["raw_by_source"]["salla_direct"].update(
        id="native-300000002", reference_id="300000002", payment_method="mada")
    await db.unified_orders.insert_many([deepcopy(unpaid), paid])
    await db.settings.insert_one({"user_id": OWNER, "payment_methods": deepcopy(DEFAULT_PAYMENT_METHODS),
                                 "payment_fee_defaults_version": PAYMENT_FEE_DEFAULTS_VERSION})
    await db[PRODUCTS].insert_one({"user_id": OWNER, "salla_product_id": "p", "name": "Synthetic product"})
    await db[COST_PROFILES].insert_one({"user_id": OWNER, "salla_product_id": "p", "base_cost": 10})
    readonly = ReadOnlyDB(db)

    async def ads(*_args, **_kwargs):
        return {"total": 0, "breakdown": {}, "spend_quality": {"amount_available": True, "amount_complete": True},
                "bank_commissions": {"total_fee_sar": 0}, "source_contract": {},
                "providers": {provider: {"spend": 0, "orders": 0, "revenue": 0, "roas": None}
                              for provider in ("snapchat", "meta", "tiktok", "google")}}

    async def recurring(*_args, **_kwargs):
        return {"total": 0, "rentals_total": 0, "utilities_total": 0, "renewals_total": 0, "by_type": {}}

    async def legacy(**kwargs):
        assert kwargs["allow_self_heal"] is False
        return {"totals": {"total_orders": 2, "total_sales": 100, "total_product_cost": 0, "total_ads_cost": 0,
                           "operating_expenses_total": 0, "operating_salaries_total": 0,
                           "total_payment_fees": 0, "net_profit": 100, "net_sales": 100},
                "payment_breakdown": [], "net_sales_config": {}}

    async def user():
        return {"id": OWNER, "role": "owner"}

    monkeypatch.setattr(dashboard, "_today_riyadh", lambda: date(2026, 11, 21))
    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(dashboard, "compute_recurring_obligations_for_range", recurring)
    app = FastAPI()
    app.include_router(dashboard.make_dashboard_v2_router(readonly, user, legacy, lambda user: None))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield SimpleNamespace(db=db, readonly=readonly, http=http)
    finally:
        await client.drop_database(db.name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("collector_enabled", [False, True])
async def test_native_projection_and_history_guard_preserve_registry_without_db_writes(cohort_app, collector_enabled):
    env = cohort_app
    before = await env.db.unified_orders.find({}, {"_id": 0}).to_list(10)
    collector = DashboardAdsCostCollector() if collector_enabled else None
    rows = await dashboard._filtered_orders(env.readonly, OWNER, **PARAMS,
        payment_methods=None, shipping_companies=None,
        **({"cost_collector": collector} if collector else {}))
    assert len(rows) == 2
    unpaid = next(row for row in rows if row["order_number"] == "300000001")
    assert unpaid[ELIGIBILITY_FIELD]["eligible"] is False
    assert unpaid[ELIGIBILITY_FIELD]["history_checked"] is True
    assert env.readonly.reads[("unified_orders", "find")] == 2
    assert before == await env.db.unified_orders.find({}, {"_id": 0}).to_list(10)


@pytest.mark.asyncio
async def test_dashboard_counts_two_source_orders_but_only_one_financial_sale_and_cost(cohort_app):
    response = await cohort_app.http.get("/dashboard-v2", params=PARAMS)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["totals"]["total_orders"] == result["financial_cohort"]["total_orders"] == 2
    assert result["totals"]["total_sales"] == 100
    assert result["totals"]["total_product_cost"] == 10
    assert result["totals"]["financial_eligible_orders"] == 1
    assert result["totals"]["excluded_unpaid_orders"] == 1
    assert result["financial_cohort"]["complete"] is True
    assert result["month_kpis"]["total_orders"] == 2 and result["month_kpis"]["total_sales"] == 100
    chart = (await cohort_app.http.get("/dashboard-v2/chart-orders", params=PARAMS)).json()
    assert chart["summary"]["orders"] == 2 and chart["summary"]["sales"] == 100


@pytest.mark.asyncio
async def test_profit_reader_keeps_reference_count_and_only_filters_checked_financial_cohort(cohort_app):
    env = cohort_app
    context = await profit._load_context(env.readonly, OWNER, _instant(START), _instant(END))
    assert [row["order_number"] for row in context["orders"]] == ["300000002"]
    assert context["coverage"]["orders"] == context["coverage"]["total_orders"] == 2
    assert context["coverage"]["financial_eligible_orders"] == 1
    assert context["coverage"]["excluded_unpaid_orders"] == 1
    assert context["coverage"]["complete"] is True
    product_cost = await dashboard.build_mezan_v2_product_cost_cooperative(env.readonly, OWNER, context["orders"])
    assert product_cost["total"] == 10


@pytest.mark.asyncio
async def test_prior_shipping_fact_retains_order_and_prevents_final_profit(cohort_app):
    env = cohort_app
    await env.db.operational_balance_states_v1.insert_one({"owner_id": OWNER, "engine": {
        "facts": {"shipping_fee:native-300000001": {"cost": "25.00", "currency": "SAR"}}, "obligations": {}}})
    context = await profit._load_context(env.readonly, OWNER, _instant(START), _instant(END))
    assert len(context["orders"]) == 2
    assert context["coverage"]["excluded_unpaid_orders"] == 0
    assert context["coverage"]["review_orders"] == 1 and not context["coverage"]["complete"]
    result = (await env.http.get("/dashboard-v2", params=PARAMS)).json()
    assert result["totals"]["total_orders"] == 2 and result["totals"]["total_product_cost"] == 20
    assert result["totals"]["net_profit"] is None and not result["financial_cohort"]["complete"]


@pytest.mark.asyncio
async def test_normal_card_refresh_replaces_unpaid_cohort_when_same_native_order_is_paid_emkan(cohort_app):
    env = cohort_app
    await env.db.unified_orders.delete_one({"user_id": OWNER, "order_number": "300000002"})
    path = "/dashboard-v2/cards/top-products"
    previous = (await env.http.get(path, params=PARAMS)).json()
    assert previous["financial_cohort"]["total_orders"] == 1
    assert previous["financial_cohort"]["financial_eligible_orders"] == 0
    assert previous["product_cost_v2"]["total"] == 0
    version = "2026-11-21T10:00:00+00:00"
    await env.db.unified_orders.update_one({"user_id": OWNER, "order_number": "300000001"}, {"$set": {
        "payment_method": "EmkanInstallment", "paid_amount": 100, "payment_collection_status": "paid",
        "g47_salla_snapshot.source_updated_at": version,
        "raw_by_source.salla_direct.updated_at": version,
        "raw_by_source.salla_direct.payment_method": "emkan_installment",
        "raw_by_source.salla_direct.payment_methods": [
            {"payment_method": "emkan_installment", "amount": "100.00", "transaction_reference": "synthetic-capture"}],
        "raw_by_source.salla_direct.is_pending_payment": False,
        "raw_by_source.salla_direct.status": {"slug": "in_progress"},
        "raw_by_source.salla_direct.payment_actions.remaining_action": {
            "paid_amount": {"amount": "100.00", "currency": "SAR"},
            "remaining_amount": {"amount": "0.00", "currency": "SAR"}, "has_remaining_amount": False},
    }})
    result = (await env.http.get(path, params={**PARAMS, "refresh": True})).json()
    assert result["financial_cohort"]["total_orders"] == result["financial_cohort"]["financial_eligible_orders"] == 1
    assert result["financial_cohort"]["excluded_unpaid_orders"] == result["financial_cohort"]["review_orders"] == 0
    assert result["product_cost_v2"]["total"] == 10
    assert result["financial_cohort"]["eligibility_digest"] != previous["financial_cohort"]["eligibility_digest"]
    assert (await env.http.get(path, params=PARAMS)).json() == result
