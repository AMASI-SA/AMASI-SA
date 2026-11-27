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


@pytest.mark.parametrize("party", ["imile", "smsa"])
def test_full_salla_source_collector_status_to_saved_courier_base(party):
    """No injected normalized order: exercise retained register, quote and reducer."""
    from test_operational_balance_sources import HardBoundaryDB, run
    from test_operational_balance_source_fixes import shipping_data
    from operational_balance_sources import collect_sources

    data = shipping_data(
        created_at=CREATED, updated_at=OBSERVED, status="delivered",
        payment={"method": "card"}, paid_amount="0",
    )
    source = data["unified_orders"][0]
    raw = source["raw_by_source"]["salla_direct"]
    raw["shipping"]["company_code"] = party
    source["salla_shipping_current"] = {
        "source_kind": "order", "company_code": party,
        "company_name": "iMile" if party == "imile" else "سمسا",
        "status": "delivered", "superseded_shipment_ids": [],
    }
    setup = data["mz2_shipping_setup_v2"][0]
    setup["couriers"][0].update(
        courier_key=party,
        name="iMile" if party == "imile" else "سمسا",
        salla_carrier_keys=[party],
    )
    setup["contracts"][0]["party_id"] = party
    prior = saved()
    original = deepcopy((data, prior))
    sources = run(collect_sources(
        HardBoundaryDB(data), "owner", START, OBSERVED,
        operational_state=prior,
    ))
    assert (data, prior) == original
    assert len(sources["orders"]) == 1
    assert sources["orders"][0]["delivery_event"]["schema"] == "operational_delivery_status_v1"
    assert sources["orders"][0]["carrier"]["id"] == party
    result = reconcile(deepcopy(prior), deepcopy(sources), OBSERVED)
    fee = result["engine"]["obligations"]["shipping:100:base_shipping"]
    assert (fee["party_id"], fee["confirmed"], fee["business_date"]) == (
        party, "10.00", "2026-11-26")
    assert fee["business_date_basis"] == "first_observed_delivered_status"
    assert result["movements"] == prior["movements"] == []
    assert not any(k.startswith("cod_collection:") for k in result["engine"]["facts"])
    assert not any(row["kind"] in {"provider", "cod"} for row in result["engine"]["obligations"].values())
    assert reconcile(deepcopy(result), deepcopy(sources), OBSERVED) == result


def test_later_native_delivery_date_never_rewrites_observed_charge_or_adds_base():
    first = reconcile(saved(), {"orders": [new_order()]}, OBSERVED)
    key = "shipping:synthetic-postopening-100:base_shipping"
    before = deepcopy(first["engine"]["obligations"][key])
    dated = new_order(observation=LATER)
    dated["delivered_at"] = "2026-11-23T08:00:00+00:00"
    dated["delivery_event"].update(
        schema="operational_delivery_event_v1",
        id="courier-delivery:synthetic-postopening-100",
        delivered_at=dated["delivered_at"],
    )
    dated["delivery_event"].pop("recognition_basis")
    dated["delivery_event"].pop("recorded_at")
    later = reconcile(deepcopy(first), {"orders": [dated]}, LATER)
    assert later["engine"]["obligations"][key] == before
    assert later["engine"]["facts"][PROOF + dated["id"]] == first["engine"]["facts"][PROOF + dated["id"]]
    assert len([r for r in later["engine"]["obligations"].values()
                if r["kind"] == "shipping" and r.get("confirmed") == "17.25"]) == 1
    assert not later["movements"]


def test_unrelated_existing_courier_payment_never_changes_money_on_refresh():
    state = saved()
    state["movements"].append({
        "id": "prior-company-payment", "party_type": "courier", "party_id": "smsa",
        "direction": "outgoing", "kind": "payment", "currency": "SAR",
        "amount": "50.00", "allocations": [],
    })
    original = deepcopy(state["movements"])
    after = reconcile(state, {"orders": [new_order(party="imile")]}, OBSERVED)
    assert after["movements"] == original
    assert after["engine"]["obligations"]["shipping:synthetic-postopening-100:base_shipping"]["party_id"] == "imile"


def test_legacy_aggregate_courier_base_is_not_charged_again():
    state = saved()
    order = new_order()
    oid = order["id"]
    old_key = "shipping:" + oid
    state["engine"]["obligations"] = {
        old_key: {
            "id": old_key, "kind": "shipping", "party_id": "imile",
            "party_type": "courier", "currency": "SAR",
            "direction": "payable", "confirmed": "17.25",
            "expected": "0.00", "business_date": "2026-11-23",
        }
    }
    state["engine"]["facts"] = {
        old_key: {"id": "imile", "party_type": "courier",
                  "cost": None, "delivered_at": None}
    }
    result = reconcile(deepcopy(state), {"orders": [order]}, OBSERVED)
    assert result["engine"]["obligations"][old_key]["confirmed"] == "17.25"
    assert "shipping:" + oid + ":base_shipping" not in result["engine"]["obligations"]
    assert PROOF + oid not in result["engine"]["facts"]
    assert result["movements"] == []



