"""Independent frozen-fixture oracles for the reviewed Google read extension.

The immutable fixture stores 11.115 on two days: its period is 22.23 while
its displayed days are each 11.12. No production Google reader is imported.
"""
import copy
import json
from datetime import date, timedelta

import pytest

from dashboard_runtime_gate_plugin import (
    _extend_google_saved_contract,
    _expected_google_commands,
    _google_command,
    assert_runtime_read_contract,
    expected_cost_completeness_json,
)


OLD_SOURCE = "daily_costs.google_ads:transitional_read_only:no_account_allocation"
SAVED_SOURCE = ("mezan_google_ads_performance_daily_v2:selected_accounts:stored_sar:"
                "daily_costs.google_ads_positive_legacy_days_only")
NATIVE = "mezan_google_ads_performance_daily_v2"
DATABASE = "dashboard_b_shared_google_contract"


def historical_response(*, total=22.23, days=8, start="2026-10-01", observed=None):
    if observed is None:
        observed = {"2026-10-01": 11.12, "2026-10-08": 11.12}
    dates = [(date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(days)]
    ads_total = round(225.60 + total, 2)
    return {
        "totals": {"total_sales": 1000.0, "net_sales": 900.0, "net_profit": 80.0,
                   "total_ads_cost": ads_total, "daily_ads_total": ads_total,
                   "daily_costs_total": round(889.89 + ads_total, 2),
                   "ads_spend_data_complete": True, "ads_spend_provisional": False},
        "product_cost_v2": {"missing_all_cost_products_count": 0,
                            "no_products_orders_count": 0, "product_rows": []},
        "net_sales_config": {"deduct_product_costs": True},
        "ads_v2": {
            "total": ads_total, "known_subtotal_sar": ads_total,
            "breakdown": {"meta": 150.40, "tiktok": 75.20, "snapchat": 0.0,
                          "google_transitional": total},
            "history": [{"date": day, "snapchat": 0.0, "meta": 0.0, "tiktok": 0.0,
                         "google": observed.get(day, 0.0)} for day in dates],
            "providers": {"meta": {"spend": 150.40}, "tiktok": {"spend": 75.20},
                          "snapchat": {"spend": 0.0, "amount_complete": True,
                                       "amount_available": True, "provisional": False}},
            "spend_quality": {
                "status": "complete", "amount_complete": True, "amount_available": True,
                "provisional": False, "known_subtotal_sar": ads_total,
                "snapchat": {"data_state": "not_required", "amount_complete": True,
                             "amount_available": True, "provisional": False},
            },
            "bank_commissions": {"total_fee_sar": 5.20, "total_effective_spend_sar": 225.60,
                                 "google_transitional_spend_sar": total,
                                 "google_account_allocation": "not_available" if total else "not_required"},
            "source_contract": {"snapchat": "unchanged-snapchat-contract", "google": OLD_SOURCE},
            "executive_breakdown": {
                "providers": {"google": {"spend_sar": float(total), "salla_sales": 100.0,
                                          "data_state": None, "coverage_complete": None,
                                          "amount_complete": None},
                              "snapchat": {"spend_sar": 0.0, "salla_sales": 75.0}},
                "total": {"spend_sar": ads_total, "salla_sales": 175.0},
            },
        },
        "source_contract": {"advertising": {"snapchat": "unchanged-snapchat-contract", "google": OLD_SOURCE}},
        "unrelated": {"selected_statuses": ["completed"], "precision": 3.335},
    }


def approved_response(old):
    """Literal accepted quality contract; financial values are copied unchanged."""
    result = copy.deepcopy(old)
    ads = result["ads_v2"]
    observed = sum(row["google"] > 0 for row in ads["history"])
    positive = observed > 0
    quality = {
        "status": "provisional" if positive else "not_required",
        "data_state": "provisional_data" if positive else "not_required",
        "amount_available": True, "amount_complete": not positive,
        "coverage_complete": not positive, "provisional": positive,
        "expected": False,
        "reason_codes": ["google_legacy_manual_fallback"] if positive else [],
        "last_observed_at": None, "observed_days": observed,
        "requested_days": len(ads["history"]), "account_count": 0,
    }
    for row in ads["history"]:
        if row["google"] == 0:
            row["google"] = None
    # Runtime exposes Google before Snapchat. Member order is part of the gate.
    ads["providers"] = {
        "meta": ads["providers"]["meta"], "tiktok": ads["providers"]["tiktok"],
        "google": {"spend": ads["breakdown"]["google_transitional"], **quality},
        "snapchat": ads["providers"]["snapchat"],
    }
    ads["spend_quality"].update(status="provisional" if positive else "complete",
                                amount_complete=not positive, provisional=positive,
                                google=copy.deepcopy(quality))
    ads["source_contract"]["google"] = SAVED_SOURCE
    result["source_contract"]["advertising"]["google"] = SAVED_SOURCE
    ads["executive_breakdown"]["providers"]["google"].update(
        data_state=quality["data_state"], coverage_complete=not positive,
        amount_complete=not positive)
    result["totals"].update(ads_spend_data_complete=not positive, ads_spend_provisional=positive)
    return result


def pinned_commands(owner="fixture-owner", start="2026-10-01", end="2026-10-08"):
    """Expected wire queries pinned independently of helper/plugin constants."""
    period = {"$gte": start, "$lte": end}
    queries = [
        ("mezan_integrations_v2", {"user_id": owner, "provider": "google_ads"},
         {"_id": 0, "connection_status": 1,
          "account_selection_configured": 1, "selected_account_ids": 1}),
        ("mezan_integration_accounts_v2",
         {"user_id": owner, "provider": "google_ads", "connection_status": "connected",
          "connection_provenance": "api_connection"},
         {"_id": 0, "ad_account_id": 1, "external_account_id": 1, "mezan_selected": 1,
          "currency": 1, "timezone": 1}),
        ("mezan_google_ads_performance_daily_v2",
         {"user_id": owner, "provider": "google_ads", "date": period},
         {"_id": 0, "ad_account_id": 1, "date": 1, "source_mode": 1, "account_timezone": 1,
          "currency_native": 1, "spend_native": 1, "spend_sar": 1, "fx_rate_to_sar": 1,
          "source_close_proof": 1, "observed_at": 1, "updated_at": 1}),
        ("daily_costs", {"user_id": owner, "date": period}, {"_id": 0, "date": 1, "google_ads": 1}),
    ]
    return [{"operation": "find", "collection": collection, "database": DATABASE,
             "command_database": DATABASE, "filter": query, "projection": projection,
             "limit": None, "unreviewed_fields": []} for collection, query, projection in queries]


def evidence_pair(*, zero=False, owner="fixture-owner"):
    old = historical_response(total=0, observed={}) if zero else historical_response()
    baseline = {
        "json": json.dumps(old), "commands": {"find": 8}, "documents": 7,
        # Shared collections contain other platform reads, which must survive.
        "collections": {"settings": 2, "daily_costs": 4, "mezan_integrations_v2": 0,
                        "mezan_integration_accounts_v2": 1},
    }
    response = approved_response(old)
    response["totals"]["product_cost_complete"] = True
    runtime = copy.deepcopy(baseline)
    runtime["json"] = json.dumps(response)
    runtime["commands"]["find"] += 3  # Four Google reads replace one legacy read.
    runtime["collections"][NATIVE] = 0
    runtime["cost_authority_audit"] = {
        "version": 1, "owner_id": owner, "database": DATABASE,
        "cohorts": [{"user_id": owner, "orders": []}], "commands": [],
        "traffic_cohorts": [{"user_id": owner, "numbers": []}], "traffic_commands": [],
        "google_cohorts": [{"user_id": owner, "date_from": "2026-10-01", "date_to": "2026-10-08"}],
        "google_commands": pinned_commands(owner),
    }
    return runtime, baseline


@pytest.mark.parametrize("kwargs", [
    {},
    {"total": 0, "observed": {}},
    {"total": 11.12, "start": "2026-09-01", "days": 30, "observed": {"2026-09-30": 11.12}},
    {"total": 11.12, "start": "2026-10-08", "days": 1, "observed": {"2026-10-08": 11.12}},
    {"total": 40.0, "observed": {"2026-10-05": 40.0}},
    {"total": 10.0, "observed": {"2026-10-05": 10.0}},
])
def test_google_oracle_changes_only_reviewed_quality_and_preserves_money_and_order(kwargs):
    old = historical_response(**kwargs)
    result = copy.deepcopy(old)
    _extend_google_saved_contract(result)
    assert json.dumps(result) == json.dumps(approved_response(old))
    assert result["ads_v2"]["breakdown"] == old["ads_v2"]["breakdown"]
    assert result["ads_v2"]["bank_commissions"] == old["ads_v2"]["bank_commissions"]
    if not kwargs:
        assert result["ads_v2"]["breakdown"]["google_transitional"] == 22.23
        assert [r["google"] for r in result["ads_v2"]["history"]] == [11.12, None, None, None, None, None, None, 11.12]


def test_missing_google_is_not_observed_zero_and_other_owner_contract_is_available():
    runtime, baseline = evidence_pair(zero=True, owner="fixture-other")
    assert_runtime_read_contract(runtime, baseline)
    result = json.loads(runtime["json"])
    google = result["ads_v2"]["providers"]["google"]
    assert google["status"] == "not_required" and google["observed_days"] == 0
    assert google["amount_available"] is True and google["expected"] is False
    assert type(result["ads_v2"]["breakdown"]["google_transitional"]) is int
    assert result["totals"]["ads_spend_data_complete"] is True


def test_four_exact_queries_have_only_three_net_finds_and_preserve_shared_counts():
    runtime, baseline = evidence_pair()
    before = copy.deepcopy((runtime, baseline))
    audit = runtime["cost_authority_audit"]
    assert _expected_google_commands(audit, json.loads(baseline["json"])) == pinned_commands()
    assert_runtime_read_contract(runtime, baseline)
    # Arrival order of asyncio.gather reads is deliberately not a contract.
    audit["google_commands"][:] = audit["google_commands"][2:] + audit["google_commands"][:2]
    assert_runtime_read_contract(runtime, baseline)
    audit["google_commands"][:] = pinned_commands()
    assert (runtime, baseline) == before


def test_permission_failure_has_no_google_cohort_or_query():
    historical = {"http_error": 403, "detail": "Forbidden"}
    unchanged = copy.deepcopy(historical)
    _extend_google_saved_contract(unchanged)
    assert unchanged == historical
    runtime, baseline = evidence_pair()
    for value in (runtime, baseline):
        value.update(json=json.dumps(historical), commands={}, documents=0, collections={})
    audit = runtime["cost_authority_audit"]
    for name in ("cohorts", "traffic_cohorts", "google_cohorts", "google_commands"):
        audit[name] = []
    assert_runtime_read_contract(runtime, baseline)
    audit["google_cohorts"] = [{"user_id": "fixture-owner", "date_from": "2026-10-01", "date_to": "2026-10-08"}]
    with pytest.raises(AssertionError):
        assert_runtime_read_contract(runtime, baseline)


@pytest.mark.parametrize("defect", [
    "query_owner", "coherent_wrong_owner", "missing_owner", "wider_date", "coherent_wrong_date",
    "provider", "account_status", "account_provenance", "projection", "selection_marker_missing",
    "selection_ids_missing", "collection", "missing_read",
    "extra_read", "write", "extra_option", "second_cohort", "command_database", "event_database",
    "native_documents", "shared_account_documents", "legacy_documents", "untracked_find", "get_more",
    "unknown_collection", "missing_native_evidence", "subtract_four", "money", "google_history_zero",
    "google_quality", "observed_days", "snapchat_quality", "unrelated_json", "zero_numeric_type",
])
def test_google_gate_rejects_unreviewed_scope_reads_counts_and_json(defect):
    runtime, baseline = evidence_pair(zero=defect == "zero_numeric_type")
    audit = runtime["cost_authority_audit"]
    commands = audit["google_commands"]
    if defect == "query_owner":
        commands[2]["filter"]["user_id"] = "foreign"
    elif defect == "selection_marker_missing":
        del commands[0]["projection"]["account_selection_configured"]
    elif defect == "selection_ids_missing":
        del commands[0]["projection"]["selected_account_ids"]
    elif defect == "coherent_wrong_owner":
        audit["google_cohorts"][0]["user_id"] = "foreign"
        for command in commands:
            command["filter"]["user_id"] = "foreign"
    elif defect == "missing_owner":
        del commands[3]["filter"]["user_id"]
    elif defect in {"wider_date", "coherent_wrong_date"}:
        for command in commands[2:]:
            command["filter"]["date"]["$gte"] = "2026-09-01"
        if defect == "coherent_wrong_date":
            audit["google_cohorts"][0]["date_from"] = "2026-09-01"
    elif defect == "provider":
        commands[0]["filter"]["provider"] = "meta_ads"
    elif defect == "account_status":
        del commands[1]["filter"]["connection_status"]
    elif defect == "account_provenance":
        del commands[1]["filter"]["connection_provenance"]
    elif defect == "projection":
        commands[2]["projection"]["unreviewed_private_field"] = 1
    elif defect == "collection":
        commands[2]["collection"] = "unreviewed_native_source"
    elif defect == "missing_read":
        commands.pop()
        runtime["commands"]["find"] -= 1
    elif defect == "extra_read":
        commands.append(copy.deepcopy(commands[3]))
        runtime["commands"]["find"] += 1
    elif defect == "write":
        commands[2]["operation"] = "update"
        runtime["commands"]["update"] = 1
    elif defect == "extra_option":
        commands[3]["unreviewed_fields"] = ["skip"]
    elif defect == "second_cohort":
        audit["google_cohorts"].append(copy.deepcopy(audit["google_cohorts"][0]))
    elif defect in {"command_database", "event_database"}:
        commands[2]["command_database" if defect == "command_database" else "database"] = "foreign"
    elif defect in {"native_documents", "shared_account_documents", "legacy_documents"}:
        name = {"native_documents": NATIVE, "shared_account_documents": "mezan_integration_accounts_v2",
                "legacy_documents": "daily_costs"}[defect]
        runtime["collections"][name] += 1
        runtime["documents"] += 1
    elif defect == "untracked_find":
        runtime["commands"]["find"] += 1
    elif defect == "get_more":
        runtime["commands"]["getMore"] = 1
    elif defect == "unknown_collection":
        runtime["collections"]["unknown"] = 0
    elif defect == "missing_native_evidence":
        del runtime["collections"][NATIVE]
    elif defect == "subtract_four":
        runtime["commands"]["find"] += 1
    else:
        response = json.loads(runtime["json"])
        if defect == "money":
            response["ads_v2"]["breakdown"]["google_transitional"] = 22.24
        elif defect == "google_history_zero":
            response["ads_v2"]["history"][1]["google"] = 0.0
        elif defect == "google_quality":
            response["ads_v2"]["providers"]["google"]["amount_complete"] = True
        elif defect == "observed_days":
            response["ads_v2"]["providers"]["google"]["observed_days"] = 8
        elif defect == "snapchat_quality":
            response["ads_v2"]["providers"]["snapchat"]["amount_available"] = False
        elif defect == "unrelated_json":
            response["unrelated"]["precision"] = 3.34
        elif defect == "zero_numeric_type":
            response["ads_v2"]["breakdown"]["google_transitional"] = 0.0
        runtime["json"] = json.dumps(response)
    with pytest.raises(AssertionError):
        assert_runtime_read_contract(runtime, baseline)


def test_google_command_capture_preserves_semantics_and_snapshots_input():
    expected = pinned_commands()[2]
    command = {"find": expected["collection"], "filter": copy.deepcopy(expected["filter"]),
               "projection": copy.deepcopy(expected["projection"]), "$db": DATABASE,
               "lsid": {"id": "synthetic"}, "$clusterTime": {"clusterTime": "synthetic"}}
    recorded = _google_command("find", command, database_name=DATABASE)
    command["filter"]["user_id"] = "changed-later"
    assert recorded == expected
    command["sort"] = {"date": 1}
    assert _google_command("find", command, database_name=DATABASE)["unreviewed_fields"] == ["sort"]
    write = _google_command("update", {"update": NATIVE, "updates": [], "$db": DATABASE}, database_name=DATABASE)
    assert write["operation"] == "update" and write["unreviewed_fields"] == ["updates"]


def test_full_oracle_preserves_the_reference_input_and_uses_independent_google_expectations():
    old = historical_response()
    source = json.dumps(old)
    expected = approved_response(old)
    expected["totals"]["product_cost_complete"] = True
    assert expected_cost_completeness_json(source) == json.dumps(expected)
    assert source == json.dumps(old)
