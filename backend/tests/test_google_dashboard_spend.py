from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json

from mongomock_motor import AsyncMongoMockClient
import pytest

import dashboard_v2_routes as executive
from dashboard_v2_ads_executive import (
    build_salla_ads_executive_breakdown,
    build_salla_ads_executive_breakdown_cooperative,
)
from integrations_control_center import dashboard_ads_platform_spend_routes as platforms
from integrations_control_center.ad_daily_close_proof import close_proof
from integrations_control_center.ads_platform_hourly import local_hour_start_utc
from integrations_control_center.google_dashboard_spend import load_google_dashboard_spend


DAY = "2026-08-05"
OWNER = "google-spend-owner"
ACCOUNT = "1234567890"
COLLECTION = "mezan_google_ads_performance_daily_v2"
SOURCE = "google_ads_reporting_v1"


def native_fact(amount=96.48, *, day=DAY, account=ACCOUNT, owner=OWNER, observed=None):
    if observed is None:
        observed = (date.fromisoformat(day) + timedelta(days=1)).isoformat() + "T00:00:00+00:00"
    return {
        "user_id": owner, "provider": "google_ads", "ad_account_id": account,
        "date": day, "source_mode": SOURCE, "account_timezone": "Asia/Riyadh",
        "currency_native": "SAR", "spend_native": amount, "spend_sar": round(amount, 2),
        "fx_rate_to_sar": 1.0, "observed_at": observed, "updated_at": observed,
        "source_close_proof": close_proof(
            account_id=account, business_date=day, timezone="Asia/Riyadh", currency="SAR",
            spend=amount, provider_row_count=1, complete_response=True,
            explicit_spend_present=True, identity_proven=True,
            source_mode=SOURCE, observed_at=observed,
        ),
    }


async def connected_google(db, *, account=ACCOUNT, selected=True):
    await db.mezan_integrations_v2.update_one(
        {"user_id": OWNER, "provider": "google_ads"},
        {"$set": {"connection_status": "connected", "connection_provenance": "api_connection"}},
        upsert=True,
    )
    await db.mezan_integration_accounts_v2.insert_one({
        "user_id": OWNER, "provider": "google_ads", "ad_account_id": account,
        "connection_status": "connected", "connection_provenance": "api_connection",
        "mezan_selected": selected, "currency": "SAR", "timezone": "Asia/Riyadh",
    })


@pytest.fixture
def db():
    return AsyncMongoMockClient().google_dashboard_spend


@pytest.fixture
def provisional_snapchat(monkeypatch):
    snapshot = {
        "total_sar": 25078.85, "known_total_sar": 25078.85,
        "rows": [{"date": DAY, "spend_sar": 25078.85}],
        "daily_sar": {DAY: 25078.85}, "hourly_sar": {}, "bank_commissions": {},
        "quality": {
            "status": "provisional", "data_state": "provisional_data", "connected": True,
            "amount_available": True, "amount_complete": False, "coverage_complete": False,
            "provisional": True, "reason_codes": ["snapchat_operational_hour_observation"],
        },
    }

    async def saved_snapchat(*_args, **_kwargs):
        return deepcopy(snapshot)

    async def no_shadow(*_args, **_kwargs):
        return None

    async def no_provider_request(*_args, **_kwargs):
        raise AssertionError("Dashboard spend must use saved facts, without provider requests")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "request", no_provider_request)
    monkeypatch.setattr(executive, "load_unified_marketing_dashboard_spend", saved_snapchat)
    monkeypatch.setattr(platforms, "load_unified_marketing_dashboard_spend", saved_snapchat)
    monkeypatch.setattr(platforms, "load_dashboard_unified_shadow", no_shadow)
    return snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_manual", [0, 999])
