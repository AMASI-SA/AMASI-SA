"""Actual reader/portfolio regressions, importable on the pre-fix baseline."""
from copy import deepcopy
from datetime import timedelta

import pytest

from snapchat_v2.facts import SNAPCHAT_HOURLY_FACTS_COLLECTION
from snapchat_v2.projections import SNAPCHAT_DAILY_PROJECTIONS_COLLECTION
from snapchat_v2.reconciliation import SNAPCHAT_RECONCILIATION_COLLECTION
from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from tests.test_snapchat_reporting_v2_midnight_zero_dashboard import DAY, NOW, dashboard, midnight
from unified_marketing.readers import snapchat_v2 as reader

SCOPE = {"user_id": "owner", "ad_account_id": "sar-account"}
START = NOW.replace(minute=0, second=0, microsecond=0)
END = START + timedelta(hours=1)


@pytest.fixture
def operational(midnight, monkeypatch):
    db = midnight
    # raising=False keeps the old reader importable for a meaningful red test.
    monkeypatch.setattr(reader, "_operational_now", lambda: NOW, raising=False)
    db.raw[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(SCOPE, {"$set": {
        "owner_id": "worker", "started_at": NOW - timedelta(seconds=10),
        "request_window.start_utc": START, "request_window.end_utc": END}})
    proof = {"version": "snapchat-current-operational-hours-v1", "user_id": "owner",
        "provider": "snapchat_ads", "ad_account_id": "sar-account", "currency": "SAR",
        "account_timezone": "Asia/Riyadh", "projection_timezone": "Asia/Riyadh",
        "report_date": DAY.isoformat(), "action_report_time": "conversion",
        "attribution_windows": {"swipe": "28_DAY", "view": "7_DAY"}, "sync_run_id": "sar-run",
        "producer_owner_id": "worker", "projection_generated_at": NOW - timedelta(seconds=2),
        "window_start_utc": START, "window_end_utc": END, "requested_at": NOW - timedelta(seconds=1),
        "observed_at": NOW, "status": "complete", "expected_requests": 1, "completed_requests": 1,
        "provider_rows": 0, "source_mode": "account_hour", "data_state": "observed_no_activity",
        "explicit_spend_present": False}
    hours = [{"sequence": index, "local_hour": f"{index:02}:00",
        "hour_start_utc": START + timedelta(hours=index), "hour_end_utc": START + timedelta(hours=index + 1),
        "spend_native": 0 if index == 0 else None, "status": "provisional" if index == 0 else "future"}
        for index in range(24)]
    db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].update_one(SCOPE, {"$set": {
        "operational_attempt_run_id": "sar-run", "operational_attempt_owner_id": "worker",
        "operational_observation": proof, "operational_hours": hours}})
    return db


@pytest.mark.asyncio
async def test_actual_reader_portfolio_uses_usd_observed_plus_sar_no_activity_for_provisional_graph(operational):
    db = operational
    before = deepcopy(list(db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].find()))
    result = await dashboard(db)
    assert result["total_sar"] == 81.32 and result["known_total_sar"] == 81.32
    assert result["quality"]["amount_available"] is True and result["quality"]["provisional"] is True
    assert all(result["quality"][key] is False for key in ("complete", "amount_complete", "coverage_complete"))
    assert result["daily_state"][DAY.isoformat()] == "provisional_data"
    assert "snapchat_operational_no_activity_observed" in result["quality"]["reason_codes"]
    assert result["hourly_sar"][DAY.isoformat()][0]["spend_sar"] == 81.32
    assert result["hourly_sar"][DAY.isoformat()][0]["status"] == "provisional"
    assert result["hourly_sar"][DAY.isoformat()][1]["spend_sar"] is None
    sar = next(account for account in result["accounts"] if account["account_id"] == "sar-account")
    assert sar["total_sar"] == 0 and sar["quality"]["financial_status"] == "partial"
    assert sar["quality"]["operational_observation"]["data_state"] == "observed_no_activity"
    row = next(row for row in result["rows"] if row["ad_account_id"] == "sar-account")
    assert all(row[key] is None for key in ("impressions", "clicks", "purchases", "purchase_value_sar"))
    assert db.raw[SNAPCHAT_HOURLY_FACTS_COLLECTION].count_documents({}) == 0
    assert list(db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].find()) == before
    assert next(row for row in before if row["ad_account_id"] == "sar-account")["source_close_proof"]["complete"] is False
    assert result["accounting_write_reached"] is False and result["provider_write_reached"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [0, 2])
