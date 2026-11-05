"""A paid execution charge remains visible without inventing a delivery."""
from copy import deepcopy

from operational_financial_position import build_financial_position
from test_operational_balance_financial_position import NOW, obligation, opening, state_for, table


COMPLETED = "2026-11-01T07:00:00+00:00"
DELIVERED = "2026-11-01T08:00:00+00:00"


def prepaid_position():
    state = state_for()
    opening(state, "cash-salla-fixture", "cash", "100.00")
    obligation(state, "shipping-prepaid:execution-order", "shipping", "courier", "34.50",
               party="aramex-fixture", order_id="execution-order", order_number="EX-100",
               payment_mode="prepaid", recognition_event="order_completed", completed_at=COMPLETED,
               cash_account_id="cash-salla-fixture")
    state["movements"].append({
        "id": "shipping-prepaid-payment:execution-order", "kind": "settlement", "direction": "outgoing",
        "party_type": "courier", "party_id": "aramex-fixture", "name": "Aramex fixture",
        "amount": "34.50", "currency": "SAR", "bank_id": "cash-salla-fixture", "bank_kind": "cash",
        "bank_name": "Salla fixture cashbox", "source_account_type": "cash", "occurred_at": COMPLETED,
        "allocations": [{"obligation_id": "shipping-prepaid:execution-order", "amount": "34.50"}],
    })
    return state


def test_paid_execution_charge_has_its_own_date_and_cashbox_without_false_delivery_or_debt():
    state = prepaid_position()
    before = deepcopy(state)
    result = build_financial_position(state, generated_at=NOW)
    currency = result["currencies"]["SAR"]
    row, = currency["deliveries"]["rows"]
    assert row["order_id"] == "execution-order" and row["order_number"] == "EX-100"
    assert row["payment_mode"] == "prepaid" and row["recognition_event"] == "order_completed"
    assert row["completed_at"] == COMPLETED and row["delivered_at"] is None
    assert row["cash_account_id"] == "cash-salla-fixture"
    assert row["shipping_confirmed"] == row["shipping_settled"] == "34.50"
    assert row["shipping_outstanding"] == "0.00"
    assert currency["metrics"]["liquidity"] == currency["metrics"]["monetary_position"] == "65.50"
    assert table(result, "couriers")["totals"] == {"name": "الإجمالي", "receivable": "0.00", "payable": "0.00"}
    assert row["collection_amount"] is None and row["courier_responsibility"] is None
    assert row["collection_status"] == row["collection_destination"] == "unavailable"
    assert state == before
    assert result == build_financial_position(state, generated_at=NOW)


def test_later_delivery_adds_its_separate_date_without_hiding_or_recounting_paid_shipping():
    state = prepaid_position()
    state["engine"]["facts"]["shipping:execution-order"] = {
        "id": "aramex-fixture", "party_type": "courier", "delivered_at": DELIVERED,
    }
    result = build_financial_position(state)
    row, = result["currencies"]["SAR"]["deliveries"]["rows"]
    assert row["completed_at"] == COMPLETED and row["delivered_at"] == DELIVERED
    assert row["shipping_confirmed"] == row["shipping_settled"] == "34.50"
    assert result["currencies"]["SAR"]["metrics"]["liquidity"] == "65.50"


def test_replacement_carrier_delivery_does_not_reassign_the_frozen_prepaid_charge():
    state = prepaid_position()
    state["engine"]["facts"]["shipping:execution-order"] = {
        "id": "replacement-fixture", "party_type": "courier", "delivered_at": DELIVERED,
    }
    row, = build_financial_position(state)["currencies"]["SAR"]["deliveries"]["rows"]
    assert row["party_id"] == "aramex-fixture"
    assert row["completed_at"] == COMPLETED and row["delivered_at"] is None
    assert row["shipping_settled"] == "34.50"


def test_two_carriers_on_one_order_keep_each_shipping_fee_and_cod_responsibility_separate():
    state = prepaid_position()
    state["engine"]["facts"]["shipping:execution-order"] = {
        "id": "replacement-fixture", "party_type": "courier", "delivered_at": DELIVERED,
    }
    obligation(state, "shipping:execution-order", "shipping", "courier", "20.00", party="replacement-fixture")
    obligation(state, "cod:execution-order", "cod", "courier", "30.00", party="replacement-fixture", direction="receivable")
    result = build_financial_position(state)
    rows = result["currencies"]["SAR"]["deliveries"]["rows"]
    assert len(rows) == 2 and len({row["id"] for row in rows}) == 2
    prepaid = next(row for row in rows if row["party_id"] == "aramex-fixture")
    replacement = next(row for row in rows if row["party_id"] == "replacement-fixture")
    assert prepaid["shipping_confirmed"] == prepaid["shipping_settled"] == "34.50"
    assert prepaid["shipping_outstanding"] == "0.00" and prepaid["courier_responsibility"] is None
    assert prepaid["completed_at"] == COMPLETED and prepaid["delivered_at"] is None
    assert replacement["shipping_confirmed"] == replacement["shipping_outstanding"] == "20.00"
    assert replacement["shipping_settled"] == "0.00" and replacement["courier_responsibility"] == "30.00"
    assert replacement["completed_at"] is None and replacement["delivered_at"] == DELIVERED
    assert table(result, "couriers")["totals"]["payable"] == "20.00"


def test_unassigned_customer_cod_is_order_information_and_does_not_duplicate_the_paid_carrier_row():
    state = prepaid_position()
    state["engine"]["cod_reports"] = {"cod:execution-order": {
        "order_id": "execution-order", "currency": "SAR", "party_id": None, "party_type": None,
        "payment_method": "cod", "gross": "300.00", "collected": "100.00", "customer_outstanding": "200.00",
    }}
    row, = build_financial_position(state)["currencies"]["SAR"]["deliveries"]["rows"]
    assert row["party_id"] == "aramex-fixture" and row["shipping_settled"] == "34.50"
    assert row["payment_method"] == "cod" and row["customer_outstanding"] == "200.00"
    assert row["collection_status"] == row["collection_destination"] == "unavailable"
    assert row["courier_responsibility"] is row["collection_amount"] is row["collection_outstanding"] is None