@pytest.mark.parametrize("payout_allocation,should_accrue", [
    ("unallocated", False),
    ("settled_previous_invoice", True),
])
def test_canonical_payout_guard_preserves_existing_company_settlements(payout_allocation, should_accrue):
    """An unallocated company payment may be an advance for a new order.

    Only an explicitly allocated old payable can be excluded from the
    candidate's own fee. The earlier movement itself is never changed.
    """
    from test_operational_balance_sources import HardBoundaryDB, run
    from test_operational_balance_source_fixes import shipping_data
    from operational_balance_sources import collect_sources

    data = shipping_data(
        created_at=CREATED, updated_at=OBSERVED, status="delivered",
        payment={"method": "card"}, paid_amount="0",
    )
    source = data["unified_orders"][0]
    raw = source["raw_by_source"]["salla_direct"]
    raw["shipping"]["company_code"] = "imile"
    source["salla_shipping_current"] = {
        "source_kind": "order", "company_code": "imile",
        "company_name": "iMile", "status": "delivered",
        "superseded_shipment_ids": [],
    }
    setup = data["mz2_shipping_setup_v2"][0]
    setup["couriers"][0].update(
        courier_key="imile", name="iMile", salla_carrier_keys=["imile"])
    setup["contracts"][0]["party_id"] = "imile"


    previous = saved()
    if payout_allocation == "settled_previous_invoice":
        previous["engine"]["obligations"] = {
            "shipping:prior-order:base_shipping": {
                "id": "shipping:prior-order:base_shipping",
                "kind": "shipping", "party_type": "courier",
                "party_id": "imile", "currency": "SAR", "direction": "payable",
                "business_date": "2026-11-15", "expected": "0.00", "confirmed": "35.00",
            }
        }
    previous["movements"] = [{
        "id": "synthetic-prior-company-payout", "party_type": "courier",
        "party_id": "imile", "direction": "outgoing", "kind": "payment",
        "currency": "SAR", "amount": "35.00",
        "allocations": [{"obligation_id": "shipping:prior-order:base_shipping",
                         "amount": "35.00"}] if payout_allocation == "settled_previous_invoice" else [],
        "settlement_scope": "account" if payout_allocation == "unallocated" else "payable",
        "occurred_at": "2026-11-20T00:00:00+00:00",
    }]
    originals = deepcopy(previous["movements"])
    sources = run(collect_sources(
        HardBoundaryDB(data), "owner", START, OBSERVED,
        operational_state=previous,
    ))
    order = sources["orders"][0]
    assert order["delivery_event"]["schema"] == "operational_delivery_status_v1"
    assert (order["carrier"].get("fee_recognition_held") is not True) == should_accrue
    after = reconcile(deepcopy(previous), deepcopy(sources), OBSERVED)
    assert after["movements"] == originals
    charge = after["engine"]["obligations"].get("shipping:100:base_shipping")
    assert (charge is not None and charge.get("confirmed") == "10.00") == should_accrue
    if payout_allocation == "settled_previous_invoice":
        assert after["engine"]["obligations"]["shipping:prior-order:base_shipping"] == previous["engine"]["obligations"]["shipping:prior-order:base_shipping"]
    assert reconcile(deepcopy(after), deepcopy(sources), OBSERVED) == after


@pytest.mark.parametrize("party", ["imile", "smsa"])
def test_observed_fee_is_visible_as_one_automatic_courier_delivery(party):
    """A fee-only observation must appear in the operating delivery history."""
    from operational_workspace_projection import automatic_source
    state = reconcile(saved(), {"orders": [new_order(party=party)]}, OBSERVED)
    key = "shipping:synthetic-postopening-100:base_shipping"
    recorded = state["engine"]["obligations"][key]
    assert automatic_source(state, recorded) == (
        "delivery", "shipping:synthetic-postopening-100")
    corrupted = deepcopy(state)
    corrupted["engine"]["facts"][PROOF + "synthetic-postopening-100"]["amount"] = "999.00"
    assert automatic_source(corrupted, recorded) == (None, None)
    corrupted["engine"]["facts"][PROOF + "synthetic-postopening-100"]["amount"] = "17.25"
    corrupted["engine"]["facts"][PROOF + "synthetic-postopening-100"]["party_id"] = "other"
    assert automatic_source(corrupted, recorded) == (None, None)
