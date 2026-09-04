from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from integrations_control_center import snapchat_account_timezone_manager as manager
from integrations_control_center.snapchat_campaign_result_source_routes import (
    _match_order_campaign,
    _unique_lookup,
)
from integrations_control_center.snapchat_campaign_truth_contract import (
    apply_campaign_truth_contract,
)
from integrations_control_center.snapchat_platform_source_integrity import (
    _scope_campaigns,
)


def _payload() -> dict:
    return {
        "request_id": "request-2",
        "selected_account_id": "account-1",
        "selected_account_name": "Primary",
        "date_from": "2026-09-03",
        "date_to": "2026-09-03",
        "business_timezone": "Asia/Riyadh",
        "account_timezone": "America/Los_Angeles",
        "salla_status": "complete",
        "snapchat_status": "complete",
        "matching_status": "complete",
        "salla_as_of": "2026-09-04T00:10:00+00:00",
        "snapchat_as_of": "2026-09-04T00:05:00+00:00",
        "totals": {
            "salla_total_orders": 25,
            "salla_matched_orders": 1,
            "salla_sales_sar": 132.92,
            "snapchat_purchases": 2,
            "snapchat_purchase_value_sar": 300.0,
            "snapchat_spend_sar": 100.0,
            "orders": 66,
            "sales_sar": 17_971.72,
            "roas": 16.94,
        },
        "campaigns": [{
            "account_id": "account-1",
            "campaign_id": "c7-campaign",
            "campaign_name": "C7",
            "salla_results": {"orders": 1, "sales_sar": 132.92},
            "platform_results": {"orders": 2, "sales_sar": 300.0},
            "snapchat_spend_sar": 100.0,
            "profitability": {
                "orders": 1,
                "sales_sar": 132.92,
                "product_cost_sar": None,
                "known_product_cost_sar": 0.0,
            },
        }],
        "daily": [{
            "date": "2026-09-03",
            "salla_matched_orders": 1,
            "salla_sales_sar": 132.92,
            "snapchat_purchases": 2,
            "snapchat_purchase_value_sar": 300.0,
            "snapchat_spend_sar": 100.0,
        }],
        "accounts": [{"account_id": "account-1"}],
        "source": {
            "platform_total_snapshot_ready": True,
            "platform_source_status": "complete",
            "platform_account_spend_sar": 100.0,
            "platform_campaign_spend_sar": 100.0,
            "salla_campaign_matched_orders": 1,
            "salla_attribution": {
                "salla_total_orders": 25,
                "salla_matched_orders": 1,
                "salla_unmatched_orders": 24,
                "matching_status": "complete",
                "matching_reason": "literal_utm_campaign_id_only",
            },
        },
    }


def test_selected_source_fields_never_use_generic_commercial_aliases():
    result = apply_campaign_truth_contract(_payload())
    assert result["totals"]["orders"] is None
    assert result["totals"]["sales_sar"] is None
    assert result["campaigns"][0]["orders"] is None


def test_same_business_date_retains_separate_riyadh_and_la_timezones():
    result = apply_campaign_truth_contract(_payload())
    assert result["effective_timezone"] == "Asia/Riyadh"
    assert result["account_timezone"] == "America/Los_Angeles"
    assert result["salla_attribution_timezone"] == "Asia/Riyadh"


def test_request_id_and_selected_account_are_immutable_contract_fields():
    result = apply_campaign_truth_contract(_payload())
    assert (result["request_id"], result["selected_account_id"]) == (
        "request-2", "account-1"
    )


@pytest.mark.parametrize(
    "campaign_id",
    [
        "aa3cdaec-b950-4df3-b28d-c3502cd2bf7b",
        "8ae1e7cd-db61-41bd-af15-9f52824709d6",
    ],
)
def test_reported_missing_campaign_ids_survive_active_scope_when_active(campaign_id):
    campaigns = [{
        "campaign_id": campaign_id,
        "campaign_name": campaign_id,
        "campaign_active": True,
        "snapchat_spend_sar": None,
    }]
    assert _scope_campaigns(
        campaigns,
        active_campaigns_only=True,
        campaign_query=None,
    )[0]["campaign_id"] == campaign_id


def test_matched_order_count_cannot_exceed_salla_total_incident_a():
    payload = _payload()
    payload["source"]["salla_attribution"].update({
        "salla_total_orders": 25,
        "salla_matched_orders": 66,
    })
    result = apply_campaign_truth_contract(payload)
    assert result["matching_status"] == "failed"
    assert result["totals"]["salla_matched_orders"] is None
    assert result["totals"]["salla_roas"] is None


def test_one_order_132_92_is_cost_incomplete_not_no_match_incident_e():
    campaign = apply_campaign_truth_contract(_payload())["campaigns"][0]
    assert campaign["salla_orders"] == 1
    assert campaign["salla_sales_sar"] == 132.92
    assert campaign["cost_status"] == "cost_incomplete"


