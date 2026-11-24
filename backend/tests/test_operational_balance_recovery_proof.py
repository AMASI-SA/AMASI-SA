"""Committed recovery receipts enumerate money, preserve originals and redact sources."""
import asyncio
from copy import deepcopy
from datetime import date, datetime
import json
from unittest.mock import AsyncMock

import pytest

import operational_balance_recovery_proof as proof
from operational_balance_store import STATES
from operational_salary_projection import candidates


STAMP = "2026-11-24T12:00:00+00:00"
BEFORE = "2026-11-21T17:00:00+00:00"


def state():
    return {"owner_id": "owner", "status": "active", "revision": 8,
        "started_at": "2026-11-01T00:00:00+03:00",
        "engine": {"as_of": BEFORE, "obligations": {}, "facts": {}, "salary_days": {}, "ad_days": {}},
        "movements": [{"id": "manual-original", "amount": "7.00", "reference": "private-customer-reference"}],
        "openings": {"bank": {"amount": "500.00"}}, "requests": {"manual-request": {"hash": "prior-hash"}}}


def add_ad(target, sources, day, *, currency="SAR", amount="10.25"):
    entity = "snap-" + currency.lower()
    key = "advertising:" + entity + ":" + day
    target["engine"]["obligations"][key] = {"id": key, "kind": "advertising", "party_type": "ad_account",
        "party_id": entity, "currency": currency, "direction": "payable", "business_date": day,
        "confirmed": amount, "expected": "0.00", "ad_day_id": key, "ad_total_amount": amount}
    target["engine"]["obligations"][key]["timezone"] = "Asia/Riyadh"
    target["engine"]["ad_days"][key] = {"amount": amount, "closed": True, "observed_at": STAMP, "timezone": "Asia/Riyadh"}
    sources.setdefault("ad_snapshots", []).append({"account_id": entity, "date": day, "amount": amount,
        "currency": currency, "observed_at": STAMP, "closed": True, "complete": True,
        "day_ended": True, "timezone": "Asia/Riyadh"})
    return key


def receipt(before, after, sources=None):
    return proof.build_recovery_proof(before, after, sources or {}, STAMP)


def test_receipt_keeps_each_original_day_even_before_last_aggregate_and_separates_currencies():
    before, inputs = state(), {}
    after = deepcopy(before)
    ids = [add_ad(after, inputs, "2026-11-19"), add_ad(after, inputs, "2026-11-20"),
           add_ad(after, inputs, "2026-11-23", currency="USD", amount="4.75")]
    saved = deepcopy((before, after, inputs))
    result = receipt(before, after, inputs)
    assert (before, after, inputs) == saved
    assert result["complete"] is True and result["original_dates_verified"] is True
    assert result["source_completeness_verified"] is False
    assert result["previous_revision"] == 8 and result["calculation_revision"] == 9
    assert result["previous_as_of"] == BEFORE and result["as_of"] == STAMP
    assert result["window"]["from"] == "2026-11-19"
    rows = result["new_original_dated"]["obligations"]
    assert {(r["business_date"], r["currency"], r["confirmed"]) for r in rows} == {
        ("2026-11-19", "SAR", "10.25"), ("2026-11-20", "SAR", "10.25"), ("2026-11-23", "USD", "4.75")}
    assert sum(r["confirmed_count"] for r in rows) == 3
    assert all(value["preserved"] for value in result["preservation"].values())
    output = json.dumps(result)
    assert "private-customer-reference" not in output and "manual-original" not in output
    assert all(identity not in output for identity in ids)


def test_an_idempotent_second_pass_never_counts_existing_money_as_restored():
    before, inputs = state(), {}
    add_ad(before, inputs, "2026-11-20")
    result = receipt(before, deepcopy(before), inputs)
    assert result["new_obligation_count"] == result["new_automatic_movement_count"] == 0
    assert result["changed_existing"]["count"] == 0
    assert result["new_original_dated"]["obligations"] == []
    assert not any(result["new_duplicate_keys"].values())


