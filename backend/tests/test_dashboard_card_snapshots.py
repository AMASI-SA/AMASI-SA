"""Independent dashboard cards preserve financial authority and owner scope."""
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from mongomock_motor import AsyncMongoMockClient

import dashboard_v2_routes as dashboard
from auth import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION
from product_cost_revision import bump_product_cost_revision
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS
from tests.test_dashboard_bundle_cost_authority import (
    OWNER as BUNDLE_OWNER,
    ReadOnlyDB,
    db as bundle_db,
    freeze,
    sale,
    sale_line,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["تم التنفيذ", "ملغي", "مسترجع"])
async def test_light_costs_match_full_frozen_missing_and_status_authority(bundle_db, monkeypatch, status):
    order = sale("frozen", sale_line("bundle-line", 4), order_status=status)
    await freeze(bundle_db, order)
    # A current catalog edit must never reprice this frozen bundle.
    await bundle_db[COST_PROFILES].update_one(
        {"user_id": BUNDLE_OWNER, "salla_product_id": "shawl"}, {"$set": {"base_cost": 999}},
    )
    orders = [
        order,
        sale("missing", {"product_id": "unknown", "quantity": 1, "total": 75}),
        sale("no-products"),
        sale("unverified-fx", sale_line("ordinary", product="plain"), currency="USD"),
    ]
    full = await dashboard.build_mezan_v2_product_cost_cooperative(bundle_db, BUNDLE_OWNER, orders)

    async def no_traffic(*args, **kwargs):
        raise AssertionError("light financial computation must not read product traffic")

    monkeypatch.setattr(dashboard, "load_product_traffic_sources", no_traffic)
    light = await dashboard.build_mezan_v2_product_cost_cooperative(
        bundle_db, BUNDLE_OWNER, orders, include_product_rows=False,
    )
    assert light == {key: value for key, value in full.items() if key not in {"product_rows", "product_profit_summary"}}
    assert light["missing_all_cost_products_count"] > 0
    assert light["no_products_orders_count"] == 1
    assert "historical_cost_authority" in light["source_contract"]


def sample_order(owner, number, day, amount, *, payment="mada", shipping="carrier-a", status="تم التنفيذ"):
    return {
        "user_id": owner, "order_number": number, "order_date": day,
        "currency": "SAR", "total_amount": amount, "order_status": status,
        "payment_method": payment, "shipping_company": shipping,
        "products": [{"product_id": "p", "quantity": 1, "total": amount}],
    }