def test_salla_and_snapchat_roas_are_distinct_and_named():
    totals = apply_campaign_truth_contract(_payload())["totals"]
    assert totals["salla_roas"] == 1.3292
    assert totals["snapchat_roas"] == 3.0


def test_fixed_spend_cannot_make_stale_salla_change_current_roas_incident_b():
    payload = _payload()
    payload["totals"].update({
        "salla_sales_sar": 17_971.72,
        "snapchat_spend_sar": 1_060.85,
    })
    payload["source"]["platform_source_status"] = "stale"
    assert apply_campaign_truth_contract(payload)["totals"]["salla_roas"] is None


@pytest.mark.parametrize("status", ["partial", "stale", "failed"])
def test_snapchat_noncomplete_status_fails_closed_for_salla_ratios(status):
    payload = _payload()
    payload["source"]["platform_source_status"] = status
    totals = apply_campaign_truth_contract(payload)["totals"]
    assert totals["salla_roas"] is None
    assert totals["salla_cpa_sar"] is None


def test_salla_success_remains_visible_when_snapchat_fails():
    payload = _payload()
    payload["source"]["platform_source_status"] = "failed"
    result = apply_campaign_truth_contract(payload)
    assert result["salla_status"] == "complete"
    assert result["totals"]["salla_sales_sar"] == 132.92
    assert result["totals"]["salla_roas"] is None


def test_snapchat_success_remains_visible_when_salla_fails():
    payload = _payload()
    payload["salla_status"] = "failed"
    result = apply_campaign_truth_contract(payload)
    assert result["snapchat_status"] == "complete"
    assert result["totals"]["snapchat_spend_sar"] == 100.0
    assert result["totals"]["snapchat_roas"] == 3.0


def test_no_provider_data_is_unknown_not_a_false_zero():
    payload = _payload()
    payload["source"].update({
        "platform_total_snapshot_ready": False,
        "platform_source_status": "partial",
    })
    payload["totals"].update({
        "snapchat_purchases": None,
        "snapchat_purchase_value_sar": None,
        "snapchat_spend_sar": None,
    })
    totals = apply_campaign_truth_contract(payload)["totals"]
    assert totals["snapchat_purchases"] is None
    assert totals["snapchat_spend_sar"] is None


def test_missing_salla_coverage_counts_fail_closed():
    payload = _payload()
    payload["source"]["salla_attribution"].pop("salla_total_orders")
    payload["source"]["salla_attribution"].pop("salla_matched_orders")
    payload["totals"]["salla_total_orders"] = None
    payload["totals"]["salla_matched_orders"] = None
    result = apply_campaign_truth_contract(payload)
    assert result["matching_status"] == "partial"
    assert result["reconciliation_status"] == "unreconciled"
    assert result["totals"]["salla_roas"] is None


def test_as_of_timestamps_are_kept_for_source_labels():
    result = apply_campaign_truth_contract(_payload())
    assert result["salla_as_of"].startswith("2026-09-04")
    assert result["snapchat_as_of"].startswith("2026-09-04")


def test_profitability_cache_is_explicitly_disabled():
    result = apply_campaign_truth_contract(_payload())
    assert result["policy"]["financial_cache_status"] == (
        "disabled_for_source_coherence"
    )


def test_late_arriving_salla_order_changes_contract_without_cache_reuse():
    first = apply_campaign_truth_contract(_payload())
    next_payload = _payload()
    next_payload["source"]["salla_attribution"].update({
        "salla_total_orders": 26,
        "salla_matched_orders": 2,
    })
    next_payload["totals"].update({
        "salla_total_orders": 26,
        "salla_matched_orders": 2,
        "salla_sales_sar": 232.92,
    })
    second = apply_campaign_truth_contract(next_payload)
    assert first["totals"]["salla_matched_orders"] == 1
    assert second["totals"]["salla_matched_orders"] == 2


def test_exact_utm_id_is_literal_case_sensitive_and_whitespace_sensitive():
    lookup = _unique_lookup([{
        "account_id": "account-1",
        "campaign_id": "Campaign_ABC",
    }], "campaign_id")
    assert _match_order_campaign(
        {"utm_campaign_id": "Campaign_ABC"}, id_lookup=lookup, name_lookup={}
    )[0] == ("account-1", "Campaign_ABC")
    assert _match_order_campaign(
        {"utm_campaign_id": "campaign abc"}, id_lookup=lookup, name_lookup={}
    )[0] is None
    assert _match_order_campaign(
        {"utm_campaign_id": " Campaign_ABC "}, id_lookup=lookup, name_lookup={}
    )[0] is None


def test_campaign_name_or_generic_campaign_field_never_matches():
    lookup = {"campaign-1": ("account-1", "campaign-1")}
    for order in (
        {"utm_campaign": "campaign-1"},
        {"campaign_id": "campaign-1"},
        {"campaign_name": "campaign-1", "source": "snapchat"},
    ):
        assert _match_order_campaign(
            order, id_lookup=lookup, name_lookup=lookup
        ) == (None, "unmatched")