async def test_native_google_agrees_across_surfaces_with_provisional_snapchat(
    db, provisional_snapchat, stale_manual,
):
    await connected_google(db)
    await connected_google(db, account="unselected-account", selected=False)
    await db[COLLECTION].insert_many([
        native_fact(), native_fact(999, account="unselected-account"),
        native_fact(999, owner="another-owner"), native_fact(999, day="2026-08-04"),
    ])
    await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": stale_manual})
    for provider, amount, collection in (
        ("meta", 2837.13, "mezan_meta_performance_daily_v2"),
        ("tiktok", 648.07, "mezan_tiktok_performance_daily_v2"),
    ):
        account = provider + "-account"
        await db.mezan_integration_accounts_v2.insert_one({
            "user_id": OWNER, "provider": provider + "_ads", "ad_account_id": account,
            "connection_status": "connected", "connection_provenance": "api_connection",
            "mezan_selected": True, "currency": "SAR",
        })
        await db[collection].insert_one({
            "user_id": OWNER, "provider": provider + "_ads", "ad_account_id": account,
            "date": DAY, "spend_native": amount, "spend_sar": amount, "currency_native": "SAR",
        })
    hour = local_hour_start_utc(date.fromisoformat(DAY), 1, "Asia/Riyadh").isoformat(timespec="seconds")
    await db.mezan_ads_platform_hourly_v2.insert_many([
        {"user_id": OWNER, "provider": "google", "ad_account_id": account,
         "source_mode": SOURCE, "hour_start_utc": hour, "spend_sar": amount}
        for account, amount in ((ACCOUNT, 96.48), ("unselected-account", 999))
    ])

    ads = await executive.build_mezan_v2_ads(db, OWNER, from_date=DAY, to_date=DAY)
    card = await platforms.build_dashboard_platform_spend(db, OWNER, date_from=DAY, date_to=DAY)

    assert ads["breakdown"]["google_transitional"] == 96.48
    assert "google" not in ads["breakdown"]  # A second numeric alias would double-count costs.
    assert ads["history"][0]["google"] == 96.48
    assert card["provider_totals_sar"]["google"] == 96.48
    assert ads["total"] == card["total_sar"] == 28660.53
    assert sum(row["google"] or 0 for row in card["hourly_spend"]) == 96.48
    assert ads["spend_quality"]["provisional"] is True
    assert card["spend_quality"]["provisional"] is True
    assert ads["providers"]["snapchat"]["amount_available"] is True
    assert ads["providers"]["snapchat"]["platform_reported_metrics_complete"] is False
    assert ads["bank_commissions"]["google_account_allocation"] == "not_available"
    assert COLLECTION in ads["source_contract"]["google"]
    synchronous = build_salla_ads_executive_breakdown([], ads)
    cooperative = await build_salla_ads_executive_breakdown_cooperative([], ads)
    assert synchronous == cooperative
    assert synchronous["providers"]["google"]["spend_sar"] == 96.48
    assert synchronous["total"]["spend_sar"] == 28660.53