def test_changed_existing_obligation_is_separate_from_new_recovery():
    before, old_inputs, inputs = state(), {}, {}
    add_ad(before, old_inputs, "2026-11-20", amount="10.00")
    after = deepcopy(before)
    add_ad(after, inputs, "2026-11-20", amount="12.00")
    result = receipt(before, after, inputs)
    changed = result["changed_existing"]
    assert result["new_obligation_count"] == 0 and changed["count"] == 1
    assert changed["before"]["obligations"][0]["confirmed"] == "10.00"
    assert changed["after"]["obligations"][0]["confirmed"] == "12.00"


def test_status_only_shipping_and_provider_aggregate_dates_are_explicitly_unproven():
    before = state()
    after = deepcopy(before)
    for kind in ("shipping", "provider"):
        after["engine"]["obligations"][kind + ":private-order"] = {
            "id": kind + ":private-order", "kind": kind, "party_type": "courier" if kind == "shipping" else "provider",
            "party_id": "entity", "currency": "SAR", "direction": "payable", "business_date": "2026-11-24",
            "confirmed": "15.00", "expected": "0.00",
            **({"business_date_basis": "recognition_recorded_at"} if kind == "shipping" else {})}
    result = receipt(before, after)
    assert result["complete"] is True and result["original_dates_verified"] is False
    assert result["new_original_dated"]["obligations"] == []
    assert sum(r["count"] for r in result["new_date_unverified"]["obligations"]) == 2
    assert result["original_date_unknown_reasons"] == {"recognition_observation_date": 1, "provider_observation_or_aggregate": 1}
    assert "private-order" not in json.dumps(result)


@pytest.mark.parametrize("fault", [None, "wrong_day", "conflict", "expected"])
def test_salary_source_validation_keeps_original_day_and_cumulative_cent_rounding(fault):
    before = state()
    after = deepcopy(before)
    employee = {"id": "employee", "currency": "SAR", "salary_revisions": [
        {"effective_from": "2026-11-01", "status": "active", "salary": "1000.00"}]}
    projected, _ = candidates(employee, date(2026, 11, 2), date(2026, 11, 24))
    key = "salary:employee:2026-11-21"
    candidate = projected[key]
    after["engine"]["salary_days"][key] = candidate
    after["engine"]["obligations"][key] = {"id": key, "kind": "salary", "party_type": "employee", "party_id": "employee",
        "currency": "SAR", "business_date": "2026-11-21", "direction": "payable", "confirmed": candidate["amount"], "expected": "0.00"}
    if fault == "wrong_day": after["engine"]["obligations"][key]["business_date"] = "2026-11-24"
    elif fault == "conflict": after["engine"]["salary_conflicts"] = {key: {"recorded": candidate}}
    elif fault == "expected": after["engine"]["obligations"][key]["expected"] = "1.00"
    result = receipt(before, after, {"employees": [employee]})
    assert result["original_dates_verified"] is (fault is None)
    if fault is None:
        assert result["new_original_dated"]["obligations"][0]["confirmed"] == candidate["amount"]
    else:
        assert result["new_original_dated"]["obligations"] == []


@pytest.mark.parametrize("fault", ["closed", "complete", "day_ended", "early_observation", "confirmed", "expected", "timezone"])
def test_ad_original_date_requires_closed_native_day_and_exact_financial_parts(fault):
    before, inputs = state(), {}
    after = deepcopy(before)
    key = add_ad(after, inputs, "2026-11-20")
    if fault in {"closed", "complete", "day_ended"}:
        inputs["ad_snapshots"][0][fault] = False
    elif fault == "early_observation": inputs["ad_snapshots"][0]["observed_at"] = "2026-11-20T12:00:00+00:00"
    elif fault == "confirmed": after["engine"]["obligations"][key]["confirmed"] = "500.00"
    elif fault == "expected": after["engine"]["obligations"][key]["expected"] = "1.00"
    else: after["engine"]["obligations"][key]["timezone"] = "America/Los_Angeles"
    result = receipt(before, after, inputs)
    assert result["original_dates_verified"] is False
    assert result["new_original_dated"]["obligations"] == []


