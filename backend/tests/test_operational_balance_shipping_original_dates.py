"""A historical shipping replay admits money only with the original event day."""
from copy import deepcopy

import pytest

from operational_balance_engine import reconcile
from operational_balance_recovery_proof import build_recovery_proof
from operational_delivery_evidence import STATUS_BASIS, STATUS_SCHEMA


START = "2026-11-01T00:00:00+00:00"
CREATED = "2026-11-20T10:00:00+00:00"
DELIVERED = "2026-11-20T22:30:00+00:00"
NOW = "2026-11-24T12:00:00+00:00"


def initial():
    return {"owner_id": "owner", "status": "active", "started_at": START, "revision": 2,
        "engine": {"as_of": START}, "movements": [{"id": "manual", "amount": "3.00", "kind": "adjustment",
            "party_type": "courier", "party_id": "carrier", "currency": "SAR", "direction": "outgoing",
            "occurred_at": CREATED}], "openings": {}, "requests": {"manual": {"digest": "retained"}}}


def order(*, observed=False, event=True):
    delivery = {"schema": STATUS_SCHEMA if observed else "operational_delivery_event_v1",
        "id": "source-delivery", "order_id": "o1", "order_number": "number-1", "order_created_at": CREATED,
        "party_type": "courier", "party_id": "carrier", "currency": "SAR", "delivered_at": None if observed else DELIVERED}
    if observed:
        delivery.update(recognition_basis=STATUS_BASIS, recorded_at=NOW)
    value = {"id": "o1", "order_number": "number-1", "created_at": CREATED, "updated_at": NOW,
        "status": "delivered", "currency": "SAR", "items": [{"id": "item", "cost": "5", "quantity": "1",
            "supplier_id": "supplier", "supplier_receipt_proven": True}],
        "carrier": {"id": "carrier", "kind": "courier", "currency": "SAR", "cost": "10.00"},
        "cod_amount": "20.00", "cod_required": True,
        "cod": {"evidence_complete": True, "gross": "20.00", "collected": "0.00", "outstanding": "20.00",
                "custody_amount": "20.00", "payment_method": "cod", "evidence_ids": ["collection"]}}
    if event:
        value.update(delivery_event=delivery, delivered_at=delivery["delivered_at"])
    return value


@pytest.mark.parametrize("mode", ["observation", "updated_fallback", "unpaired_delivered_at"])
def test_unproved_delivery_cannot_create_shipping_cod_or_facts_but_supplier_continues(mode):
    previous, source = initial(), order(observed=mode == "observation", event=mode == "observation")
    if mode == "unpaired_delivered_at":
        source["delivered_at"] = DELIVERED
    untouched = deepcopy(previous)
    result = reconcile(previous, {"orders": [source]}, NOW)
    assert previous == untouched
    assert result["movements"] == previous["movements"] and result["requests"] == previous["requests"]
    assert result["engine"]["obligations"]["supplier:o1:item"]["expected"] == "5.00"
    assert not any(key.startswith(("shipping:", "cod:")) for key in result["engine"]["obligations"])
    assert not any(key.startswith(("delivery_event:", "shipping:", "cod_collection:")) for key in result["engine"]["facts"])
    issue = next(row for row in result["engine"]["issues"] if row["code"] == "operational_shipping_original_date_unproven")
    assert (issue["party_type"], issue["party_id"]) == ("courier", "carrier")


