"""Only NEW operational orders: courier base fee at first observed delivery.

Synthetic imile/SMSA samples are not historical merchant records. A Salla
delivered status with no original transition date does NOT prove that date;
its first saved observation can still prove one current courier base payable
under a stable approved quote. Never infer customer COD, cash or bank income.
"""
from copy import deepcopy

import pytest

from operational_balance_engine import reconcile
from operational_courier_observed_fees import PROOF

START = "2026-10-03T00:00:00+03:00"
CREATED = "2026-11-21T10:00:00+00:00"
OBSERVED = "2026-11-26T20:36:00+00:00"
LATER = "2026-11-27T02:00:00+00:00"


def saved():
    return {"owner_id": "owner", "status": "active", "started_at": START,
            "revision": 2, "openings": {}, "movements": [],
            "engine": {"as_of": "2026-11-20T10:00:00+00:00"}}


def new_order(*, party="imile", creation=CREATED, observation=OBSERVED,
              fee="17.25", mode="deferred"):
    return {
        "id": "synthetic-postopening-100", "order_number": "SYNTH-NOV-100",
        "created_at": creation, "updated_at": observation,
        "status": "delivered", "currency": "SAR", "items": [],
        "delivered_at": None,
        "delivery_event": {
            "schema": "operational_delivery_status_v1",
            "id": "salla-delivery:synthetic-postopening-100",
            "recognition_basis": "delivered_order_created_after_start",
            "order_id": "synthetic-postopening-100",
            "order_number": "SYNTH-NOV-100",
            "order_created_at": creation,
            "party_type": "courier", "party_id": party,
            "currency": "SAR", "delivered_at": None, "recorded_at": observation,
        },
        "carrier": {
            "kind": "courier", "id": party, "currency": "SAR",
            "contract_id": "synthetic-approved-tariff",
            "payment_mode": mode, "cost": fee, "fee_complete": True,
            "fee_components": [
                {"id": "base_shipping", "complete": True, "amount": fee}
            ],
        },
    }


@pytest.mark.parametrize("party", ["imile", "smsa"])
def test_new_delivered_order_records_one_operational_courier_base(party):
    previous = saved()
    original = deepcopy(previous)
    order = new_order(party=party)
    sources = {"orders": [order]}
    first = reconcile(deepcopy(previous), deepcopy(sources), OBSERVED)

    key = "shipping:synthetic-postopening-100:base_shipping"
    fee = first["engine"]["obligations"][key]
    assert (fee["kind"], fee["party_type"], fee["party_id"]) == ("shipping", "courier", party)
    assert (fee["direction"], fee["currency"], fee["confirmed"]) == ("payable", "SAR", "17.25")
    assert fee["business_date"] == "2026-11-26"
    assert fee["business_date_basis"] == "first_observed_delivered_status"
    assert fee["delivered_at"] is None
    assert first["engine"]["facts"][PROOF + order["id"]]["original_delivery_time_verified"] is False
    assert first["engine"]["facts"][key] == {"cost": "17.25", "currency": "SAR"}
    assert not first["movements"]
    assert not any(key.startswith(("cod:", "cod_collection:", "bank_credit:"))
                   for key in first["engine"]["facts"])
    assert not any(row["kind"] in {"supplier", "provider", "cod"}
                   for row in first["engine"]["obligations"].values())
    assert previous == original

    # Re-reading exactly the same source and later calculating the current
    # day must not produce a second fee or silently redate this one.
    again = reconcile(deepcopy(first), deepcopy(sources), OBSERVED)
    assert again == first
    later = reconcile(deepcopy(first), deepcopy(sources), LATER)
    assert later["engine"]["obligations"][key] == fee
    assert len([k for k in later["engine"]["obligations"]
                if k.endswith(":base_shipping")]) == 1


def test_order_created_before_opening_never_enters_observed_base():
    order = new_order(creation="2026-10-02T20:00:00+00:00")
    result = reconcile(saved(), {"orders": [order]}, OBSERVED)
    assert PROOF + order["id"] not in result["engine"]["facts"]
    assert not any(x["kind"] == "shipping" and x.get("confirmed") == "17.25"
                   for x in result["engine"]["obligations"].values())


@pytest.mark.parametrize("change", [
    "not_delivered", "wrong_courier", "prepaid", "unproven_contract",
    "fee_missing", "future_observation", "prior_observation", "undated_order",
])
def test_no_courier_base_without_exact_status_and_safe_rate(change):
    order = new_order()
    if change == "not_delivered":
        order["status"] = "completed"
    elif change == "wrong_courier":
        order["delivery_event"]["party_id"] = "smsa"
    elif change == "prepaid":
        order["carrier"]["payment_mode"] = "prepaid"
    elif change == "unproven_contract":
        order["carrier"]["contract_id"] = None
    elif change == "fee_missing":
        order["carrier"]["fee_components"] = []
    elif change == "future_observation":
        order["delivery_event"]["recorded_at"] = "2026-12-01T00:00:00+00:00"
    elif change == "prior_observation":
        order["delivery_event"]["recorded_at"] = "2026-11-20T00:00:00+00:00"
    elif change == "undated_order":
        order["delivery_event"]["order_created_at"] = "2026-11-19T00:00:00+00:00"
    result = reconcile(saved(), {"orders": [order]}, OBSERVED)
    assert PROOF + order["id"] not in result["engine"].get("facts", {})
    assert not any(row.get("confirmed") == "17.25"
                   for row in result["engine"]["obligations"].values())


def test_prior_completed_shipping_or_settled_base_never_doubles_observed_fee():
    for prior in ("completed", "base"):
        state = saved()
        state["engine"]["facts"] = {}
        state["engine"]["obligations"] = {}
        oid = new_order()["id"]
        if prior == "completed":
            state["engine"]["facts"]["completed_shipping:" + oid] = {
                "order_id": oid, "party_id": "imile", "amount": "17.25"}
            charge = "shipping-completed:" + oid
        else:
            charge = "shipping:" + oid + ":base_shipping"
            state["engine"]["facts"][charge] = {"cost": "17.25", "currency": "SAR"}
        state["engine"]["obligations"][charge] = {
            "id": charge, "kind": "shipping", "party_id": "imile",
            "party_type": "courier", "currency": "SAR", "direction": "payable",
            "confirmed": "17.25", "expected": "0.00",
            "business_date": "2026-11-22",
        }
        result = reconcile(deepcopy(state), {"orders": [new_order()]}, OBSERVED)
        assert len([row for row in result["engine"]["obligations"].values()
                    if row["kind"] == "shipping" and row.get("confirmed") == "17.25"]) == 1
        assert result["engine"]["obligations"][charge]["business_date"] == "2026-11-22"
        assert not result["movements"]
