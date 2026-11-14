"""Bound replay work while retaining both financial rollback boundaries."""
from copy import deepcopy
import sys

import pytest

from operational_balance_engine import instant, reconcile
from operational_order_page_payment import observed_balance
from operational_reconciliation_state import ReconciliationMap
from test_operational_balance_engine import NOW, initial, order, policy


def _captured_orders(count):
    return [order(id=f"replay-{index}", payment={
        "provider": "tabby", "gross": "1000", "captures": [{
            "id": f"capture-{index}", "amount": "1000", "captured_at": NOW,
        }],
    }) for index in range(count)]


def _replay_work(count, without_carrier=False, page_mode=False):
    orders = _captured_orders(count)
    for index, row in enumerate(orders):
        if without_carrier:
            row["carrier"] = None
        if page_mode:
            row["order_number"] = index
            row["payment"] = {"provider": "tabby", "gross": "1000", "order_page_balance": {
                "source": "canonical_salla_order_page", "owner_id": "owner", "order_id": row["id"],
                "order_number": index, "provider": "tabby", "currency": "SAR",
                "gross": "1000", "paid": "1000", "remaining": "0",
                "source_updated_at": row["updated_at"],
            }}
    sources = {"orders": orders, "fee_policies": [policy()]}
    saved = reconcile({**initial(), "owner_id": "owner"}, sources, NOW)
    assert len(saved["engine"]["provider_reports"]) == count
    assert all(saved["engine"]["obligations"]["provider:" + row["id"]]["confirmed"] == "1000.00"
               for row in orders)
    copied_entries = prefix_checks = 0

    def profile(frame, event, operation):
        nonlocal copied_entries, prefix_checks
        if event != "c_call":
            return
        name, owner = getattr(operation, "__name__", None), getattr(operation, "__self__", None)
        # Count the entire call tree. Moving a full scan/copy into a helper
        # cannot conceal its work from this regression.
        if name == "copy" and isinstance(owner, dict):
            copied_entries += len(owner)
        if name == "startswith" and isinstance(owner, str):
            prefix_checks += 1

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        replayed = reconcile(saved, sources, NOW)
    finally:
        sys.setprofile(previous)
    assert replayed == saved
    assert all(type(replayed["engine"][name]) is dict for name in
               ("obligations", "facts", "provider_reports", "cod_reports"))
    return copied_entries, prefix_checks


@pytest.mark.parametrize("without_carrier", [False, True])
@pytest.mark.parametrize("page_mode", [False, True])
def test_unchanged_replay_work_does_not_grow_quadratically(without_carrier, page_mode):
    small, large = _replay_work(24, without_carrier, page_mode), _replay_work(48, without_carrier, page_mode)
    for small_work, large_work in zip(small, large):
        assert large_work <= small_work * 2.5 + 48, (small, large)


@pytest.mark.parametrize("provider", [None, 42, [], {}])
def test_bad_provider_after_held_delivery_restores_deleted_estimates_and_order(provider):
    queued = order(id="held", carrier={"id": "imile", "party_type": "courier", "cost": None,
        "fee_components": [{"id": "base_shipping", "amount": "17.25", "complete": True},
                           {"id": "cod_commission", "amount": "5", "complete": True}]})
    state = reconcile(initial(), {"orders": [order(id="before"), queued, order(id="after")]}, NOW)
    state_before = deepcopy(state)
    delivered = "2026-10-07T11:00:00+00:00"
    invalid = deepcopy(queued)
    invalid.update(status="delivered", delivered_at=delivered, updated_at=NOW, order_number=123,
        delivery_event={"schema": "operational_delivery_event_v1", "id": "delivery-held",
            "order_id": "held", "order_number": 123, "order_created_at": queued["created_at"],
            "delivered_at": delivered, "currency": "SAR", "party_id": "imile", "party_type": "courier"},
        payment={"provider": provider, "gross": "1000"})
    invalid["carrier"]["fee_recognition_held"] = True
    output = reconcile(state, {"orders": [invalid]}, NOW)
    for name in ("obligations", "facts", "provider_reports", "cod_reports", "order_versions"):
        assert output["engine"][name] == state["engine"][name]
        assert list(output["engine"][name]) == list(state["engine"][name])
    assert output["engine"]["issues"][0]["source_id"] == "held"
    assert state == state_before