def test_derived_sar_or_tax_amount_changes_are_preserved_as_existing_financial_changes():
    before, inputs = state(), {}
    key = add_ad(before, inputs, "2026-11-20", currency="USD")
    before["engine"]["obligations"][key]["sar_amount"] = "38.44"
    after = deepcopy(before)
    after["engine"]["obligations"][key]["sar_amount"] = "39.00"
    result = receipt(before, after, inputs)
    assert result["changed_existing"]["count"] == 1 and result["new_obligation_count"] == 0


def test_delivery_date_needs_current_native_event_paired_with_immutable_fact():
    before, after = state(), state()
    key, at = "shipping:private-order", "2026-11-20T10:00:00+00:00"
    after["engine"]["obligations"][key] = {"id": key, "kind": "shipping", "party_type": "courier", "party_id": "courier",
        "currency": "SAR", "direction": "payable", "business_date": "2026-11-20", "confirmed": "15.00", "expected": "0.00"}
    after["engine"]["facts"][key] = {"id": "courier", "party_type": "courier", "cost": "15.00", "delivered_at": at, "fee_currency": "SAR"}
    assert receipt(before, after)["original_dates_verified"] is False
    event = {"id": "delivery-private", "order_id": "private-order", "schema": "operational_delivery_event_v1",
        "party_type": "courier", "party_id": "courier", "currency": "SAR", "delivered_at": at}
    after["engine"]["facts"]["delivery_event:private-order"] = event
    inputs = {"delivery_events": [{"id": "private-order", "delivery_event": event}]}
    result = receipt(before, after, inputs)
    assert result["original_dates_verified"] is True
    assert "private-order" not in json.dumps(result)
    # Delivery belongs to the sale currency; the separately authenticated fee
    # remains in its own contract currency.
    event["currency"] = "KWD"
    inputs["delivery_events"][0]["currency"] = "KWD"
    assert receipt(before, after, inputs)["original_dates_verified"] is True
    inputs["delivery_events"][0]["currency"] = "USD"
    assert receipt(before, after, inputs)["original_dates_verified"] is False


@pytest.mark.parametrize("fault", [None, "missing_source", "wrong_day", "wrong_amount", "wrong_party", "observation"])
def test_completed_shipping_uses_paired_native_completion_evidence(fault):
    before, after = state(), state()
    at, key = "2026-11-20T22:30:00+00:00", "shipping-completed:private-order"
    event = {"schema": "operational_completed_shipping_v1", "owner_id": "owner", "order_id": "private-order",
        "party_type": "courier", "party_id": "carrier", "currency": "SAR", "amount": "12.50",
        "completed_at": at, "payment_mode": "deferred", "recognition_event": "order_completed", "component": "base_shipping"}
    row = {"id": key, "kind": "shipping", "party_type": "courier", "party_id": "carrier", "currency": "SAR",
        "direction": "payable", "business_date": "2026-11-21", "confirmed": "12.50", "expected": "0.00",
        "completed_at": at, "payment_mode": "deferred", "recognition_event": "order_completed", "component": "base_shipping"}
    if fault == "wrong_day": row["business_date"] = "2026-11-24"
    if fault == "wrong_amount": row["confirmed"] = "13.00"
    if fault == "wrong_party": row["party_id"] = "other-carrier"
    if fault == "observation": event["recognition_basis"] = "aramex_status_created_after_start"
    after["engine"]["facts"]["completed_shipping:private-order"] = deepcopy(event)
    after["engine"]["obligations"][key] = row
    inputs = {"completed_shipping_events": [] if fault == "missing_source" else [event]}
    result = receipt(before, after, inputs)
    assert result["original_dates_verified"] is (fault is None)
    assert bool(result["new_original_dated"]["obligations"]) is (fault is None)
    assert "private-order" not in json.dumps(result)


def test_demand_cancellation_interrupts_receipt_and_is_never_converted_to_unknown(monkeypatch):
    def cancelled():
        raise asyncio.CancelledError
    monkeypatch.setattr(proof, "calculation_checkpoint", cancelled)
    with pytest.raises(asyncio.CancelledError):
        receipt(state(), state())