def test_updated_at_is_not_used_as_order_creation_time():
    assert manager._order_timestamp({
        "updated_at": "2026-09-04T00:01:00+00:00",
        "order_date": "2026-09-03",
    }) is None


def test_active_filter_runs_before_page_slice():
    campaigns = [
        {"campaign_id": "inactive", "campaign_name": "A", "campaign_active": False, "spend_sar": 999},
        {"campaign_id": "active-1", "campaign_name": "B", "campaign_active": True, "spend_sar": 2},
        {"campaign_id": "active-2", "campaign_name": "C", "campaign_active": True, "spend_sar": 1},
    ]
    scoped = _scope_campaigns(
        campaigns, active_campaigns_only=True, campaign_query=None
    )
    assert [row["campaign_id"] for row in scoped[:1]] == ["active-1"]


def test_campaign_search_is_applied_before_pagination():
    campaigns = [
        {"campaign_id": f"campaign-{index}", "campaign_name": "needle" if index == 40 else "other", "campaign_active": True, "spend_sar": index}
        for index in range(50)
    ]
    scoped = _scope_campaigns(
        campaigns, active_campaigns_only=True, campaign_query="needle"
    )
    assert [row["campaign_id"] for row in scoped] == ["campaign-40"]


def test_source_status_vocabulary_is_bounded():
    for status in ("complete", "partial", "stale", "failed"):
        payload = _payload()
        payload["source"]["platform_source_status"] = status
        assert apply_campaign_truth_contract(payload)["snapchat_status"] == status


def test_action_report_time_does_not_change_salla_fields():
    conversion = _payload()
    conversion["action_report_time"] = "conversion"
    impression = deepcopy(conversion)
    impression["action_report_time"] = "impression"
    assert apply_campaign_truth_contract(conversion)["totals"]["salla_sales_sar"] == (
        apply_campaign_truth_contract(impression)["totals"]["salla_sales_sar"]
    )


def test_historical_snapshot_can_be_complete_when_source_windows_reconcile():
    result = apply_campaign_truth_contract(_payload())
    assert result["reconciliation_status"] == "reconciled"
    assert result["reconciliation_reasons"] == []


def test_account_campaign_spend_mismatch_is_explicit_and_fails_closed():
    payload = _payload()
    payload["source"]["platform_campaign_spend_sar"] = 90.0
    result = apply_campaign_truth_contract(payload)
    assert result["reconciliation_status"] == "unreconciled"
    assert result["reconciliation"]["snapchat_account_campaign_spend_delta_sar"] == 10.0
    assert "snapchat_account_campaign_spend_mismatch" in result["reconciliation_reasons"]
    assert result["totals"]["salla_roas"] is None


def test_salla_matched_campaign_sum_mismatch_is_explicit():
    payload = _payload()
    payload["source"]["salla_campaign_matched_orders"] = 0
    result = apply_campaign_truth_contract(payload)
    assert result["reconciliation"]["salla_matched_campaign_delta"] == 1
    assert "salla_matched_campaign_sum_mismatch" in result["reconciliation_reasons"]


def test_report_contract_is_read_only():
    result = apply_campaign_truth_contract(_payload())
    assert result["policy"]["provider_write_reached"] is False
    assert result["policy"]["accounting_write_reached"] is False
    assert result["policy"]["qoyod_write_reached"] is False


def test_incident_b_old_generic_values_cannot_leak_to_current_fields():
    payload = _payload()
    payload["totals"].update({"orders": 66, "sales_sar": 17_971.72, "roas": 16.94})
    totals = apply_campaign_truth_contract(payload)["totals"]
    assert (totals["orders"], totals["sales_sar"], totals["roas"]) == (
        None, None, None
    )


def test_snapshot_contract_contains_all_required_campaign_source_fields():
    campaign = apply_campaign_truth_contract(_payload())["campaigns"][0]
    required = {
        "salla_orders", "salla_sales_sar", "snapchat_purchases",
        "snapchat_purchase_value_sar", "snapchat_spend_sar",
        "salla_roas", "snapchat_roas",
    }
    assert required <= set(campaign)


def test_snapshot_contract_contains_all_required_top_level_status_fields():
    result = apply_campaign_truth_contract(_payload())
    required = {
        "request_id", "selected_account_id", "selected_account_name",
        "date_from", "date_to", "effective_timezone", "salla_as_of",
        "snapchat_as_of", "salla_status", "snapchat_status",
        "matching_status", "reconciliation_status",
    }
    assert required <= set(result)


def test_pr1004_spend_fields_remain_snapchat_owned():
    result = apply_campaign_truth_contract(_payload())
    assert result["totals"]["snapchat_spend_sar"] == 100.0
    assert result["campaigns"][0]["snapchat_spend_sar"] == 100.0


def test_current_timestamp_shape_is_timezone_aware_for_fixture_sanity():
    assert datetime.fromisoformat(_payload()["snapchat_as_of"]).tzinfo == timezone.utc