@pytest_asyncio.fixture
async def card_app(monkeypatch):
    db = AsyncMongoMockClient().dashboard_cards_synthetic
    for owner, cost in (("a", 10), ("b", 25)):
        await db.settings.insert_one({
            "user_id": owner, "payment_methods": deepcopy(DEFAULT_PAYMENT_METHODS),
            "payment_fee_defaults_version": PAYMENT_FEE_DEFAULTS_VERSION,
        })
        await db[PRODUCTS].insert_one({"user_id": owner, "salla_product_id": "p", "name": "Synthetic product"})
        await db[COST_PROFILES].insert_one({"user_id": owner, "salla_product_id": "p", "base_cost": cost})
    await db.unified_orders.insert_many([
        sample_order("a", "selected", "2026-11-11", 100),
        sample_order("a", "month", "2026-11-03", 200),
        sample_order("a", "old", "2026-10-03", 999),
        sample_order("a", "other-payment", "2026-11-11", 700, payment="cash"),
        sample_order("a", "other-shipping", "2026-11-11", 800, shipping="carrier-b"),
        sample_order("b", "foreign", "2026-11-11", 1000),
    ])
    calls = Counter()
    reads, costs = [], []
    original_filtered = dashboard._filtered_orders
    original_cost = dashboard.build_mezan_v2_product_cost_cooperative
    state = {"today": date(2026, 11, 11), "ads_spend": 4}

    async def filtered(*args, **kwargs):
        reads.append((args[1], deepcopy(kwargs)))
        return await original_filtered(*args, **kwargs)

    async def cost(*args, **kwargs):
        costs.append((args[1], [row["order_number"] for row in args[2]], kwargs.get("include_product_rows", True)))
        return await original_cost(*args, **kwargs)

    async def ads(*args, **kwargs):
        calls["ads"] += 1
        spend = state["ads_spend"]
        return {
            "total": spend,
            "breakdown": {"snapchat": spend, "meta": 0, "tiktok": 0, "google_transitional": 0},
            "spend_quality": {"amount_available": True, "amount_complete": True},
            "bank_commissions": {"total_fee_sar": 0}, "source_contract": {},
            "providers": {
                provider: {"spend": spend if provider == "snapchat" else 0, "orders": 2,
                           "revenue": 0, "roas": 0}
                for provider in ("snapchat", "meta", "tiktok", "google")
            },
        }

    async def recurring(*args, **kwargs):
        calls["recurring"] += 1
        return {"total": 0, "rentals_total": 0, "utilities_total": 0, "renewals_total": 0, "by_type": {}}

    async def legacy(**kwargs):
        calls["legacy"] += 1
        assert kwargs["allow_self_heal"] is False
        return {
            "totals": {"total_orders": 1, "total_sales": 100, "total_product_cost": 0,
                       "total_ads_cost": 0, "operating_expenses_total": 0, "operating_salaries_total": 0,
                       "total_payment_fees": 0, "net_profit": 100, "net_sales": 100},
            "payment_breakdown": [], "net_sales_config": {}, "recent_analyses": ["must_be_hidden"],
        }

    async def no_provider(*args, **kwargs):
        raise AssertionError("dashboard card GET must not call a provider")

    async def user(request: Request):
        return {"id": request.headers.get("x-owner", "a"), "role": request.headers.get("x-role", "owner")}

    def require_owner(current):
        if current["role"] != "owner":
            raise HTTPException(status_code=403, detail="owner_only")

    monkeypatch.setattr(dashboard, "_today_riyadh", lambda: state["today"])
    monkeypatch.setattr(dashboard, "_filtered_orders", filtered)
    monkeypatch.setattr(dashboard, "build_mezan_v2_product_cost_cooperative", cost)
    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(dashboard, "compute_recurring_obligations_for_range", recurring)
    monkeypatch.setattr(dashboard, "call_salla", no_provider)
    app = FastAPI()
    app.include_router(dashboard.make_dashboard_v2_router(ReadOnlyDB(db), user, legacy, require_owner))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(db=db, app=app, client=client, calls=calls, reads=reads, costs=costs, state=state)


PARAMS = {
    "from_date": "2026-11-11", "to_date": "2026-11-11", "payment_methods": "mada",
    "shipping_companies": "carrier-a", "report_statuses": "default",
}


@pytest.mark.asyncio
async def test_top_products_reads_only_selected_costs_and_authorizes_saved_hits(card_app):
    env = card_app
    path = "/dashboard-v2/cards/top-products"
    response = await env.client.get(path, params=PARAMS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"product_cost_v2", "snapshot"}
    assert body["snapshot"]["refresh_interval_seconds"] == 1800
    assert body["product_cost_v2"]["total"] == 10
    assert body["product_cost_v2"]["product_rows"][0]["total_sales"] == 100
    assert env.costs == [("a", ["selected"], True)]
    assert len(env.reads) == 1
    assert env.calls == {}
    assert (await env.client.get(path, params=PARAMS)).json() == body
    assert len(env.costs) == 1
    denied = await env.client.get(path, params=PARAMS, headers={"x-role": "staff"})
    assert denied.status_code == 403
    foreign = await env.client.get(path, params=PARAMS, headers={"x-owner": "b"})
    assert foreign.json()["product_cost_v2"]["total"] == 25
    assert foreign.json()["product_cost_v2"]["product_rows"][0]["total_sales"] == 1000
    await env.client.get(path, params={**PARAMS, "refresh": True})
    assert env.costs[-1] == ("a", ["selected"], True)
    assert len(env.costs) == 3