def test_provider_failure_discards_only_its_facts_and_next_order_can_use_reference():
    payment = {"provider": "tabby", "gross": "1000", "captures": [
        {"id": "retry-reference", "amount": "500", "captured_at": NOW},
        {"id": "bad-reference", "amount": "600", "captured_at": NOW}]}
    failed = order(id="failed", status="delivered", payment=payment)
    good = order(id="good", payment={"provider": "tabby", "gross": "1000", "captures": [
        {"id": "retry-reference", "amount": "500", "captured_at": NOW}]})
    output = reconcile(initial(), {"orders": [failed, good], "fee_policies": [policy()]}, NOW)
    facts, claims = output["engine"]["facts"], output["engine"]["obligations"]
    assert facts["shipping:failed"]["id"] == "imile"
    assert facts["cod_collection:failed"]["amount"] == "500.00"
    assert claims["shipping:failed"]["confirmed"] == "17.25"
    assert claims["cod:failed"]["confirmed"] == "500.00"
    assert "capture:bad-reference" not in facts and "provider:failed" not in claims
    assert facts["capture:retry-reference"] == {"order_id": "good", "provider": "tabby", "amount": "500.00"}
    assert claims["provider:good"]["confirmed"] == "500.00"
    assert {"code": "provider_capture_amount_conflict", "source_id": "provider:failed"} in output["engine"]["issues"]


def test_late_split_failure_rolls_back_direct_helper_writes_with_order():
    state = {**initial(), "owner_id": "owner"}
    split = {"order_id": "split", "order_number": 99, "owner_id": "owner", "currency": "SAR", "gross": "1000.00",
        "tenders": [{"reference": "split-a", "method": "tabby", "provider": "tabby", "amount": "400"},
                    {"reference": "split-b", "method": "tamara", "provider": "tamara", "amount": None}]}
    output = reconcile(state, {"orders": [order(id="split", order_number=99, status="delivered", payment={"split": split})],
                              "fee_policies": [policy(), policy(id="tamara", provider="tamara")]}, NOW)
    assert output["engine"]["issues"]
    for name in ("obligations", "facts", "provider_reports", "cod_reports", "order_versions"):
        assert output["engine"][name] == {}
        assert type(output["engine"][name]) is dict


def test_refund_index_keeps_all_provider_prefixes_and_unused_amounts_lazy():
    sources = {"orders": _captured_orders(1), "fee_policies": [policy(refund_fee_treatment="recalculate")]}
    saved = reconcile(initial(), sources, NOW)
    saved["engine"]["facts"]["refund:tamara:foreign-prefix"] = {"order_id": "replay-0", "amount": "40.00"}
    saved["engine"]["facts"]["capture:unused"] = {"order_id": "unused", "provider": "tabby", "amount": "not-money"}
    saved["engine"]["facts"]["refund:tabby:unused"] = {"order_id": "unused", "amount": "not-money"}
    output = reconcile(saved, sources, NOW)
    assert output["engine"]["obligations"]["provider:replay-0"]["confirmed"] == "960.00"
    assert not output["engine"]["issues"]