async def read_google(db, *, start=DAY, end=DAY):
    return await load_google_dashboard_spend(
        db, OWNER, date_from=start, date_to=end,
        now=datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_native_explicit_zero_overrides_stale_positive_manual(db):
    await connected_google(db)
    await db[COLLECTION].insert_one(native_fact(0))
    await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": 999})
    result = await read_google(db)
    assert result["total_sar"] == result["daily_sar"][DAY] == 0
    assert result["daily_sources"][DAY] == "native"
    assert result["quality"]["amount_complete"] is True
    assert result["quality"]["coverage_complete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [None, 0])
async def test_connected_google_missing_is_not_manufactured_zero(db, manual):
    await connected_google(db)
    if manual is not None:
        await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": manual})
    result = await read_google(db)
    assert result["total_sar"] is None
    assert result["daily_sar"][DAY] is None
    assert result["quality"]["amount_available"] is False
    assert "google_spend_missing" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
async def test_unconfigured_google_does_not_block_sparse_legacy_period(db):
    absent = await read_google(db)
    assert absent["total_sar"] == 0
    assert absent["quality"]["data_state"] == "not_required"
    assert absent["daily_sar"][DAY] is None  # No claim that a provider observed zero.
    await db.daily_costs.insert_many([
        {"user_id": OWNER, "date": DAY, "google_ads": 20},
        {"user_id": OWNER, "date": "2026-08-06", "google_ads": 0},
    ])
    result = await read_google(db, end="2026-08-07")
    assert result["total_sar"] == 20
    assert result["quality"]["amount_available"] is True
    assert result["daily_sources"] == {
        DAY: "legacy_manual", "2026-08-06": "not_required", "2026-08-07": "not_required",
    }


@pytest.mark.asyncio
async def test_legacy_fractional_days_round_only_after_period_sum(db):
    await db.daily_costs.insert_many([
        {"user_id": OWNER, "date": DAY, "google_ads": 11.115},
        {"user_id": OWNER, "date": "2026-08-06", "google_ads": 11.115},
    ])
    result = await read_google(db, end="2026-08-06")
    assert result["total_sar"] == result["known_total_sar"] == 22.23
    assert result["daily_sar"] == {DAY: 11.12, "2026-08-06": 11.12}
    assert result["quality"]["amount_available"] is True
    assert result["quality"]["provisional"] is True


@pytest.mark.asyncio
async def test_native_days_sum_saved_cents_without_repricing_native_or_manual(db):
    await connected_google(db)
    await db[COLLECTION].insert_many([
        native_fact(0.015), native_fact(0.015, day="2026-08-06"),
    ])
    await db.daily_costs.insert_many([
        {"user_id": OWNER, "date": DAY, "google_ads": 11.115},
        {"user_id": OWNER, "date": "2026-08-06", "google_ads": 11.115},
    ])
    result = await read_google(db, end="2026-08-06")
    assert result["total_sar"] == result["known_total_sar"] == 0.02
    assert result["daily_sar"] == {DAY: 0.01, "2026-08-06": 0.01}
    assert set(result["daily_sources"].values()) == {"native"}


@pytest.mark.asyncio
async def test_unconfigured_card_keeps_google_unavailable_without_blocking_other_spend(db, provisional_snapchat):
    card = await platforms.build_dashboard_platform_spend(db, OWNER, date_from=DAY, date_to=DAY)
    assert card["provider_totals_sar"]["google"] is None
    assert card["providers"]["google"]["total_sar"] is None
    assert card["providers"]["google"]["daily_available"] is False
    assert card["providers"]["google"]["data_state"] == "not_required"
    assert card["total_sar"] == 25078.85
    assert card["spend_quality"]["amount_available"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("native_zero,expected_json", [
    (False, '{"google_transitional":0,"google_transitional_spend_sar":0}'),
    (True, '{"google_transitional":0.0,"google_transitional_spend_sar":0.0}'),
])
async def test_executive_google_zero_json_preserves_empty_sum_and_native_evidence(
    db, provisional_snapchat, native_zero, expected_json,
):
    if native_zero:
        await connected_google(db)
        await db[COLLECTION].insert_one(native_fact(0))
    ads = await executive.build_mezan_v2_ads(db, OWNER, from_date=DAY, to_date=DAY)
    assert json.dumps({
        "google_transitional": ads["breakdown"]["google_transitional"],
        "google_transitional_spend_sar": ads["bank_commissions"]["google_transitional_spend_sar"],
    }, separators=(",", ":")) == expected_json


@pytest.mark.asyncio
async def test_ambiguous_legacy_day_is_unknown_even_without_google_configuration(db):
    await db.daily_costs.insert_many([
        {"user_id": OWNER, "date": DAY, "google_ads": 10},
        {"user_id": OWNER, "date": DAY, "google_ads": 20},
    ])
    result = await read_google(db)
    assert result["total_sar"] is None
    assert result["quality"]["amount_available"] is False
    assert "google_legacy_day_ambiguous" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value,invalid", [
    (-10, True), ("bad-value", True), (float("nan"), True), (0, False), (None, False),
])
async def test_explicit_invalid_legacy_amount_is_not_an_unconfigured_zero(db, value, invalid):
    await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": value})
    result = await read_google(db)
    if invalid:
        assert result["total_sar"] is None
        assert result["daily_sources"][DAY] == "legacy_incomplete"
        assert result["quality"]["amount_available"] is False
        assert "google_legacy_amount_invalid" in result["quality"]["reason_codes"]
    else:
        assert result["total_sar"] == 0
        assert result["quality"]["data_state"] == "not_required"
        assert result["quality"]["amount_available"] is True


@pytest.mark.asyncio
async def test_legacy_fills_only_native_uncovered_days_and_is_tenant_date_scoped(db):
    await connected_google(db)
    await db[COLLECTION].insert_one(native_fact())
    await db.daily_costs.insert_many([
        {"user_id": OWNER, "date": DAY, "google_ads": 999},
        {"user_id": OWNER, "date": "2026-08-06", "google_ads": 20},
        {"user_id": "another-owner", "date": "2026-08-06", "google_ads": 999},
        {"user_id": OWNER, "date": "2026-08-07", "google_ads": 999},
    ])
    result = await read_google(db, end="2026-08-06")
    assert result["total_sar"] == 116.48
    assert result["daily_sources"] == {DAY: "native", "2026-08-06": "legacy_manual"}
    assert result["quality"]["amount_available"] is True
    assert result["quality"]["amount_complete"] is False
    assert result["quality"]["provisional"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [96.48, 0.004])
async def test_positive_pre_proof_native_observation_is_available_but_provisional(db, native):
    await connected_google(db)
    row = native_fact(native)
    row.pop("source_close_proof")
    await db[COLLECTION].insert_one(row)
    result = await read_google(db)
    assert result["total_sar"] == round(native, 2)
    assert result["quality"]["amount_available"] is True
    assert result["quality"]["provisional"] is True
    assert result["quality"]["amount_complete"] is False
    assert result["quality"]["coverage_complete"] is False


@pytest.mark.asyncio
async def test_current_day_source_proof_does_not_claim_final_day_amount(db):
    await connected_google(db)
    await db[COLLECTION].insert_one(native_fact(day="2026-11-11", observed="2026-11-11T12:00:00+00:00"))
    result = await read_google(db, start="2026-11-11", end="2026-11-11")
    assert result["total_sar"] == 96.48
    assert result["quality"]["coverage_complete"] is True
    assert result["quality"]["amount_complete"] is False
    assert result["quality"]["provisional"] is True
    assert result["quality"]["last_observed_at"] == "2026-11-11T12:00:00+00:00"


@pytest.mark.asyncio
async def test_midday_observation_stays_provisional_after_midnight_without_new_fact(db):
    await connected_google(db)
    await db[COLLECTION].insert_one(native_fact(day="2026-11-10", observed="2026-11-10T12:00:00+00:00"))
    for now in (datetime(2026, 11, 10, 12, 1, tzinfo=timezone.utc),
                datetime(2026, 11, 11, 0, 1, tzinfo=timezone.utc)):
        result = await load_google_dashboard_spend(
            db, OWNER, date_from="2026-11-10", date_to="2026-11-10", now=now,
        )
        assert result["total_sar"] == 96.48
        assert result["quality"]["amount_available"] is True
        assert result["quality"]["amount_complete"] is False
        assert result["quality"]["provisional"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", [
    "missing_sar", "invalid_sar", "infinite_sar", "wrong_stored_fx", "wrong_timezone",
    "incomplete_proof", "wrong_proof_account", "wrong_proof_day", "unproven_zero",
])
async def test_invalid_native_blocks_manual_fallback(db, corruption):
    await connected_google(db)
    row = native_fact()
    if corruption == "missing_sar":
        row["spend_sar"] = None
    elif corruption == "invalid_sar":
        row["spend_sar"] = "not-a-number"
    elif corruption == "infinite_sar":
        row["spend_sar"] = float("inf")
    elif corruption == "wrong_stored_fx":
        row["fx_rate_to_sar"] = 3.75
    elif corruption == "wrong_timezone":
        row["account_timezone"] = row["source_close_proof"]["timezone"] = "America/New_York"
    elif corruption == "incomplete_proof":
        row["source_close_proof"]["complete"] = False
    elif corruption == "wrong_proof_account":
        row["source_close_proof"]["account_id"] = "another-account"
    elif corruption == "wrong_proof_day":
        row["source_close_proof"]["business_date"] = "2026-08-04"
    elif corruption == "unproven_zero":
        row = native_fact(0)
        row.pop("source_close_proof")
    await db[COLLECTION].insert_one(row)
    await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": 999})
    result = await read_google(db)
    assert result["total_sar"] is None
    assert result["daily_sources"][DAY] == "native_incomplete"
    assert result["quality"]["amount_available"] is False
    assert result["quality"]["amount_complete"] is False


