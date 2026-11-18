"""Status-based prepaid fees remain independent of actual delivery proof."""
from copy import deepcopy

import pytest

from operational_balance_service import report
from operational_financial_position import build_financial_position
from test_operational_balance_financial_position import NOW, table
from test_operational_balance_prepaid_position import prepaid_position


RECORDED = "2026-11-01T08:00:00+00:00"
DELIVERY_RECORDED = "2026-11-01T08:15:00+00:00"


def status_position(status="out_for_delivery"):
    state = prepaid_position()
    charge = state["engine"]["obligations"]["shipping-prepaid:execution-order"]
    charge.update(recognition_event="aramex_status_eligible", completed_at=None,
                  recognition_basis="aramex_status_created_after_start", recorded_at=RECORDED,
                  observed_order_status=status)
    state["movements"][0].update(occurred_at=RECORDED, automatic_prepaid_shipping=True)
    return state


@pytest.mark.parametrize("status", ["completed", "out_for_delivery", "delivered"])
def test_status_paid_fee_has_its_own_time_and_cash_without_implying_delivery(status):
    state = status_position(status)
    before = deepcopy(state)
    result = build_financial_position(state, generated_at=NOW)
    currency = result["currencies"]["SAR"]
    row, = currency["deliveries"]["rows"]
    assert row["recognition_event"] == "aramex_status_eligible"
    assert row["fee_recognition_basis"] == "aramex_status_created_after_start"
    assert row["fee_recorded_at"] == RECORDED
    assert row["fee_observed_order_status"] == status
    assert row["completed_at"] is None and row["delivered_at"] is None
    assert "prepaid_payment_evidence_incomplete" not in row["incomplete_reasons"]
    assert row["shipping_confirmed"] == row["shipping_settled"] == "34.50"
    assert row["shipping_outstanding"] == "0.00"
    assert row["cash_account_id"] == "cash-salla-fixture"
    assert currency["metrics"]["liquidity"] == "65.50"
    assert row["courier_responsibility"] is None and row["collection_amount"] is None
    assert row["collection_status"] == "unavailable"
    assert table(result, "couriers")["totals"]["confirmed_orders"] == 1
    assert table(result, "couriers")["totals"]["delivered_orders"] == 0
    assert {"key": "confirmed_orders", "label": "طلبات محتسبة الأجرة", "type": "count"} in table(result, "couriers")["columns"]
    courier = next(p for p in report(state)["parties"] if p["party_type"] == "courier")
    assert courier["expense_paid"] == "34.50"
    assert state == before


def test_later_status_delivery_retains_both_independent_recognition_times():
    state = status_position("delivered")
    proof = {"schema": "operational_delivery_status_v1", "order_id": "execution-order",
             "order_number": "EX-100", "party_type": "courier", "party_id": "aramex-fixture",
             "recognition_basis": "delivered_order_created_after_start",
             "recorded_at": DELIVERY_RECORDED, "delivered_at": None}
    state["engine"]["facts"]["delivery_event:execution-order"] = proof
    state["engine"]["facts"]["shipping:execution-order"] = {**proof, "id": "aramex-fixture"}
    result = build_financial_position(state)
    row, = result["currencies"]["SAR"]["deliveries"]["rows"]
    assert row["fee_recorded_at"] == RECORDED
    assert row["recorded_at"] == DELIVERY_RECORDED
    assert row["fee_recognition_basis"] == "aramex_status_created_after_start"
    assert row["recognition_basis"] == "delivered_order_created_after_start"
    assert row["completed_at"] is None and row["delivered_at"] is None
    assert row["shipping_settled"] == "34.50"
    assert table(result, "couriers")["totals"]["delivered_orders"] == 1
    assert result["currencies"]["SAR"]["metrics"]["liquidity"] == "65.50"


@pytest.mark.parametrize("damage", [
    {"recorded_at": None}, {"recognition_basis": "unknown"},
    {"observed_order_status": "pending"}, {"cash_account_id": None},
])
def test_incomplete_status_fee_metadata_remains_explicitly_unproven(damage):
    state = status_position()
    state["engine"]["obligations"]["shipping-prepaid:execution-order"].update(damage)
    row, = build_financial_position(state)["currencies"]["SAR"]["deliveries"]["rows"]
    assert "prepaid_payment_evidence_incomplete" in row["incomplete_reasons"]
    assert row["completed_at"] is None and row["delivered_at"] is None
