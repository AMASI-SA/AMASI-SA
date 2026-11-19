"""Supplier receipt replay keeps financial evidence with bounded lookup work."""
from copy import deepcopy
import asyncio
from decimal import Decimal
import json
import sys
from threading import Event

import pytest

import operational_balance_engine as engine
from operational_financial_activity_runtime import preparation_scope
from test_operational_balance_engine import NOW, initial, order


def cohort(count=2):
    state = {**initial(), "owner_id": "synthetic-owner", "engine": {"facts": {
        f"unrelated:{i}": {"proof": str(i)} for i in range(5 * count)
    }}}
    sources = {
        "orders": [order(id=f"o{i}") for i in range(count)],
        "supplier_receipts": [{"id": f"r{i}", "order_id": f"o{i}", "item_id": "i1",
            "supplier_id": "s1", "currency": "SAR", "amount": "200.00", "accepted_at": NOW}
            for i in range(count)],
        "supplier_returns": [{"id": f"ret{i}", "receipt_id": f"r{i}", "accepted": True,
            "amount": "20.00", "accepted_at": NOW} for i in range(count)],
    }
    return state, sources


def test_receipt_replay_has_bounded_whole_history_and_source_inspections():
    count = 256
    state, sources = cohort(count)
    saved = engine.reconcile(state, sources, NOW)
    unchanged = deepcopy((saved, sources))
    source_ids = {id(row) for row in sources["supplier_returns"]}
    work = {"fact_prefix_checks": 0, "source_field_reads": 0}

    def profile(frame, event, operation):
        if event != "c_call":
            return
        name, owner = getattr(operation, "__name__", None), getattr(operation, "__self__", None)
        # Observe the full real call tree using ordinary dicts/strings. A
        # helper cannot hide the repeated work, and custom row types cannot
        # accidentally select the compatibility fallback in this benchmark.
        if name == "startswith" and isinstance(owner, str):
            work["fact_prefix_checks"] += 1
        if name == "get" and id(owner) in source_ids:
            work["source_field_reads"] += 1

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        replayed = engine.reconcile(saved, sources, NOW)
    finally:
        sys.setprofile(previous)
    assert replayed == saved
    assert (saved, sources) == unchanged
    assert work["fact_prefix_checks"] < 100 * (len(saved["engine"]["facts"]) + count), work
    assert work["source_field_reads"] < 8 * (len(sources["supplier_returns"]) + count), work


def legacy_comparison(monkeypatch, state, sources):
    """Keep the original full scans as an independent behavior comparison."""
    untouched = deepcopy((state, sources))
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_receipt_return_source_index", lambda *_: None)
        patch.setattr(engine, "_receipt_return_fact_index", lambda *_: None)
        expected = engine.reconcile(state, sources, NOW)
    actual = engine.reconcile(state, sources, NOW)
    # JSON equality also observes all dictionary/list ordering, audit entries,
    # before/after values, timestamps, and the absence of retained indexes.
    assert json.dumps(actual) == json.dumps(expected)
    assert (state, sources) == untouched
    return actual


def return_row(identity, receipt_id="r0", **changes):
    return {"id": identity, "receipt_id": receipt_id, "amount": "20.00",
            "accepted": True, "accepted_at": NOW, **changes}


def saved_receipts(count=2):
    state, sources = cohort(count)
    sources["supplier_returns"] = []
    return engine.reconcile(state, sources, NOW), sources


def claim(result, receipt="r0", order_id="o0"):
    return result["engine"]["obligations"][f"supplier:{order_id}:i1:receipt:{receipt}"]


def test_interleaved_duplicate_returns_keep_receipt_order_money_and_audit(monkeypatch):
    state, sources = saved_receipts()
    sources["orders"][0]["status"] = "cancelled"
    sources["supplier_returns"] = [
        return_row("r1-first-in-input", "r1", amount="30"),
        return_row("r0-first", accepted_at="2020-01-01T00:00:00+00:00"),
        return_row("r0-second", amount="15.25"),
        return_row("r0-first", accepted_at="2020-01-01T00:00:00+00:00"),
        return_row("ignored", accepted=False, amount="bad", accepted_at="bad"),
        return_row("future", amount="bad", accepted_at="2099-01-01T00:00:00+00:00"),
        return_row("unrelated", "absent", amount="bad", accepted_at="bad"),
    ]
    result = legacy_comparison(monkeypatch, state, sources)
    assert claim(result)["confirmed"] == "164.75"
    assert claim(result, "r1", "o1")["confirmed"] == "170.00"
    assert [key for key in result["engine"]["facts"] if key.startswith("return:")] == [
        "return:r0-first", "return:r0-second", "return:r1-first-in-input"]
    assert result["engine"]["issues"] == []
    assert engine.reconcile(result, sources, NOW) == result


