"""Table scope must follow real financial dependencies, not shared labels."""
from copy import deepcopy

import pytest

from operational_financial_position import build_financial_position
from operational_provider_report_coverage import provider_evidence_coverage
from test_operational_balance_financial_position import state_for, opening, table


def snapshot(*issues):
    state = state_for()
    for kind in ("bank", "cash", "supplier", "employee", "external_person", "provider"):
        opening(state, kind, kind, "100.00")
    state["engine"]["issues"] = list(issues)
    return state


def test_tabby_fee_gap_preserves_unrelated_complete_tables_and_all_recorded_amounts():
    issue = {"code": "provider_payment_plan_required", "source_id": "provider:one", "provider": "tabby"}
    state = snapshot(issue, deepcopy(issue))
    before = deepcopy(state)
    result = build_financial_position(state)
    assert table(result, "providers")["complete"] is False
    for identity in ("banks_network", "cash", "suppliers", "employees", "external"):
        assert table(result, identity)["complete"] is True
    assert table(result, "providers")["coverage"]["affected_source_count"] == 1
    assert table(result, "providers")["coverage"]["financial_impact"] is None
    assert "provider_payment_plan_required" not in " ".join(table(result, "providers")["notes"])
    assert result["currencies"]["SAR"]["metrics"]["monetary_position"] is None
    clean = build_financial_position(snapshot())
    for identity in ("banks_network", "cash", "suppliers", "employees", "external"):
        assert table(result, identity)["rows"] == table(clean, identity)["rows"]
        assert table(result, identity)["totals"] == table(clean, identity)["totals"]
    assert state == before


def test_prepaid_gap_blocks_cash_and_shipping_but_not_unrelated_supplier_debt():
    result = build_financial_position(snapshot({"code": "operational_prepaid_shipping_completion_timestamp_missing",
                                               "source_id": "shipment-one", "component": "prepaid_shipping"}))
    assert not table(result, "cash")["complete"]
    assert not table(result, "couriers")["complete"]
    assert table(result, "suppliers")["complete"]
    assert table(result, "banks_network")["complete"]


@pytest.mark.parametrize("issue", [
    {"code": "future_unknown_source_failure", "component": "shipping_fee"},
    {"code": "provider_settled_capture_review_required", "source_id": "provider:one"},
    {"code": "operational_order_bank_credit_conflict", "source_id": "order"},
    {"code": "supplier_receipt_evidence_incomplete", "source_id": "invoice"},
])
def test_unknown_or_bank_affecting_failure_is_never_silently_isolated(issue):
    result = build_financial_position(snapshot(issue))
    for identity in ("banks_network", "cash", "suppliers", "employees", "external"):
        assert table(result, identity)["complete"] is False


def test_salary_gap_only_blocks_employee_accrual_table_and_consolidated_position():
    result = build_financial_position(snapshot({"code": "salary_contract_missing", "source_id": "salary:one:2026-11-01"}))
    assert not table(result, "employees")["complete"]
    assert table(result, "suppliers")["complete"]
    assert table(result, "banks_network")["complete"]
    assert result["currencies"]["SAR"]["metrics"]["monetary_position"] is None


def test_missing_snapshot_time_does_not_establish_any_table_coverage():
    state = snapshot()
    state["engine"]["as_of"] = None
    result = build_financial_position(state)
    assert not table(result, "banks_network")["complete"]
    assert not table(result, "suppliers")["complete"]


def test_known_unsettled_provider_calculation_is_ready_but_statement_is_not_certified():
    projection = {"obligations": [{"id": "provider:one", "kind": "provider", "party_type": "provider",
        "party_id": "tabby", "settled": "0.00"}], "issues": [], "details": {
        "facts": {"capture:one": {"order_id": "one", "provider": "tabby"}},
        "provider_balances": {"SAR": [{"provider": "tabby", "complete": True}]}}}
    result = provider_evidence_coverage(projection)[0]
    assert result["status"] == result["calculation_status"] == "ready"
    assert result["statement_reconciliation_status"] == "unavailable"
    assert result["affected_order_count"] == 0
    projection["issues"] = [
        {"code": "provider_payment_plan_required", "source_id": "provider:one", "provider": "tabby"},
        {"code": "provider_capture_evidence_incomplete", "source_id": "provider:one", "provider": "tabby"},
        {"code": "provider_net_balance_incomplete", "source_id": "provider:tabby", "provider": "tabby"},
    ]
    result = provider_evidence_coverage(projection)[0]
    assert result["status"] == "incomplete"
    assert result["affected_order_count"] == 1