@pytest.mark.asyncio
async def test_period_summary_uses_month_cohort_and_light_selected_cost_without_full_dashboard(card_app):
    env = card_app
    response = await env.client.get("/dashboard-v2/cards/period-summary", params=PARAMS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["snapshot"]["refresh_interval_seconds"] == 18000
    assert body["month_kpis"]["total_orders"] == 2
    assert body["month_kpis"]["total_sales"] == 300
    assert body["month_kpis"]["from_date"] == "2026-11-01"
    assert body["month_kpis"]["to_date"] == "2026-11-11"
    assert body["product_cost_v2"]["total"] == 10
    assert "product_rows" not in body["product_cost_v2"]
    snap = body["ads_v2"]["executive_breakdown"]["providers"]["snapchat"]
    assert snap["platform_reported_orders"] == 2
    assert snap["platform_cost_per_order_sar"] == 2
    assert env.costs == [("a", ["selected"], False)]
    assert env.calls == {"ads": 1}
    assert len(env.reads) == 2
    for owner, scope in env.reads:
        assert owner == "a"
        assert scope["payment_methods"] == "mada"
        assert scope["shipping_companies"] == "carrier-a"
        assert scope["report_statuses"] == "default"


@pytest.mark.asyncio
async def test_top_products_matches_original_rows_with_raw_salla_attribution(card_app):
    env = card_app
    await env.db.unified_orders.update_one({"user_id": "a", "order_number": "selected"}, {"$set": {
        "raw_by_source": {"salla_direct": {"utm_source": "snapchat", "utm_campaign": "synthetic-campaign"}},
    }})
    full = await env.client.get("/dashboard-v2", params=PARAMS)
    card = await env.client.get("/dashboard-v2/cards/top-products", params=PARAMS)
    assert full.status_code == card.status_code == 200
    assert card.json()["product_cost_v2"] == full.json()["product_cost_v2"]
    row = card.json()["product_cost_v2"]["product_rows"][0]
    assert row["traffic_sources"][0]["platform"] == "snapchat"
    assert row["traffic_sources"][0]["units_sold"] == 1


@pytest.mark.asyncio
async def test_unknown_order_fx_stays_unknown_in_all_card_financial_results(card_app):
    env = card_app
    await env.db.unified_orders.update_one({"user_id": "a", "order_number": "selected"}, {"$set": {"currency": "USD"}})
    full = (await env.client.get("/dashboard-v2", params=PARAMS)).json()
    executive = (await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)).json()
    top = (await env.client.get("/dashboard-v2/cards/top-products", params=PARAMS)).json()
    summary = (await env.client.get("/dashboard-v2/cards/period-summary", params=PARAMS)).json()
    assert executive["totals"] == full["totals"]
    assert executive["totals"]["net_profit"] is None
    assert executive["totals"]["total_sales"] is None
    assert top["product_cost_v2"]["product_rows"][0]["total_sales"] is None
    assert top["product_cost_v2"]["product_rows"][0]["net_profit"] is None
    assert summary["month_kpis"]["total_sales"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,expected_sales", [
    ({"from_date": "2026-11-03", "to_date": "2026-11-03"}, 200),
    ({"report_statuses": "reviewed"}, None),
])
async def test_date_and_explicit_status_have_distinct_actual_card_cohorts(card_app, scope, expected_sales):
    env = card_app
    await env.client.get("/dashboard-v2/cards/top-products", params=PARAMS)
    response = await env.client.get("/dashboard-v2/cards/top-products", params={**PARAMS, **scope})
    assert response.status_code == 200, response.text
    rows = response.json()["product_cost_v2"]["product_rows"]
    if expected_sales is None:
        assert rows == []
    else:
        assert rows[0]["total_sales"] == expected_sales
    assert len(env.costs) == 2


@pytest.mark.asyncio
async def test_executive_matches_full_financials_omits_rich_work_and_cost_edits_invalidate(card_app, monkeypatch):
    env = card_app
    full_response = await env.client.get("/dashboard-v2", params=PARAMS)
    assert full_response.status_code == 200, full_response.text
    full = full_response.json()
    assert "month_kpis" in full and "product_rows" in full["product_cost_v2"]
    assert full["recent_analyses"] == []
    env.reads.clear()

    async def forbidden_traffic(*args, **kwargs):
        raise AssertionError("executive must not perform rich product traffic work")

    monkeypatch.setattr(dashboard, "load_product_traffic_sources", forbidden_traffic)
    path = "/dashboard-v2/cards/executive"
    response = await env.client.get(path, params=PARAMS)
    assert response.status_code == 200, response.text
    light = response.json()
    assert light["totals"] == full["totals"]
    assert light["totals"]["net_profit"] == 86
    assert "month_kpis" not in light
    assert "product_rows" not in light["product_cost_v2"]
    assert "product_profit_summary" not in light["product_cost_v2"]
    assert len(env.reads) == 1
    assert light["snapshot"]["refresh_interval_seconds"] == 60
    previous_calls = env.calls.copy()
    assert (await env.client.get(path, params=PARAMS)).json() == light
    assert env.calls == previous_calls

    await env.db[COST_PROFILES].update_one({"user_id": "a", "salla_product_id": "p"}, {"$set": {"base_cost": 20}})
    await bump_product_cost_revision(env.db, "a")
    edited = (await env.client.get(path, params=PARAMS)).json()
    assert edited["totals"]["total_product_cost"] == 20
    assert edited["totals"]["net_profit"] == 76
    env.state["ads_spend"] = 8
    refreshed = (await env.client.get(path, params={**PARAMS, "refresh": True})).json()
    assert refreshed["totals"]["net_profit"] == 72