def test_later_native_delivery_recovers_same_ids_on_original_local_day_once():
    before = initial()
    held = reconcile(before, {"orders": [order(observed=True)]}, NOW)
    inputs = {"orders": [order()]}
    restored = reconcile(held, inputs, NOW)
    rows = restored["engine"]["obligations"]
    assert rows["shipping:o1"]["confirmed"] == "10.00"
    assert rows["cod:o1"]["confirmed"] == "20.00"
    assert rows["shipping:o1"]["business_date"] == rows["cod:o1"]["business_date"] == "2026-11-21"
    assert restored["movements"] == before["movements"]
    receipt = build_recovery_proof(held, restored, inputs, NOW)
    restored_money = receipt["new_original_dated"]["obligations"]
    assert {(row["direction"], row["business_date"], row["confirmed"]) for row in restored_money} == {
        ("payable", "2026-11-21", "10.00"), ("receivable", "2026-11-21", "20.00")}
    replay = reconcile(restored, inputs, NOW)
    assert replay["engine"]["facts"] == restored["engine"]["facts"]
    assert replay["engine"]["obligations"] == rows
    proof = build_recovery_proof(restored, replay, inputs, NOW)
    assert proof["new_obligation_count"] == proof["new_automatic_movement_count"] == 0


def test_observation_cannot_promote_existing_forecast_or_add_fee_components():
    before = initial()
    forecast = order()
    forecast.update(status="processing")
    forecast.pop("delivery_event")
    forecast.pop("delivered_at")
    previous = reconcile(before, {"orders": [forecast]}, CREATED)
    source = order(observed=True)
    source["carrier"]["fee_components"] = [{"id": "new-fee", "complete": True, "amount": "4.50"}]
    result = reconcile(previous, {"orders": [source]}, NOW)
    for key in ("shipping:o1", "cod:o1"):
        assert result["engine"]["obligations"][key] == previous["engine"]["obligations"][key]
    assert "shipping:o1:new-fee" not in result["engine"]["obligations"]
    assert "shipping:o1" not in result["engine"]["facts"]


def test_already_recorded_legacy_observation_finances_are_retained_exactly():
    previous = initial()
    previous["engine"].update(facts={"shipping:o1": {"id": "carrier", "party_type": "courier", "cost": "10.00",
        "delivered_at": None, "recorded_at": NOW, "recognition_basis": STATUS_BASIS, "fee_currency": "SAR"}},
        obligations={"shipping:o1": {"id": "shipping:o1", "kind": "shipping", "party_type": "courier",
            "party_id": "carrier", "currency": "SAR", "direction": "payable", "business_date": "2026-11-24",
            "expected": "0.00", "confirmed": "10.00"}})
    source = order(observed=True)
    source["carrier"]["fee_components"] = [{"id": "new-fee", "complete": True, "amount": "4.50"}]
    result = reconcile(previous, {"orders": [source]}, NOW)
    assert result["engine"]["facts"] == previous["engine"]["facts"]
    assert result["engine"]["obligations"]["shipping:o1"] == previous["engine"]["obligations"]["shipping:o1"]
    assert "shipping:o1:new-fee" not in result["engine"]["obligations"]
    assert result["movements"] == previous["movements"]


def test_native_replay_does_not_mass_redate_previous_financial_rows():
    inputs = {"orders": [order()]}
    previous = reconcile(initial(), inputs, NOW)
    previous["engine"]["obligations"]["shipping:o1"]["business_date"] = "2026-11-20"
    result = reconcile(previous, inputs, NOW)
    assert result["engine"]["obligations"]["shipping:o1"] == previous["engine"]["obligations"]["shipping:o1"]


def test_prepaid_observation_is_pending_then_native_completion_posts_original_pair_once():
    from test_operational_balance_prepaid_shipping import fixture, calculate, COMPLETED
    data, previous = fixture(old=False, at=None)
    held = calculate(data, previous)
    assert held["movements"] == [] and "prepaid_shipping:100" not in held["engine"]["facts"]
    assert "shipping-prepaid:100" not in held["engine"]["obligations"]
    assert any(row["code"] == "operational_prepaid_shipping_original_date_unproven" for row in held["engine"]["issues"])
    data["unified_orders"][0]["raw_by_source"]["salla_direct"]["status_changed_at"] = COMPLETED
    restored = calculate(data, held)
    movement, = restored["movements"]
    assert movement["occurred_at"] == COMPLETED and movement["amount"] == "34.50"
    assert restored["engine"]["obligations"]["shipping-prepaid:100"]["business_date"] == "2026-10-06"
    assert calculate(data, restored)["movements"] == restored["movements"]