def test_record_limit_is_checked_before_iterating_evidence(monkeypatch):
    before, after = state(), state()
    before["engine"]["obligations"] = {"oversized": None}
    monkeypatch.setattr(proof, "MAX_RECORDS", 0)
    result = receipt(before, after)
    assert result["reason"] == "record_limit" and result["complete"] is False


def test_changed_manual_opening_or_missing_request_prevents_preservation_claim():
    before, after = state(), state()
    after["movements"][0]["amount"] = "8.00"
    after["openings"]["bank"]["amount"] = "600.00"
    after["requests"] = {}
    result = receipt(before, after)
    assert result["complete"] is False
    assert result["preservation"]["manual_movements"]["changed_count"] == 1
    assert result["preservation"]["openings"]["changed_count"] == 1
    assert result["preservation"]["requests"]["missing_count"] == 1


def test_new_duplicate_excess_is_compared_per_identity_against_baseline():
    before = state()
    movement = {"id": "auto1", "automatic_order_bank": True, "actor_id": "operational_order_worker",
        "request_id": "same-private-request", "order_number": "private-order", "amount": "9.00", "currency": "SAR",
        "direction": "incoming", "party_type": "bank", "party_id": "bank", "occurred_at": STAMP}
    before["movements"] += [movement, {**movement, "id": "auto2"}]
    after = deepcopy(before)
    after["movements"].append({**movement, "id": "auto3"})
    result = receipt(before, after)
    assert result["existing_duplicate_keys"]["request_id"] == 1
    assert result["new_duplicate_keys"]["request_id"] == result["new_duplicate_keys"]["automatic_bank_order"] == 1
    assert result["complete"] is False and result["new_automatic_movement_count"] == 1


def test_missing_date_is_not_silently_dropped_from_a_complete_proof():
    before, inputs = state(), {}
    after = deepcopy(before)
    key = add_ad(after, inputs, "2026-11-20")
    after["engine"]["obligations"][key].pop("business_date")
    result = receipt(before, after, inputs)
    assert result["complete"] is False and result["ungrouped_record_count"] == 1
    assert result["original_dates_verified"] is False


def test_group_overflow_returns_explicit_omission_not_partial_success(monkeypatch):
    monkeypatch.setattr(proof, "MAX_GROUPS", 1)
    before, inputs = state(), {}
    after = deepcopy(before)
    add_ad(after, inputs, "2026-11-20")
    add_ad(after, inputs, "2026-11-21")
    result = receipt(before, after, inputs)
    assert result["complete"] is False and result["reason"] == "metadata_limit"
    assert result["omitted_group_count"] == 2 and result["new_obligation_count"] == 2
    assert "new_original_dated" not in result


@pytest.mark.parametrize("fault", [None, "owner", "revision", "as_of", "future", "malformed"])
def test_read_projects_only_bound_metadata_and_fails_closed(fault):
    async def scenario():
        before, inputs = state(), {}
        after = deepcopy(before)
        add_ad(after, inputs, "2026-11-20")
        result = receipt(before, after, inputs)
        row = {"owner_id": "owner", "revision": 9, "engine": {"as_of": STAMP, proof.FIELD: result}}
        if fault == "owner": result["owner_id"] = "other"
        elif fault == "revision": result["calculation_revision"] = 10
        elif fault == "as_of": row["engine"]["as_of"] = BEFORE
        elif fault == "future": result["as_of"] = row["engine"]["as_of"] = "2026-11-25T12:00:00+00:00"
        elif fault == "malformed": row["engine"][proof.FIELD] = []
        collection = type("Collection", (), {})()
        collection.find_one = AsyncMock(return_value=row)
        response = await proof.read_proof({STATES: collection}, "owner", now=datetime.fromisoformat(STAMP))
        assert response["state"] == ("available" if fault is None else "unknown")
        query, projection = collection.find_one.call_args.args
        assert query == {"_id": "owner", "owner_id": "owner"}
        assert set(projection) == {"_id", "owner_id", "revision", "engine.as_of", "engine." + proof.FIELD, "engine.automatic_source_coverage"}
    asyncio.run(scenario())