@pytest.mark.parametrize("key", ["capture:orphan", "refund:tabby:orphan", "refund:tamara:orphan"])
def test_missing_fact_identity_still_causes_the_existing_provider_error(key):
    sources = {"orders": _captured_orders(1), "fee_policies": [policy()]}
    saved = reconcile(initial(), sources, NOW)
    saved["engine"]["facts"][key] = {"amount": "bad"}
    output = reconcile(saved, sources, NOW)
    assert {"code": "'order_id'", "source_id": "provider:replay-0"} in output["engine"]["issues"]
    assert output["engine"]["facts"] == saved["engine"]["facts"]
    assert output["engine"]["obligations"]["provider:replay-0"]["incomplete"] is True


@pytest.mark.parametrize("operation", ["assignment", "delete", "pop", "popitem", "setdefault", "update", "clear", "union"])
def test_nested_map_rollback_restores_dictionary_value_and_insertion_order(operation):
    original = {"first": {"value": 1}, "middle": {"value": 2}, "last": {"value": 3}}
    mapping = ReconciliationMap(original, lambda: None)
    outer = mapping.checkpoint()
    del mapping["middle"]
    mapping["tail"] = {"value": 4}
    outer_state = list(mapping.items())
    inner = mapping.checkpoint()
    if operation == "assignment":
        mapping["first"] = {"value": 10}
    elif operation == "delete":
        del mapping["first"]
    elif operation == "pop":
        mapping.pop("first")
    elif operation == "popitem":
        mapping.popitem()
    elif operation == "setdefault":
        mapping.setdefault("new", {"value": 10})
    elif operation == "update":
        mapping.update({"first": {"value": 10}, "new": {"value": 11}})
    elif operation == "clear":
        mapping.clear()
    else:
        mapping |= {"first": {"value": 10}, "new": {"value": 11}}
    mapping.rollback(inner)
    assert list(mapping.items()) == outer_state
    mapping.rollback(outer)
    assert list(mapping.items()) == list(original.items())
    assert mapping["middle"] is original["middle"]


def test_fact_index_rollback_restores_original_order_and_releases_new_reference():
    original = {"capture:first": {"order_id": "o", "amount": "1"},
                "capture:middle": {"order_id": "o", "amount": "2"},
                "capture:last": {"order_id": "o", "amount": "3"}}
    facts = ReconciliationMap(original, lambda: None, index_facts=True)
    checkpoint = facts.checkpoint()
    del facts["capture:middle"]
    facts["capture:first"] = {"order_id": "other", "amount": "4"}
    facts["capture:new"] = {"order_id": "o", "amount": "5"}
    assert [key for key, _ in facts.facts_for("capture:", "o")] == ["capture:last", "capture:new"]
    facts.rollback(checkpoint)
    assert list(facts.facts_for("capture:", "o")) == list(original.items())
    assert list(facts.facts_for("capture:", "other")) == []


@pytest.mark.parametrize("refund_first", [False, True])
def test_page_observation_keeps_first_error_across_capture_and_refund_families(refund_first):
    rows = [("capture:bad-party", {"order_id": "o1", "provider": "tamara", "amount": "1"}),
            ("refund:tabby:recorded", {"order_id": "o1", "amount": "1"})]
    if refund_first:
        rows.reverse()
    facts = dict(rows)
    state = {**initial(), "owner_id": "owner", "engine": {"facts": facts}}
    current = order(order_number=1)
    payment = {"provider": "tabby", "gross": "1000"}
    page = {"owner_id": "owner", "order_id": "o1", "order_number": 1, "provider": "tabby",
            "currency": "SAR", "gross": "1000", "paid": "1000", "remaining": "0"}
    expected = "provider_refund_execution_evidence_required" if refund_first else "provider_capture_identity_conflict"
    kwargs = {"now": instant(NOW), "start": instant(state["started_at"])}
    with pytest.raises(ValueError, match=expected):
        observed_balance(page, payment, current, state, **kwargs)
    indexed = ReconciliationMap(facts, lambda: None, index_facts=True)
    with pytest.raises(ValueError, match=expected):
        observed_balance(page, payment, current, state, **kwargs,
                         fact_items=indexed.facts_for(("capture:", "refund:"), "o1"))
