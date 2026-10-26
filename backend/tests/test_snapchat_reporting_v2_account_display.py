
"""Account display uses authorized same-read evidence without changing finance."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from ads_manager.account_cost_settings import COLLECTION as COST_SETTINGS
from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.projections import SNAPCHAT_DAILY_PROJECTIONS_COLLECTION
from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from tests.test_snapchat_reporting_v2_midnight_zero_dashboard import DAY, NOW, dashboard, midnight
from tests.test_snapchat_reporting_v2_operational_dashboard import operational
from unified_marketing.readers import snapchat_v2 as reader


def label_accounts(db):
    for collection in (SNAPCHAT_ACCOUNTS_COLLECTION, "mezan_integration_accounts_v2"):
        for key, name in (("usd-account", "US storefront"), ("sar-account", "Saudi storefront")):
            db.raw[collection].update_one({"user_id": "owner", "ad_account_id": key},
                {"$set": {"display_name": name, "access_token": "synthetic-must-not-leak"}})
    db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].update_one(
        {"user_id": "owner", "ad_account_id": "usd-account"},
        {"$set": {"source_latest_updated_at": NOW - timedelta(seconds=30)}})


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name,sar_available", [("midnight", False), ("operational", True)])
async def test_actual_account_display_preserves_native_sar_hours_and_source_quality(request, monkeypatch, fixture_name, sar_available):
    db = request.getfixturevalue(fixture_name)
    label_accounts(db)
    before = deepcopy(list(db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].find()))
    single = AsyncMock(wraps=reader._load_snapchat_v2_single_account_dashboard_spend)
    projections = AsyncMock(wraps=reader.list_daily_projections)
    monkeypatch.setattr(reader, "_load_snapchat_v2_single_account_dashboard_spend", single)
    monkeypatch.setattr(reader, "list_daily_projections", projections)
    report = await dashboard(db)
    accounts = {row["account_id"]: row for row in report["accounts"]}
    assert set(accounts) == {"usd-account", "sar-account"}
    assert single.await_count == projections.await_count == 2
    usd, sar = accounts["usd-account"], accounts["sar-account"]
    assert usd["currency"] == "USD" and usd["timezone"] == "America/Los_Angeles"
    assert usd["projection_timezone"] == "Asia/Riyadh"
    assert usd["display_name"] == "US storefront"
    assert usd["total_native"] == 21.685333 and usd["total_sar"] == 81.32
    assert usd["quality"]["amount_available"] is True and usd["quality"]["provisional"] is True
    assert usd["quality"]["observed_at"] == NOW - timedelta(seconds=30)
    assert usd["hourly_sar"][DAY.isoformat()][0]["spend_sar"] == 81.32
    assert usd["hourly_sar"][DAY.isoformat()][1]["spend_sar"] is None
    assert sar["currency"] == "SAR" and sar["display_name"] == "Saudi storefront"
    assert sar["total_native"] == (0 if sar_available else None)
    assert sar["total_sar"] == (0 if sar_available else None)
    assert sar["daily_sar"][DAY.isoformat()] == (0 if sar_available else None)
    assert sar["hourly_sar"][DAY.isoformat()][0]["spend_sar"] == (0 if sar_available else None)
    assert sar["hourly_sar"][DAY.isoformat()][1]["spend_sar"] is None
    assert sar["quality"]["amount_available"] is sar_available
    assert sar["quality"]["complete"] is False and sar["quality"]["amount_complete"] is False
    assert sar["quality"]["observed_at"] == (NOW if sar_available else None)
    assert report["total_sar"] == (81.32 if sar_available else None)
    assert report["quality"]["amount_available"] is sar_available
    assert "observed_at" not in report["quality"]
    assert "synthetic-must-not-leak" not in repr(report["accounts"])
    assert all(not ({"user_id", "access_token", "purchases", "impressions"} & set(row)) for row in report["accounts"])
    assert list(db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].find()) == before
    assert report["provider_write_reached"] is False and report["accounting_write_reached"] is False


def clone_account(db, account_id, *, owner="owner", enabled=True, native=2):
    replacements = {"usd-account": account_id, "usd-run": account_id + "-run",
        "integration-usd": "integration-" + account_id}
    def remap(value):
        if isinstance(value, dict): return {key: remap(item) for key, item in value.items()}
        if isinstance(value, list): return [remap(item) for item in value]
        return replacements.get(value, value) if isinstance(value, str) else value
    for collection in (SNAPCHAT_ACCOUNTS_COLLECTION, "mezan_integration_accounts_v2",
                       COST_SETTINGS, SNAPCHAT_SYNC_RUNS_COLLECTION, SNAPCHAT_DAILY_PROJECTIONS_COLLECTION):
        field = "external_account_id" if collection == COST_SETTINGS else "ad_account_id"
        row = deepcopy(db.raw[collection].find_one({"user_id": "owner", field: "usd-account"}))
        row.pop("_id")
        row = remap(row)
        row["user_id"] = owner
        row["display_name"] = "Separate USD account"
        if "mezan_selected" in row: row["mezan_selected"] = enabled
        if "selected" in row: row["selected"] = False
        if "base_spend_native" in row:
            row["base_spend_native"] = native
            row["hours"][0]["spend_native"] = native
        db.raw[collection].insert_one(row)


@pytest.mark.asyncio
async def test_identity_is_account_id_and_foreign_or_disabled_accounts_are_excluded(midnight):
    label_accounts(midnight)
    clone_account(midnight, "usd-second")
    clone_account(midnight, "usd-disabled", enabled=False)
    clone_account(midnight, "usd-foreign", owner="other-owner")
    report = await dashboard(midnight)
    accounts = {row["account_id"]: row for row in report["accounts"]}
    assert set(accounts) == {"usd-account", "sar-account", "usd-second"}
    assert accounts["usd-account"]["total_sar"] == 81.32
    assert accounts["usd-second"]["currency"] == "USD"
    assert accounts["usd-second"]["total_native"] == 2 and accounts["usd-second"]["total_sar"] == 7.5
    assert accounts["usd-second"]["hourly_sar"][DAY.isoformat()][0]["spend_sar"] == 7.5
    assert report["known_total_sar"] == 88.82 and report["total_sar"] is None


@pytest.mark.asyncio
async def test_explicit_account_view_reuses_single_authorized_read_and_does_not_list_other_accounts(operational, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("extra account listing"))
    monkeypatch.setattr(reader, "list_enabled_accounts", forbidden)
    result = await reader.load_snapchat_v2_dashboard_spend(operational, "owner",
        date_from=DAY, date_to=DAY, timezone_name="Asia/Riyadh", account_id="sar-account")
    assert len(result["accounts"]) == 1 and result["accounts"][0]["account_id"] == "sar-account"
    assert result["accounts"][0]["total_native"] == result["accounts"][0]["total_sar"] == 0
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_account_contract_contains_no_disabled_selected_account_expense(midnight, monkeypatch):
    monkeypatch.setattr(reader, "list_enabled_accounts", AsyncMock(return_value=[]))
    result = await dashboard(midnight)
    assert result["accounts"] == [] and result["total_sar"] is None
    assert result.get("account_display") is None and result.get("total_native") is None


@pytest.mark.asyncio
async def test_platform_display_contract_and_canonical_financial_snapshot_stay_separate(operational, monkeypatch):
    import dashboard_v2_routes as canonical
    from integrations_control_center import dashboard_ads_platform_spend_routes as routes
    for name in ("mezan_integration_accounts_v2", "mezan_integrations_v2", "counterparties",
                 "ad_account_ledger", "tiktok_ads_daily", "daily_costs"):
        monkeypatch.setattr(operational, name, operational[name], raising=False)
    report = await dashboard(operational)
    async def source(db, user_id, *, provider, **kwargs):
        assert provider == "snapchat_ads" and user_id == "owner"
        return report
    monkeypatch.setattr(routes, "load_unified_marketing_dashboard_spend", source)
    monkeypatch.setattr(routes, "load_dashboard_unified_shadow", AsyncMock(return_value={}))
    spend = await routes.build_dashboard_platform_spend(operational, "owner",
        date_from=DAY.isoformat(), date_to=DAY.isoformat())
    assert [row["account_id"] for row in spend["providers"]["snapchat"]["accounts"]] == [row["account_id"] for row in report["accounts"]]
    monkeypatch.setattr(canonical, "load_unified_marketing_dashboard_spend", source)
    full = await canonical.build_mezan_v2_ads(operational, "owner", from_date=DAY.isoformat(), to_date=DAY.isoformat())
    assert "accounts" not in full["providers"]["snapchat"]
    original = deepcopy(report)
    original.pop("total_native", None)
    original.pop("display_observed_at", None)
    original.pop("account_display", None)
    original["accounts"] = [{key: row[key] for key in ("account_id", "display_name", "currency", "timezone", "total_sar")}
        | {"quality": {key: value for key, value in row["quality"].items() if key != "observed_at"}}
        for row in report["accounts"]]
    report = original
    legacy = await canonical.build_mezan_v2_ads(operational, "owner", from_date=DAY.isoformat(), to_date=DAY.isoformat())
    assert full == legacy


def test_display_metadata_is_scalar_and_does_not_expose_nested_account_material():
    secret = {"access_token": "synthetic-must-not-leak"}
    for reasons in ([secret, "expected_reason"], None):
        display = reader._account_display_summary(
            {"ad_account_id": secret, "display_name": secret, "currency": secret, "timezone": secret},
            {"total_sar": 1, "total_native": 1, "quality": {"amount_available": True, "reason_codes": reasons}},
        )
        assert display["account_id"] == display["display_name"] == ""
        assert display["currency"] is None and display["timezone"] is None
        assert display["quality"]["reason_codes"] == (["expected_reason"] if reasons else [])
        assert "synthetic-must-not-leak" not in str(display)
    assert reader._account_display_amount(1 << 5000) is None