@pytest.mark.asyncio
async def test_saved_key_changes_for_scope_policy_settings_and_riyadh_day(card_app):
    env = card_app
    path = "/dashboard-v2/cards/top-products"
    await env.client.get(path, params=PARAMS)
    without_shipping = {**PARAMS, "shipping_companies": ""}
    assert (await env.client.get(path, params=without_shipping)).json()["product_cost_v2"]["total"] == 20
    assert len(env.costs) == 2
    await env.db.order_status_policy.insert_one({"user_id": "a", "status": "تم التنفيذ", "category": "cancelled"})
    assert (await env.client.get(path, params=PARAMS)).json()["product_cost_v2"]["total"] == 0
    assert len(env.costs) == 3
    await env.db.settings.update_one({"user_id": "a"}, {"$set": {"report_included_statuses": ["تم الشحن"]}})
    assert (await env.client.get(path, params=PARAMS)).json()["product_cost_v2"]["product_rows"] == []
    assert len(env.costs) == 4
    env.state["today"] = date(2026, 11, 12)
    await env.client.get(path, params=PARAMS)
    assert len(env.costs) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["top-products", "period-summary", "executive"])
@pytest.mark.parametrize("bad", [{"from_date": "not-a-date"}, {"to_date": "2026-11-10"}, {"report_statuses": "unsupported"}])
async def test_card_validation_precedes_any_computation(card_app, section, bad):
    env = card_app
    response = await env.client.get("/dashboard-v2/cards/" + section, params={**PARAMS, **bad})
    assert response.status_code == 422
    assert env.calls == {} and env.costs == [] and env.reads == []


@pytest.mark.asyncio
async def test_snapshot_timeout_returns_retryable_incomplete_http_result(card_app, monkeypatch):
    async def timeout(*args, **kwargs):
        raise dashboard.DashboardSnapshotTimeout("synthetic_timeout")

    monkeypatch.setattr(dashboard.DashboardSnapshotCache, "get", timeout)
    response = await card_app.client.get("/dashboard-v2/cards/top-products", params=PARAMS)
    assert response.status_code == 504
    assert response.json()["detail"] == {
        "code": "dashboard_snapshot_timeout", "retryable": True, "data_complete": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["period-summary", "executive"])
async def test_failed_component_cancels_and_drains_cost_work_before_releasing_card(card_app, monkeypatch, section):
    env = card_app
    started, released, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_cost = dashboard.build_mezan_v2_product_cost_cooperative
    original_ads = dashboard.build_mezan_v2_ads

    async def pending_cost(*args, **kwargs):
        started.set()
        try:
            await released.wait()
        except asyncio.CancelledError:
            # Cancellation must drain actual async cleanup too, not just send
            # a cancellation signal and free the admission immediately.
            await asyncio.sleep(0)
            cancelled.set()
            raise

    async def failed_ads(*args, **kwargs):
        await started.wait()
        raise RuntimeError("synthetic_ads_failure")

    monkeypatch.setattr(dashboard, "build_mezan_v2_product_cost_cooperative", pending_cost)
    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", failed_ads)
    path = "/dashboard-v2/cards/" + section
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=env.app, raise_app_exceptions=False), base_url="http://test",
        ) as client:
            failed = await client.get(path, params=PARAMS)
        assert failed.status_code == 500
        assert cancelled.is_set()
    finally:
        released.set()
    monkeypatch.setattr(dashboard, "build_mezan_v2_product_cost_cooperative", original_cost)
    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", original_ads)
    assert (await env.client.get(path, params=PARAMS)).status_code == 200


@pytest.mark.asyncio
async def test_initial_read_failure_and_parent_cancellation_drain_all_siblings():
    started, cleanup = asyncio.Event(), asyncio.Event()

    async def pending():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleanup.set()

    original_error = RuntimeError("synthetic_initial_read_failure")

    async def failing():
        await started.wait()
        raise original_error

    with pytest.raises(RuntimeError) as caught:
        await dashboard._gather_dashboard_reads(pending(), failing())
    assert caught.value is original_error
    assert cleanup.is_set()
    started.clear()
    cleanup.clear()
    parent = asyncio.create_task(dashboard._gather_dashboard_reads(pending()))
    await started.wait()
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert cleanup.is_set()