async def test_explicit_account_hour_spend_remains_provisional_and_spend_only(operational, native):
    operational.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].update_one(SCOPE, {"$set": {
        "operational_observation.data_state": "explicit_hour_spend",
        "operational_observation.explicit_spend_present": True,
        "operational_observation.provider_rows": 1, "operational_hours.0.spend_native": native}})
    operational.raw[SNAPCHAT_RECONCILIATION_COLLECTION].update_one(SCOPE, {"$set": {
        "provider_riyadh_window_spend_native": native, "reconciled": False}})
    result = await dashboard(operational)
    assert result["total_sar"] == round(81.32 + native, 2)
    assert result["quality"]["amount_available"] is True and result["quality"]["complete"] is False
    assert "snapchat_operational_hour_observation" in result["quality"]["reason_codes"]
    assert "snapchat_operational_no_activity_observed" not in result["quality"]["reason_codes"]
    assert result["hourly_sar"][DAY.isoformat()][0]["spend_sar"] == round(81.32 + native, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("user_id", "foreign-owner"), ("ad_account_id", "foreign-account"), ("currency", "USD"),
    ("account_timezone", "America/Los_Angeles"), ("projection_timezone", "UTC"),
    ("report_date", "2026-10-25"), ("action_report_time", "impression"),
    ("attribution_windows", {"swipe": "7_DAY", "view": "7_DAY"}), ("sync_run_id", "other-run"),
    ("producer_owner_id", "other-worker"), ("source_mode", "legacy_normalized_zero"),
    ("status", "incomplete"), ("completed_requests", 0), ("expected_requests", True),
    ("provider_rows", 1), ("explicit_spend_present", True),
    ("window_start_utc", START - timedelta(hours=1)), ("window_end_utc", END + timedelta(hours=1)),
    ("observed_at", NOW + timedelta(seconds=1)), ("observed_at", NOW - timedelta(minutes=16)),
    ("requested_at", NOW - timedelta(minutes=16)),
    ("projection_generated_at", NOW - timedelta(days=1)),
])
async def test_wrong_stale_partial_or_mismatched_proof_is_not_zero(operational, field, value):
    operational.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].update_one(SCOPE, {"$set": {
        "operational_observation." + field: value}})
    result = await dashboard(operational)
    assert result["total_sar"] is None and result["quality"]["amount_available"] is False
    assert result["known_total_sar"] == 81.32
    assert result["hourly_sar"][DAY.isoformat()][0]["spend_sar"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["financial_partial", "run_failed", "worker_changed", "new_attempt",
    "newer_failed_run", "retained_positive_fact", "positive_projection", "positive_independent_total",
    "missing_reconciliation", "future_hour_zero", "missing_hour", "wrong_run_bounds"])