def test_failed_receipt_removes_only_new_returns_before_later_receipt_reuses_id(monkeypatch):
    state, sources = saved_receipts()
    state["engine"]["facts"]["return:old"] = {"receipt_id": "receipt:r0", "amount": "10.00"}
    sources["supplier_returns"] = [
        return_row("reusable", "r1"),
        return_row("reusable"),
        return_row("too-much", amount="200"),
    ]
    result = legacy_comparison(monkeypatch, state, sources)
    facts = result["engine"]["facts"]
    assert facts["return:old"] == state["engine"]["facts"]["return:old"]
    assert facts["return:reusable"] == {"receipt_id": "receipt:r1", "amount": "20.00"}
    assert "return:too-much" not in facts
    assert claim(result)["confirmed"] == "190.00"
    assert claim(result, "r1", "o1")["confirmed"] == "180.00"
    assert result["engine"]["issues"] == [
        {"code": "supplier_return_exceeds_receipt", "source_id": "receipt:r0"}]


@pytest.mark.parametrize("change, expected", [
    ({"amount": "11"}, "confirmed_evidence_changed"),
    ({"amount": "bad"}, "amount_invalid"),
    ({"amount": None}, "amount_missing"),
])
def test_failed_duplicate_keeps_saved_evidence_and_rolls_back_earlier_new_rows(monkeypatch, change, expected):
    state, sources = saved_receipts()
    state["engine"]["facts"]["return:old"] = {"receipt_id": "receipt:r0", "amount": "10.00"}
    sources["supplier_returns"] = [return_row("new"), return_row("old", **change)]
    result = legacy_comparison(monkeypatch, state, sources)
    assert "return:new" not in result["engine"]["facts"]
    assert result["engine"]["facts"]["return:old"] == state["engine"]["facts"]["return:old"]
    assert claim(result)["confirmed"] == "190.00"
    assert result["engine"]["issues"] == [{"code": expected, "source_id": "receipt:r0"}]


@pytest.mark.parametrize("identity", [None, True, 1, 1.25, Decimal("1.25"), ["r0"], {"id": "r0"}])
def test_source_identity_conversion_retains_original_matching(monkeypatch, identity):
    state, sources = cohort(1)
    sources["supplier_receipts"][0]["id"] = str(identity)
    sources["supplier_returns"] = [return_row("converted", identity)]
    result = legacy_comparison(monkeypatch, state, sources)
    assert claim(result, str(identity))["confirmed"] == "180.00"
    assert result["engine"]["issues"] == []


@pytest.mark.parametrize("row", [
    {"receipt_id": [], "amount": "bad"},
    {"receipt_id": "receipt:unrelated", "amount": "bad"},
])
def test_unusual_or_inconsistent_saved_return_keeps_lazy_financial_behavior(monkeypatch, row):
    state, sources = saved_receipts()
    state["engine"]["facts"]["return:legacy"] = row
    result = legacy_comparison(monkeypatch, state, sources)
    assert claim(result)["confirmed"] == "200.00"


@pytest.mark.parametrize("row", [{"amount": "1"}, None, [],
    {"receipt_id": "receipt:r0", "amount": "bad"},
    {"receipt_id": "receipt:r0", "amount": "210.00"}])
def test_malformed_saved_return_raises_same_error_at_original_boundary(monkeypatch, row):
    state, sources = saved_receipts()
    state["engine"]["facts"]["return:legacy"] = row
    untouched = deepcopy((state, sources))
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_receipt_return_source_index", lambda *_: None)
        patch.setattr(engine, "_receipt_return_fact_index", lambda *_: None)
        with pytest.raises(Exception) as expected:
            engine.reconcile(state, sources, NOW)
    with pytest.raises(type(expected.value)) as actual:
        engine.reconcile(state, sources, NOW)
    assert str(actual.value) == str(expected.value)
    assert (state, sources) == untouched


def test_same_return_identity_for_two_receipts_keeps_first_receipt_authority(monkeypatch):
    state, sources = saved_receipts()
    sources["supplier_returns"] = [return_row("shared", "r1"), return_row("shared", "r0")]
    result = legacy_comparison(monkeypatch, state, sources)
    assert result["engine"]["facts"]["return:shared"] == {
        "receipt_id": "receipt:r0", "amount": "20.00"}
    assert claim(result)["confirmed"] == "180.00"
    assert claim(result, "r1", "o1")["confirmed"] == "200.00"
    assert result["engine"]["issues"] == [
        {"code": "confirmed_evidence_changed", "source_id": "receipt:r1"}]


