"""Operating scope acceptance through the public campaign hover request."""
import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from order_engine import campaign_hover as hover
from order_engine import campaign_hover_profit as profit
from order_engine.campaign_hover_scope import resolve_operating_scope
from .test_campaign_hover_operating import DAY, OWNER, ReadDB, fixture_db, sale


class Clock(datetime):
    instant = datetime(2026, 11, 14, 20, 59, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def isolated_clock_and_cache(monkeypatch):
    monkeypatch.setattr(hover, "datetime", Clock)
    monkeypatch.setattr(Clock, "instant", datetime(2026, 11, 14, 20, 59, tzinfo=timezone.utc))
    hover._CACHE.clear()
    hover._PENDING.clear()
    yield
    hover._CACHE.clear()
    hover._PENDING.clear()


class CampaignReadDB(ReadDB):
    ALLOWED = ReadDB.ALLOWED | {
        "ads_v2_accounts", "mezan_integration_accounts_v2", "mezan_snapchat_entities_v2",
        "ads_v2_spend_raw", "mezan_snapchat_performance_daily_v2", "mezan_meta_campaign_performance_daily_v2",
        "mezan_snapchat_campaign_account_day_v2", "ads_v2_spend_daily",
        "mezan_snapchat_performance_account_day_v3", "mezan_tiktok_entities_v2",
        "mezan_tiktok_entity_daily_v2", "mezan_google_ads_campaign_factsets_v1",
        "mezan_tiktok_performance_daily_v2", "mezan_google_ads_performance_daily_v2",
    }


def application(db):
    app, router = FastAPI(), APIRouter()

    async def current_user():
        return {"id": OWNER, "role": "owner"}

    hover.install_campaign_hover_route(router, db, current_user)
    app.include_router(router)
    return app


async def campaign_database():
    order = sale("campaign-sale", 1000, raw_by_source={"salla_direct": {
        "date": {"date": "2026-11-14T09:00:00+00:00"},
        "utm_source": "snapchat", "utm_campaign": "campaign",
        "amounts": {"total": {"amount": 1000, "currency": "SAR"}},
        "items": [{"id": "line", "type": "product", "quantity": 1}],
    }})
    db = await fixture_db(order, sale("other-selected-day-sales", 4000),
        sale("new-store-day-sales", 50, order_date="2026-11-15"))
    await db.mezan_integration_accounts_v2.insert_one({"user_id": OWNER, "provider": "snapchat_ads",
        "ad_account_id": "account", "timezone": "America/Los_Angeles", "currency": "SAR"})
    await db.mezan_snapchat_entities_v2.insert_one({"user_id": OWNER, "entity_type": "campaign",
        "external_id": "campaign", "ad_account_id": "account", "name": "Synthetic campaign"})
    await db.mezan_snapchat_campaign_account_day_v2.insert_one({"user_id": OWNER,
        "ad_account_id": "account", "campaign_id": "campaign", "date": DAY,
        "account_timezone": "America/Los_Angeles", "source_mode": "snapchat_native_account_day_v2",
        "spend_sar": 100, "coverage_status": "provisional_hourly", "coverage_complete": True,
        "hours_observed": 12, "missing_hours": 0,
        "coverage_start_utc": "2026-11-14T08:00:00+00:00",
        "coverage_end_utc": "2026-11-14T20:00:00+00:00",
        "updated_at": "2026-11-14T20:05:00+00:00"})
    await db.operating_salaries.insert_one({"user_id": OWNER, "category": "charity",
        "monthly_amount": 3000, "status": "active", "start_date": "2026-11-01"})
    return CampaignReadDB(db)


def fixed_direct_costs(monkeypatch):
    # Direct product pricing is outside this regression. Keep actual operating,
    # campaign attribution, scope transport, cache, allocation and net arithmetic.
    async def prices(db, owner, orders):
        return {str(order["order_number"]): {
            "known_product_cost_sar": 500, "product_cost_complete": True,
            "shipping_cost_sar": 0, "order_payment_fees_sar": 0,
            "complete": True, "reasons": set(),
        } for order in orders}

    async def settings(db, owner):
        return []

    monkeypatch.setattr(profit, "_price_orders", prices)
    monkeypatch.setattr(profit, "_ad_settings", settings)
    monkeypatch.setattr(profit, "_ad_bank_fee", lambda record, settings: 0)


@pytest.mark.asyncio
async def test_public_selected_day_stays_fixed_across_riyadh_midnight_and_keeps_account_day(monkeypatch):
    db = await campaign_database()
    fixed_direct_costs(monkeypatch)
    params = {"campaign_ids": "campaign", "operating_from_date": DAY, "operating_to_date": DAY}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)), base_url="http://test") as client:
        before = await client.get("/campaign-hover", params={**params, "snapshot_revision": "r:before"})
        assert before.status_code == 200
        first = before.json()["campaigns"]["campaign"]
        monkeypatch.setattr(Clock, "instant", datetime(2026, 11, 14, 21, 1, tzinfo=timezone.utc))
        after = await client.get("/campaign-hover", params={**params, "snapshot_revision": "r:after"})
        assert after.status_code == 200
        second = after.json()["campaigns"]["campaign"]
        # Assert the monetary regression before new metadata, so this test
        # rejects the old midnight calculation rather than a missing field.
        allocations = [row["operating_expenses_sar"] for row in (first, second)]
        assert allocations == [20, 20], allocations
        assert [row["net_profit_sar"] for row in (first, second)] == [380, 380]
        for row in (first, second):
            assert row["date"] == DAY and row["timezone"] == "America/Los_Angeles"
            assert row["orders"] == 1 and row["sales_sar"] == 1000 and row["spend_sar"] == 100
            assert row["operating_basis"]["date_from"] == DAY and row["operating_basis"]["date_to"] == DAY
            assert row["operating_basis"]["sales_sar"] == 5000
            assert row["operating_basis"]["source"] == "executive_selected_period"
            assert row["operating_expenses_ratio"] == .02 and row["operating_expenses_sar"] == 20
            assert row["net_profit_available"] and row["net_profit_sar"] == 380
        # A deliberate selection of the new store day must produce its real
        # ratio, even above 100%, and preserve a legitimate negative net.
        changed = await client.get("/campaign-hover", params={**params,
            "operating_from_date": "2026-11-15", "operating_to_date": "2026-11-15",
            "snapshot_revision": "r:after"})
        row = changed.json()["campaigns"]["campaign"]
        assert row["date"] == DAY
        assert row["operating_expenses_ratio"] == 2 and row["operating_expenses_sar"] == 2000
        assert row["net_profit_sar"] == -1600