@pytest.mark.asyncio
async def test_unselected_native_cannot_suppress_selected_accounts_manual_fallback(db):
    await connected_google(db)
    await connected_google(db, account="unselected-account", selected=False)
    await db[COLLECTION].insert_one(native_fact(999, account="unselected-account"))
    await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": 20})
    result = await read_google(db)
    assert result["total_sar"] == 20
    assert result["daily_sources"][DAY] == "legacy_manual"


@pytest.mark.asyncio
async def test_missing_second_selected_account_is_incomplete_not_manual_or_zero(db):
    await connected_google(db)
    await connected_google(db, account="second-account")
    await db[COLLECTION].insert_one(native_fact())
    await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": 999})
    result = await read_google(db)
    assert result["total_sar"] is None
    assert result["known_total_sar"] == 96.48
    assert "google_native_account_day_missing" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
async def test_duplicate_native_is_unknown_without_double_counting_or_manual_fallback(db):
    await connected_google(db)
    await db[COLLECTION].insert_many([native_fact(), native_fact()])
    await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": 999})
    result = await read_google(db)
    assert result["total_sar"] is None
    assert result["known_total_sar"] == 0
    assert "google_native_account_day_duplicate" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["MAX_ACCOUNTS", "MAX_ROWS"])
async def test_truncated_scope_cannot_be_reported_as_complete(db, monkeypatch, bound):
    from integrations_control_center import google_dashboard_spend as reader

    await connected_google(db)
    await connected_google(db, account="second-account")
    await db[COLLECTION].insert_many([native_fact(), native_fact(20, account="second-account")])
    monkeypatch.setattr(reader, bound, 1)
    result = await read_google(db)
    assert result["total_sar"] is None
    assert result["quality"]["amount_available"] is False
    assert result["quality"]["coverage_complete"] is False
    assert "google_spend_scope_incomplete" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