class UnusualKey:
    def __hash__(self):
        return hash("receipt_id")

    def __eq__(self, other):
        raise RuntimeError("deferred receipt field lookup")

    def __deepcopy__(self, memo):
        return self


class UnusualStringKey(str):
    __hash__ = UnusualKey.__hash__
    __eq__ = UnusualKey.__eq__
    __deepcopy__ = UnusualKey.__deepcopy__


class UnusualSourceKey(UnusualKey):
    def __hash__(self):
        return hash("supplier_returns")


@pytest.mark.parametrize("qualified", [False, True])
def test_unusual_outer_source_keys_do_not_trigger_eager_return_lookup(qualified):
    state, sources = saved_receipts() if qualified else cohort(0)
    sources.pop("supplier_returns")
    sources[UnusualSourceKey()] = []
    # The original reducer never requested supplier_returns before reaching
    # a currency-qualified receipt. This assertion is independent of either
    # new helper and detects a premature lookup outside the helper itself.
    if qualified:
        with pytest.raises(RuntimeError, match="deferred receipt field lookup"):
            engine.reconcile(state, sources, NOW)
    else:
        result = engine.reconcile(state, sources, NOW)
        assert result["engine"]["facts"] == {}
        assert result["engine"]["issues"] == []


@pytest.mark.parametrize("target", ["source", "facts"])
@pytest.mark.parametrize("key", [UnusualKey(), UnusualStringKey("receipt_id")])
@pytest.mark.parametrize("qualified", [False, True])
def test_unsupported_field_keys_are_not_read_before_original_boundary(monkeypatch, target, key, qualified):
    state, sources = saved_receipts() if qualified else cohort(0)
    if target == "source":
        sources["supplier_returns"] = [{key: "r0"}]
    else:
        state["engine"]["facts"]["return:unusual"] = {key: "receipt:r0"}
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_receipt_return_source_index", lambda *_: None)
        patch.setattr(engine, "_receipt_return_fact_index", lambda *_: None)
        if qualified:
            with pytest.raises(RuntimeError, match="deferred receipt field lookup"):
                engine.reconcile(state, sources, NOW)
        else:
            expected = engine.reconcile(state, sources, NOW)
    if qualified:
        with pytest.raises(RuntimeError, match="deferred receipt field lookup"):
            engine.reconcile(state, sources, NOW)
    else:
        assert engine.reconcile(state, sources, NOW) == expected


def test_missing_currency_skips_return_validation_and_preserves_issue_order(monkeypatch):
    state, sources = saved_receipts()
    state["engine"]["facts"]["return:malformed"] = {"amount": "bad"}
    for row in state["engine"]["obligations"].values():
        if row["kind"] == "supplier":
            row.pop("currency", None)
    result = legacy_comparison(monkeypatch, state, {})
    assert result["engine"]["issues"] == [
        {"code": "supplier_invoice_currency_missing", "source_id": "receipt:r0"},
        {"code": "supplier_invoice_currency_missing", "source_id": "receipt:r1"}]


def test_each_attempt_uses_its_own_facts_and_source_rows(monkeypatch):
    state, sources = saved_receipts()
    sources["supplier_returns"] = [return_row("same")]
    first = legacy_comparison(monkeypatch, state, sources)
    state["engine"]["facts"]["return:prior"] = {"receipt_id": "receipt:r1", "amount": "5.00"}
    sources["supplier_returns"][0]["receipt_id"] = "r1"
    second = legacy_comparison(monkeypatch, state, sources)
    assert first["engine"]["facts"]["return:same"]["receipt_id"] == "receipt:r0"
    assert claim(second)["confirmed"] == "200.00"
    assert claim(second, "r1", "o1")["confirmed"] == "175.00"


@pytest.mark.parametrize("name", ["_receipt_return_source_index", "_receipt_return_fact_index"])
def test_index_construction_observes_request_cancellation_without_mutating_inputs(monkeypatch, name):
    state, sources = cohort(300)
    untouched = deepcopy((state, sources))
    original = getattr(engine, name)
    stop, calls = Event(), []

    def cancellable(rows, checkpoint):
        def checked():
            calls.append(True)
            if len(calls) == 2:
                stop.set()
            checkpoint()
        return original(rows, checked)

    monkeypatch.setattr(engine, name, cancellable)
    with preparation_scope(stop), pytest.raises(asyncio.CancelledError):
        engine.reconcile(state, sources, NOW)
    assert len(calls) == 2
    assert (state, sources) == untouched