@pytest.mark.asyncio
async def test_context_is_normalized_before_cache_and_pending_lookup(monkeypatch):
    calls, ready, release = [], asyncio.Event(), asyncio.Event()

    async def read(db, owner, ids, *, operating_scope=None):
        calls.append(operating_scope)
        if len(calls) == 6:
            ready.set()
        await release.wait()
        return {"campaigns": {}, "scope": operating_scope.metadata()}

    monkeypatch.setattr(hover, "read_campaign_hover", read)
    base = dict(from_date=DAY, to_date=DAY, payment_methods="mada", shipping_companies="a")
    scopes = [
        resolve_operating_scope(**base),
        resolve_operating_scope(**{**base, "payment_methods": " MADA,mada ", "shipping_companies": " A,a "}),
        resolve_operating_scope(**{**base, "from_date": "2026-11-13"}),
        resolve_operating_scope(**{**base, "to_date": "2026-11-15"}),
        resolve_operating_scope(**{**base, "payment_methods": "visa"}),
        resolve_operating_scope(**{**base, "shipping_companies": "b"}),
        resolve_operating_scope(**{**base, "report_statuses": "default"}),
    ]
    tasks = [asyncio.create_task(hover.cached_campaign_hover(
        None, OWNER, ["campaign"], snapshot_revision="r:1", operating_scope=scope)) for scope in scopes]
    try:
        await asyncio.wait_for(ready.wait(), timeout=1)
        assert len(calls) == 6
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert results[0] == results[1]
    assert results[0]["scope"]["report_statuses"] is None
    assert results[-1]["scope"]["report_statuses"] == ["default"]
    for scope, expected in zip(scopes, results):
        assert await hover.cached_campaign_hover(
            None, OWNER, ["campaign"], snapshot_revision="r:1", operating_scope=scope) == expected
    assert len(calls) == 6
    assert resolve_operating_scope(**base, report_statuses="reviewed,in_progress,reviewed") == (
        resolve_operating_scope(**base, report_statuses="in_progress,reviewed"))


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [
    {"operating_from_date": DAY},
    {"operating_to_date": DAY},
    {"operating_payment_methods": "mada"},
    {"operating_shipping_companies": ""},
    {"operating_report_statuses": "default"},
    {"operating_from_date": "invalid", "operating_to_date": DAY},
    {"operating_from_date": "2026-11-15", "operating_to_date": DAY},
    {"operating_from_date": DAY, "operating_to_date": DAY, "operating_report_statuses": ""},
    {"operating_from_date": DAY, "operating_to_date": DAY, "operating_report_statuses": "default,reviewed"},
    {"operating_from_date": DAY, "operating_to_date": DAY, "operating_report_statuses": "unsupported"},
])
async def test_invalid_or_partial_scope_rejected_before_cache(monkeypatch, scope):
    async def unexpected(*args, **kwargs):
        raise AssertionError("invalid scope reached the cache")
    monkeypatch.setattr(hover, "cached_campaign_hover", unexpected)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(object())), base_url="http://test") as client:
        response = await client.get("/campaign-hover", params={"campaign_ids": "campaign", **scope})
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_legacy_cache_resolves_day_before_lookup_at_midnight(monkeypatch):
    scopes = []

    async def read(db, owner, ids, *, operating_scope=None):
        scopes.append(operating_scope)
        return {"campaigns": {}, "scope": operating_scope.metadata()}

    monkeypatch.setattr(hover, "read_campaign_hover", read)
    before = await hover.cached_campaign_hover(None, OWNER, ["campaign"])
    monkeypatch.setattr(Clock, "instant", datetime(2026, 11, 14, 21, 1, tzinfo=timezone.utc))
    after = await hover.cached_campaign_hover(None, OWNER, ["campaign"])
    assert before["scope"]["date_from"] == DAY
    assert after["scope"]["date_from"] == "2026-11-15"
    assert len(scopes) == 2 and all(scope.source == "store_day" for scope in scopes)
