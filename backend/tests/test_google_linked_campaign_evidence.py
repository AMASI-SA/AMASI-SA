from copy import deepcopy
from datetime import datetime, timezone

import pytest
from snapchat_v2.models import build_attribution_key

from integrations_control_center.google_linked_campaign_evidence import snap_campaign_day

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def source():
    start = datetime(2026, 10, 27, 21, tzinfo=timezone.utc)
    end = datetime(2026, 10, 28, 21, tzinfo=timezone.utc)
    finished = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
    row = dict(user_id="owner", provider="snapchat_ads", ad_account_id="account",
               entity_type="campaign", external_id="campaign", campaign_id="campaign",
               report_date="2026-10-28", account_timezone="Asia/Riyadh", currency="SAR",
               action_report_time="conversion", attribution_key=build_attribution_key("conversion", {"swipe": "28_DAY", "view": "7_DAY"}),
               attribution_windows={"swipe": "28_DAY", "view": "7_DAY"},
               window_start_utc=start, window_end_utc=end, observed_at=finished,
               sync_run_id="run", coverage={"status": "complete"}, source={"api": "snapchat_marketing_api", "granularity": "DAY", "breakdown": "campaign"},
               spend_native=12.5, purchases=3, purchase_value_native=90)
    run = dict(user_id="owner", provider="snapchat_ads", ad_account_id="account",
               sync_run_id="run", status="complete", financial_sync_status="complete",
               campaign_sync_status="complete", ad_squad_sync_status="complete", ad_sync_status="complete",
               finished_at=finished, request_window={"date_from": "2026-10-28", "date_to": "2026-10-28", "start_utc": start, "end_utc": end,
                                                    "action_report_time": "conversion"})
    snap = dict(user_id="owner", provider="snapchat_ads", ad_account_id="account",
                report_date="2026-10-28", account_timezone="Asia/Riyadh", action_report_time="conversion",
                attribution_key=row["attribution_key"], sync_run_id="run", finished_at=finished,
                rows={"campaign": [row]})
    return snap, run


def check(snapshot, run):
    return snap_campaign_day(snapshot, run, owner_id="owner", account_id="account",
                             campaign_id="campaign", as_of=NOW)


def test_native_context_is_not_product_performance_or_store_orders():
    snapshot, run = source()
    result = check(snapshot, run)
    assert result["metrics"] == {"spend_native": 12.5, "provider_reported_purchases": 3,
                                 "provider_reported_purchase_value_native": 90}
    assert result["currency"] == "SAR"
    assert result["independent_store_orders"] is None
    assert result["product_attribution_verified"] is False
    assert result["profitability"] is None


@pytest.mark.parametrize("target,field,value", [
    ("run", "user_id", "other"), ("run", "ad_account_id", "other"),
    ("run", "status", "running"), ("run", "campaign_sync_status", "partial"),
    ("run", "sync_run_id", "different"), ("snapshot", "sync_run_id", "different"),
    ("row", "currency", ""), ("row", "spend_native", None),
    ("row", "purchases", True), ("row", "purchase_value_native", float("nan")),
    ("row", "account_timezone", "UTC"), ("row", "user_id", "other"),
    ("row", "observed_at", datetime(2026, 10, 28, 12, tzinfo=timezone.utc)),
])
def test_rejects_unproven_or_mismatched_source(target, field, value):
    snapshot, run = source()
    obj = {"run": run, "snapshot": snapshot, "row": snapshot["rows"]["campaign"][0]}[target]
    obj[field] = value
    with pytest.raises(ValueError):
        check(snapshot, run)


def test_absent_campaign_is_not_zero_and_duplicate_campaign_is_not_summed():
    snapshot, run = source()
    snapshot["rows"]["campaign"] = []
    with pytest.raises(ValueError, match="campaign_row"):
        check(snapshot, run)


def test_canonical_zero_cannot_prove_explicit_provider_zero_after_collector_defaults():
    snapshot, run = source()
    snapshot["rows"]["campaign"][0]["purchases"] = 0
    result = check(snapshot, run)
    assert result["metrics"]["provider_reported_purchases"] is None
    assert result["metric_provenance"]["provider_reported_purchases"] == "zero_or_source_omission_unproved"
    snapshot, run = source()
    snapshot["rows"]["campaign"].append(deepcopy(snapshot["rows"]["campaign"][0]))
    with pytest.raises(ValueError, match="campaign_row"):
        check(snapshot, run)
