"""The dashboard display and profit reader must use the same saved TikTok facts."""
from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

import dashboard_v2_routes as executive
from integrations_control_center import dashboard_ads_platform_spend_routes as display
from integrations_control_center.ads_platform_hourly import local_hour_start_utc
from tests.test_dashboard_four_platform_spend_v1 import FakeDB


OWNER = "native-spend-owner"
DAY = "2026-11-14"
NEXT_DAY = "2026-11-15"
ACCOUNT = "direct-tiktok-account"


def account(account_id=ACCOUNT, **extra):
    return {
        "user_id": OWNER,
        "provider": "tiktok_ads",
        "ad_account_id": account_id,
        "connection_status": "connected",
        "connection_provenance": "api_connection",
        "currency": "SAR",
        **extra,
    }


def fact(amount, *, day=DAY, account_id=ACCOUNT, **extra):
    return {
        "user_id": OWNER,
        "provider": "tiktok_ads",
        "ad_account_id": account_id,
        "date": day,
        "spend_native": amount,
        "spend_sar": amount,
        "currency_native": "SAR",
        **extra,
    }


def database(*, facts=(), accounts=None, ledger_amount=1.25, extra=None):
    rows = {
        "mezan_integrations_v2": [{
            "user_id": OWNER, "provider": "tiktok_ads",
            "connection_status": "connected", "connection_provenance": "api_connection",
        }],
        "mezan_integration_accounts_v2": [account()] if accounts is None else accounts,
        "mezan_tiktok_performance_daily_v2": list(facts),
        "counterparties": [{
            "user_id": OWNER, "kind": "ad_account", "ad_provider": "tiktok", "id": "old-bridge",
        }],
        "ad_account_ledger": [{
            "user_id": OWNER, "counterparty_id": "old-bridge", "type": "spend",
            "date": day, "amount": ledger_amount,
        } for day in (DAY, NEXT_DAY)],
        "tiktok_ads_daily": [{
            "user_id": OWNER, "date": DAY, "updated_at": DAY + "T10:00:00+00:00",
        }],
        **(extra or {}),
    }
    return FakeDB(rows)


@pytest.fixture(autouse=True)
def saved_only_shared_providers(monkeypatch):
    async def saved_snapchat(*_args, **kwargs):
        start, end = kwargs["date_from"], kwargs["date_to"]
        dates = [start.isoformat()] if start == end else [start.isoformat(), end.isoformat()]
        return {
            "total_sar": 10.0 * len(dates), "known_total_sar": 10.0 * len(dates),
            "rows": [{"date": day, "spend_sar": 10.0} for day in dates],
            "daily_sar": {day: 10.0 for day in dates}, "hourly_sar": {},
            "bank_commissions": {"accounts": [], "total_fee_sar": 0.0},
            "quality": {
                "status": "provisional", "amount_available": True, "amount_complete": False,
                "provisional": True, "connected": True,
            },
        }

    async def no_shadow(*_args, **_kwargs):
        return None

    async def no_provider_request(*_args, **_kwargs):
        raise AssertionError("Saved dashboard reads must never make a provider request")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "request", no_provider_request)
    monkeypatch.setattr(executive, "load_unified_marketing_dashboard_spend", saved_snapchat)
    monkeypatch.setattr(display, "load_unified_marketing_dashboard_spend", saved_snapchat)
    monkeypatch.setattr(display, "load_dashboard_unified_shadow", no_shadow)


async def both(db, *, end=DAY):
    canonical = await executive.build_mezan_v2_ads(db, OWNER, from_date=DAY, to_date=end)
    shown = await display.build_dashboard_platform_spend(db, OWNER, date_from=DAY, date_to=end)
    return canonical, shown


@pytest.mark.asyncio
@pytest.mark.parametrize("native_amount", [245.25, 0.0])
async def test_connected_native_tiktok_overrides_stale_make_even_for_observed_zero(native_amount):
    db = database(facts=[fact(native_amount)])
    before = deepcopy({name: collection.rows for name, collection in db.collections.items()})

    canonical, shown = await both(db)

    assert canonical["breakdown"]["tiktok"] == native_amount
    assert shown["provider_totals_sar"]["tiktok"] == native_amount
    assert shown["total_sar"] == canonical["total"] == 10 + native_amount
    assert shown["providers"]["tiktok"]["hourly_source"] != "make_daily_total_marker"
    assert shown["providers"]["tiktok"]["hourly_available"] is False
    assert all(row["tiktok"] is None for row in shown["hourly_spend"])
    assert {name: db[name].rows for name in before} == before
    assert shown["provider_write_reached"] is False
    assert shown["accounting_write_reached"] is False


@pytest.mark.asyncio
async def test_native_tiktok_uses_canonical_owner_account_scope():
    db = database(
        accounts=[
            account(), account("disconnected", connection_status="disconnected"),
            account("manual", connection_provenance="manual"),
            account("foreign", user_id="different-owner"),
        ],
        facts=[
            fact(25.0), fact(400.0, account_id="disconnected"),
            fact(800.0, account_id="manual"), fact(1600.0, account_id="foreign"),
            fact(3200.0, user_id="different-owner"),
        ],
    )

    canonical, shown = await both(db)

    assert canonical["breakdown"]["tiktok"] == shown["provider_totals_sar"]["tiktok"] == 25.0
    assert canonical["total"] == shown["total_sar"] == 35.0