async def test_writer_fallback_to_all_connected_accounts_when_none_is_selected(db):
    await connected_google(db, selected=False)
    await connected_google(db, account="second-account", selected=False)
    await db[COLLECTION].insert_many([native_fact(), native_fact(20, account="second-account")])
    result = await read_google(db)
    assert result["total_sar"] == 116.48
    assert result["quality"]["account_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [10, 0.000001])
async def test_usd_uses_verified_stored_conversion_including_subcent_spend(db, native):
    await connected_google(db)
    await db.mezan_integration_accounts_v2.update_one(
        {"user_id": OWNER, "provider": "google_ads"}, {"$set": {"currency": "USD"}},
    )
    row = native_fact(native)
    row.update(currency_native="USD", fx_rate_to_sar=3.75, spend_sar=round(native * 3.75, 2))
    row["source_close_proof"]["currency"] = "USD"
    await db[COLLECTION].insert_one(row)
    result = await read_google(db)
    assert result["total_sar"] == round(native * 3.75, 2)
    assert result["quality"]["amount_complete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("currency,fx,hourly_cost_micros,raw,stored_sar_expected", [
    ("SAR", 1.0, [500, 4500], 0.004999999999999999, 0.0),
    ("USD", 3.75, [4179, 15821], 0.020000000000000004, 0.08),
])
async def test_writer_native_rounding_does_not_invalidate_saved_cent_amount(
    db, currency, fx, hourly_cost_micros, raw, stored_sar_expected,
):
    await connected_google(db)
    await db.mezan_integration_accounts_v2.update_one(
        {"user_id": OWNER, "provider": "google_ads"}, {"$set": {"currency": currency}},
    )
    # Match the writer's hourly float accumulation, followed by independent
    # six-decimal native storage and two-decimal SAR storage.
    daily_native = 0.0
    for micros in hourly_cost_micros:
        daily_native += micros / 1_000_000
    stored_native = round(daily_native, 6)
    stored_sar = round(daily_native * fx, 2)
    assert daily_native == raw
    assert stored_sar == stored_sar_expected
    assert stored_sar != round(stored_native * fx, 2)
    row = native_fact(stored_native)
    row.update(currency_native=currency, fx_rate_to_sar=fx, spend_sar=stored_sar)
    row["source_close_proof"] = close_proof(
        account_id=ACCOUNT, business_date=DAY, timezone="Asia/Riyadh", currency=currency,
        spend=sum(hourly_cost_micros) / 1_000_000, provider_row_count=len(hourly_cost_micros),
        complete_response=True, explicit_spend_present=True, identity_proven=True,
        source_mode=SOURCE, observed_at=row["observed_at"],
    )
    await db[COLLECTION].insert_one(row)
    await db.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": 999})

    result = await read_google(db)
    assert result["total_sar"] == stored_sar
    assert result["daily_sources"][DAY] == "native"
    assert result["quality"]["amount_complete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("native,sar,currency,fx", [
    (0.005001, 0.0, "SAR", 1.0),  # Outside half a cent plus native-storage rounding.
    (10.0, 10.01, "SAR", 1.0),
    (10.0, 10.001, "SAR", 1.0),  # Within half a cent, but not a stored cent amount.
    (0.001334, 0.0, "USD", 3.75),
])
async def test_amount_consistency_rejects_excess_error_and_noncent_sar(db, native, sar, currency, fx):
    await connected_google(db)
    await db.mezan_integration_accounts_v2.update_one(
        {"user_id": OWNER, "provider": "google_ads"}, {"$set": {"currency": currency}},
    )
    row = native_fact(native)
    row.update(currency_native=currency, fx_rate_to_sar=fx, spend_sar=sar)
    row["source_close_proof"]["currency"] = currency
    await db[COLLECTION].insert_one(row)
    result = await read_google(db)
    assert result["total_sar"] is None
    assert "google_native_amount_or_currency_unproven" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
async def test_missing_google_preserves_snapchat_independent_availability(db, provisional_snapchat):
    await connected_google(db)
    ads = await executive.build_mezan_v2_ads(db, OWNER, from_date=DAY, to_date=DAY)
    card = await platforms.build_dashboard_platform_spend(db, OWNER, date_from=DAY, date_to=DAY)
    assert ads["breakdown"]["google_transitional"] is None
    assert ads["total"] is card["total_sar"] is None
    for result in (ads, card):
        assert result["spend_quality"]["amount_available"] is False
        assert result["providers"]["snapchat"]["amount_available"] is True
        assert result["providers"]["snapchat"]["provisional"] is True
    assert ads["providers"]["snapchat"]["spend"] == 25078.85
    assert ads["providers"]["snapchat"]["platform_reported_metrics_complete"] is False