async def test_failed_superseded_or_contradictory_source_cannot_be_zero(operational, kind):
    db = operational
    projections, runs = db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION], db.raw[SNAPCHAT_SYNC_RUNS_COLLECTION]
    if kind == "financial_partial": runs.update_one(SCOPE, {"$set": {"financial_sync_status": "partial"}})
    elif kind == "run_failed": runs.update_one(SCOPE, {"$set": {"status": "failed"}})
    elif kind == "worker_changed": runs.update_one(SCOPE, {"$set": {"owner_id": "other-worker"}})
    elif kind == "new_attempt": projections.update_one(SCOPE, {"$set": {"operational_attempt_run_id": "new-run"}})
    elif kind == "newer_failed_run":
        db.raw[SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one({**SCOPE, "provider": "snapchat_ads", "sync_run_id": "new-run",
            "status": "failed", "started_at": NOW - timedelta(seconds=5), "request_window": {
                "date_from": DAY.isoformat(), "date_to": DAY.isoformat(), "action_report_time": "conversion"}})
    elif kind == "retained_positive_fact":
        db.raw[SNAPCHAT_HOURLY_FACTS_COLLECTION].insert_one({**SCOPE, "provider": "snapchat_ads", "entity_type": "ad_account",
            "external_id": "sar-account", "sync_run_id": "older-retained-run", "hour_start_utc": START,
            "action_report_time": "conversion", "attribution_windows": {"swipe": "28_DAY", "view": "7_DAY"}, "spend_native": 1})
    elif kind == "positive_projection": projections.update_one(SCOPE, {"$set": {"base_spend_native": 1}})
    elif kind == "positive_independent_total":
        db.raw[SNAPCHAT_RECONCILIATION_COLLECTION].update_one(SCOPE, {"$set": {"provider_riyadh_window_spend_native": 1}})
    elif kind == "missing_reconciliation": db.raw[SNAPCHAT_RECONCILIATION_COLLECTION].delete_many(SCOPE)
    elif kind == "future_hour_zero": projections.update_one(SCOPE, {"$set": {"operational_hours.1.spend_native": 0}})
    elif kind == "missing_hour": projections.update_one(SCOPE, {"$pop": {"operational_hours": 1}})
    elif kind == "wrong_run_bounds": runs.update_one(SCOPE, {"$set": {"request_window.end_utc": START}})
    result = await dashboard(db)
    assert result["total_sar"] is None and result["quality"]["amount_available"] is False
    assert result["hourly_sar"][DAY.isoformat()][0]["spend_sar"] is None


@pytest.mark.asyncio
async def test_foreign_positive_fact_does_not_cross_account_or_owner_scope(operational):
    for owner, account in [("foreign-owner", "sar-account"), ("owner", "different-account")]:
        operational.raw[SNAPCHAT_HOURLY_FACTS_COLLECTION].insert_one({"user_id": owner, "provider": "snapchat_ads",
            "ad_account_id": account, "entity_type": "ad_account", "external_id": account,
            "hour_start_utc": START, "action_report_time": "conversion", "spend_native": 99,
            "attribution_windows": {"swipe": "28_DAY", "view": "7_DAY"}})
    assert (await dashboard(operational))["total_sar"] == 81.32


@pytest.mark.asyncio
async def test_day_rollover_and_ranges_with_closed_days_do_not_gain_financial_proof(operational, monkeypatch):
    multi = await reader.load_snapchat_v2_dashboard_spend(operational, "owner", date_from=DAY - timedelta(days=1),
        date_to=DAY, timezone_name="Asia/Riyadh")
    assert multi["total_sar"] is None and multi["quality"]["complete"] is False
    monkeypatch.setattr(reader, "_today_in_timezone", lambda _: DAY + timedelta(days=1))
    monkeypatch.setattr(reader, "_operational_now", lambda: NOW + timedelta(days=1), raising=False)
    closed = await dashboard(operational)
    assert closed["total_sar"] is None and closed["quality"]["amount_available"] is False


@pytest.mark.asyncio
async def test_safe_operational_reason_reaches_route_through_actual_reader_and_portfolio(operational, monkeypatch):
    from unittest.mock import AsyncMock
    from integrations_control_center import dashboard_ads_platform_spend_routes as routes

    # Existing storage fixture exposes indexed access; these legacy route
    # collections additionally use attribute access. All reads still hit DB.
    for name in ("mezan_integration_accounts_v2", "mezan_integrations_v2", "counterparties", "ad_account_ledger", "tiktok_ads_daily"):
        monkeypatch.setattr(operational, name, operational[name], raising=False)

    async def actual_snapchat_reader(db, user_id, *, provider, **kwargs):
        assert provider == "snapchat_ads"
        return await reader.load_snapchat_v2_dashboard_spend(db, user_id, **kwargs)

    monkeypatch.setattr(routes, "load_unified_marketing_dashboard_spend", actual_snapchat_reader)
    monkeypatch.setattr(routes, "load_dashboard_unified_shadow", AsyncMock(return_value={}))
    result = await routes.build_dashboard_platform_spend(operational, "owner", date_from=DAY.isoformat(), date_to=DAY.isoformat())
    snap = result["providers"]["snapchat"]
    assert snap["total_sar"] == 81.32 and snap["amount_available"] is True and snap["provisional"] is True
    assert "snapchat_operational_no_activity_observed" in snap["reason_codes"]
    assert snap["amount_complete"] is False and snap["coverage_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("include_usd", [True, False], ids=["positive-usd-and-sar-zero", "sar-zero-only"])
async def test_actual_canonical_ads_snapshot_and_both_executive_builders_keep_unknown_metrics(operational, monkeypatch, include_usd):
    import dashboard_v2_routes as canonical
    from dashboard_v2_ads_executive import (build_salla_ads_executive_breakdown,
        build_salla_ads_executive_breakdown_cooperative)

    if not include_usd:
        operational.raw.mezan_integration_accounts_v2.update_one(
            {"user_id": "owner", "ad_account_id": "usd-account"}, {"$set": {"mezan_selected": False}})
    for name in ("mezan_integration_accounts_v2", "daily_costs"):
        monkeypatch.setattr(operational, name, operational[name], raising=False)

    async def actual_snapchat_reader(db, user_id, *, provider, **kwargs):
        assert provider == "snapchat_ads"
        return await reader.load_snapchat_v2_dashboard_spend(db, user_id, **kwargs)

    monkeypatch.setattr(canonical, "load_unified_marketing_dashboard_spend", actual_snapchat_reader)
    snapshot = await canonical.build_mezan_v2_ads(operational, "owner",
        from_date=DAY.isoformat(), to_date=DAY.isoformat())
    expected_spend = 81.32 if include_usd else 0
    snap = snapshot["providers"]["snapchat"]
    assert snapshot["total"] == expected_spend and snapshot["breakdown"]["snapchat"] == expected_spend
    assert snap["spend"] == expected_spend and snap["amount_available"] is True and snap["provisional"] is True
    assert snap["platform_reported_metrics_complete"] is False
    assert all(snap[key] is None for key in ("orders", "revenue", "impressions", "clicks",
        "roas", "cpa", "cost_per_order", "cpc", "cpm", "ctr"))
    assert snapshot["providers"]["meta"]["orders"] == 0  # Other provider semantics are unchanged.
    assert snapshot["spend_quality"]["amount_complete"] is False
    orders = [{"order_number": "salla-observed", "source": "snapchat", "total_amount": 100}]
    synchronous = build_salla_ads_executive_breakdown(orders, snapshot)
    cooperative = await build_salla_ads_executive_breakdown_cooperative(orders, snapshot)
    assert cooperative == synchronous
    for result in (synchronous, cooperative):
        provider = result["providers"]["snapchat"]
        assert provider["spend_sar"] == expected_spend
        assert provider["platform_reported_orders"] is None
        assert provider["platform_cost_per_order_sar"] is None
        assert provider["platform_reported_metrics_complete"] is False
        assert result["total"]["platform_reported_orders"] is None
        assert result["total"]["platform_cost_per_order_sar"] is None
        assert result["coverage"]["platform_cpa_denominator_complete"] is False
        assert result["coverage"]["spend_amount_complete"] is True
        assert result["total"]["spend_sar"] == expected_spend
        # Salla commerce observations remain available for operating profit;
        # only the unavailable platform conversion denominator stays unknown.
        assert provider["salla_orders"] == 1 and provider["salla_sales_sar"] == 100
        assert result["total"]["salla_sales_sar"] == 100