@pytest.mark.asyncio
async def test_native_tiktok_uses_account_fx_and_rounds_selected_range_once():
    db = database(
        accounts=[account(currency="USD")],
        facts=[fact(0.001, day=day, currency_native="USD", spend_sar=9.0) for day in (DAY, NEXT_DAY)],
        extra={"mezan_ad_account_cost_settings_v2": [{
            "user_id": OWNER, "provider": "tiktok_ads", "external_account_id": ACCOUNT,
            "native_currency": "USD", "exchange_rate_to_sar": 3.7544,
            "apply_bank_commission": False,
        }]},
    )

    canonical, shown = await both(db, end=NEXT_DAY)

    expected = round((0.001 + 0.001) * 3.7544, 2)
    assert canonical["breakdown"]["tiktok"] == shown["provider_totals_sar"]["tiktok"] == expected
    assert shown["total_sar"] == canonical["total"] == round(20 + expected, 2)


@pytest.mark.asyncio
async def test_native_tiktok_hourly_data_excludes_accounts_outside_direct_authority():
    hour = local_hour_start_utc(date.fromisoformat(DAY), 3, "Asia/Riyadh").isoformat(timespec="seconds")
    db = database(facts=[fact(25.0)], extra={"mezan_ads_platform_hourly_v2": [
        {"user_id": OWNER, "provider": "tiktok", "ad_account_id": ident,
         "hour_start_utc": hour, "spend_sar": amount}
        for ident, amount in ((ACCOUNT, 25.0), ("unconnected-account", 900.0))
    ]})

    _, shown = await both(db)

    assert shown["hourly_spend"][3]["tiktok"] == 25.0
    assert shown["providers"]["tiktok"]["hourly_source"] == "provider_native"


@pytest.mark.asyncio
async def test_make_only_historical_bridge_still_reads_its_saved_amount():
    db = database(accounts=[], facts=[fact(0.25)])

    shown = await display.build_dashboard_platform_spend(db, OWNER, date_from=DAY, date_to=DAY)

    assert shown["provider_totals_sar"]["tiktok"] == 1.25
    assert shown["providers"]["tiktok"]["hourly_source"] == "make_daily_total_marker"


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_facts,accounts,end,known", [
    ([], [account()], DAY, 0.0),
    ([fact(25.0)], [account(), account("second-native")], DAY, 25.0),
    ([fact(25.0)], [account()], NEXT_DAY, 25.0),
    ([fact(None, spend_sar=None)], [account()], DAY, 0.0),
    ([fact(True)], [account()], DAY, 0.0),
    ([fact("not-an-amount")], [account()], DAY, 0.0),
])
async def test_missing_or_unusable_native_account_day_stays_unknown_without_make(
    saved_facts, accounts, end, known,
):
    db = database(facts=saved_facts, accounts=accounts, ledger_amount=999.0)

    shown = await display.build_dashboard_platform_spend(db, OWNER, date_from=DAY, date_to=end)

    assert shown["provider_totals_sar"]["tiktok"] is None
    assert shown["total_sar"] is None
    assert shown["spend_quality"]["status"] == "incomplete"
    assert shown["spend_quality"]["amount_available"] is False
    assert shown["providers"]["tiktok"]["known_total_sar"] == known
    assert shown["known_total_sar"] == known + (10.0 if end == DAY else 20.0)
    assert shown["providers"]["tiktok"]["hourly_source"] != "make_daily_total_marker"


@pytest.mark.asyncio
async def test_all_connected_accounts_with_explicit_zero_are_complete():
    ids = ["native-a", "native-b", "native-c", "native-d"]
    db = database(accounts=[account(ident) for ident in ids],
                  facts=[fact(0.0, account_id=ident) for ident in ids])

    canonical, shown = await both(db)

    assert shown["provider_totals_sar"]["tiktok"] == canonical["breakdown"]["tiktok"] == 0.0
    assert shown["total_sar"] == canonical["total"] == 10.0
    assert shown["spend_quality"]["amount_available"] is True


@pytest.mark.asyncio
async def test_truncated_native_read_is_incomplete_without_bridge_fallback(monkeypatch):
    ids = ["native-a", "native-b", "native-c"]
    monkeypatch.setattr(display, "MAX_DAILY_ROWS", 2)
    db = database(accounts=[account(ident) for ident in ids],
                  facts=[fact(10.0, account_id=ident) for ident in ids])

    shown = await display.build_dashboard_platform_spend(db, OWNER, date_from=DAY, date_to=DAY)

    assert shown["total_sar"] is None
    assert shown["provider_totals_sar"]["tiktok"] is None
    assert shown["providers"]["tiktok"]["known_total_sar"] == 20.0
    assert shown["providers"]["tiktok"]["reason_codes"] == ["tiktok_saved_rows_truncated"]


@pytest.mark.asyncio
async def test_direct_tiktok_read_never_reads_make_collections(monkeypatch):
    db = database(facts=[fact(25.0)])

    def legacy_read_is_forbidden(*_args, **_kwargs):
        raise AssertionError("Connected native TikTok must not read the abandoned bridge")

    for name in ("counterparties", "ad_account_ledger", "tiktok_ads_daily"):
        monkeypatch.setattr(db[name], "find", legacy_read_is_forbidden)

    canonical, shown = await both(db)

    assert shown["total_sar"] == canonical["total"] == 35.0
