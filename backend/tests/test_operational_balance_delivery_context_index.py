"""Bound report-local delivery lookup work without changing financial output."""
from copy import deepcopy
import asyncio
import dis
import sys
from threading import Event
import warnings

import pytest

import operational_financial_position_core as core
from operational_financial_activity_runtime import preparation_scope


def delivery_inputs(count):
    obligations = [{
        "id": f"shipping:{index}", "kind": "shipping", "party_type": "courier",
        "party_id": "carrier", "currency": "SAR", "confirmed": "10.00",
        "settled": "0.00", "outstanding": "10.00",
    } for index in range(count)]
    details = {"facts": {}, "cod_reports": {
        f"cod:{index}": {"order_id": str(index), "currency": "SAR",
                         "customer_outstanding": "30.00"}
        for index in range(count)
    }}
    return obligations, details, "SAR", {("courier", "carrier"): "Carrier"}, []


def counted_delivery_work(inputs):
    """Count all iterator advances in this module, including index creation.

    Counting FOR_ITER rather than wall time covers the existing nested lookup
    and any new preparation loop without depending on host load or exact source
    lines. The counter includes helper functions and comprehensions in the
    production module; it does not count test-fixture preparation.
    """
    advances = 0
    filename = core._deliveries.__code__.co_filename
    for_iter = dis.opmap["FOR_ITER"]

    def trace(frame, event, _argument):
        nonlocal advances
        if event == "call":
            if frame.f_code.co_filename != filename:
                return None
            frame.f_trace_lines = False
            frame.f_trace_opcodes = True
        elif event == "opcode" and frame.f_code.co_code[frame.f_lasti] == for_iter:
            advances += 1
        return trace

    previous = sys.gettrace()
    frame = sys._getframe()
    previous_opcodes = frame.f_trace_opcodes
    # CPython 3.12 enables opcode events when a current frame requests them.
    frame.f_trace_opcodes = True
    try:
        sys.settrace(trace)
        result = core._deliveries(*inputs)
    finally:
        sys.settrace(previous)
        frame.f_trace_opcodes = previous_opcodes
    return result, advances


def test_missing_collection_parties_do_not_scan_every_unrelated_shipment():
    work = {}
    for count in (128, 256):
        inputs = delivery_inputs(count)
        before = deepcopy(inputs)
        result, work[count] = counted_delivery_work(inputs)
        assert len(result["rows"]) == count
        assert inputs == before
        assert work[count] > 0
    # Doubling both orders and shipments must remain approximately linear.
    # The allowance includes all row formatting, validation and index work.
    assert work[256] <= work[128] * 2.5, work


def context_inputs(contexts, source=None, *, event_only=False, duplicate_fee=False):
    obligations, details, currency, names, issues = delivery_inputs(2)
    for index, (kind, party) in enumerate(contexts):
        names[kind, party] = f"Name {index}"
        if event_only:
            details["facts"][f"delivery_event:{index}"] = {
                "schema": "operational_delivery_status_v1", "order_id": "order",
                "party_type": kind, "party_id": party, "currency": currency,
            }
        else:
            obligations.append({
                "id": f"shipping:order:{index}", "order_id": "order", "kind": "shipping",
                "party_type": kind, "party_id": party, "currency": currency,
                "confirmed": "10.00", "settled": "2.00", "outstanding": "8.00",
            })
    if duplicate_fee:
        obligations.append({**obligations[-1], "id": "shipping:order:extra",
                            "confirmed": "7.25", "settled": "1.00", "outstanding": "6.25"})
    details["cod_reports"]["cod:order"] = {
        "order_id": "order", "currency": currency, "customer_outstanding": "30.00",
        "collection_amount": "22.00", **(source or {}),
    }
    return obligations, details, currency, names, issues


def scan_output(monkeypatch, function, *args, **kwargs):
    # None preserves the exact legacy full-shipping scan. Compare complete
    # outputs, not a second reimplementation of amounts or context matching.
    with monkeypatch.context() as original:
        original.setattr(core, "_shipping_context_index", lambda _shipping: None)
        return function(*args, **kwargs)


@pytest.mark.parametrize("contexts,source,expected", [
    ([("courier", "a")], {}, ("courier", "a")),
    ([("courier", "a"), ("store_driver", "b")], {}, (None, None)),
    ([("courier", "a"), ("store_driver", "b")], {"party_type": "courier"}, ("courier", "a")),
    ([("courier", "a"), ("courier", "b")], {"party_type": "courier"}, ("courier", None)),
    ([("courier", "a"), ("store_driver", "b")], {"party_id": "b"}, ("store_driver", "b")),
    ([("courier", "same"), ("store_driver", "same")], {"party_id": "same"}, (None, "same")),
    ([("courier", "a")], {"party_type": "store_driver"}, ("store_driver", None)),
    ([("courier", "a")], {"party_id": "unmatched"}, (None, "unmatched")),
    ([("courier", "a")], {"party_type": "", "party_id": False}, ("courier", "a")),
    ([], {}, (None, None)),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_partial_identity_keeps_exact_unique_or_ambiguous_context(monkeypatch, contexts, source, expected, reverse):
    inputs = context_inputs(list(reversed(contexts)) if reverse else contexts, source)
    before = deepcopy(inputs)
    baseline = scan_output(monkeypatch, core._deliveries, *inputs)
    actual = core._deliveries(*inputs)
    assert actual == baseline
    targets = [row for row in actual["rows"] if row["order_id"] == "order"]
    attached = [row for row in targets if row["customer_outstanding"] == "30.00"]
    assert len(attached) == 1
    assert (attached[0]["party_type"], attached[0]["party_id"]) == expected
    assert "collection_party_unproven" in attached[0]["incomplete_reasons"]
    assert actual["complete"] is False
    assert inputs == before


@pytest.mark.parametrize("event_only,duplicate_fee", [(True, False), (False, True)])
def test_event_only_context_and_multiple_fee_rows_remain_one_candidate(monkeypatch, event_only, duplicate_fee):
    inputs = context_inputs([("courier", "a")], event_only=event_only, duplicate_fee=duplicate_fee)
    before = deepcopy(inputs)
    actual = core._deliveries(*inputs)
    assert actual == scan_output(monkeypatch, core._deliveries, *inputs)
    rows = [row for row in actual["rows"] if row["order_id"] == "order"]
    assert len(rows) == 1 and rows[0]["id"] == "delivery:order"
    assert rows[0]["party_id"] == "a" and rows[0]["customer_outstanding"] == "30.00"
    assert rows[0]["shipping_confirmed"] == (None if event_only else "17.25")
    assert rows[0]["shipping_settled"] == (None if event_only else "3.00")
    assert inputs == before


def test_later_collection_for_same_context_retains_existing_overwrite_order(monkeypatch):
    inputs = context_inputs([("courier", "a")])
    inputs[1]["cod_reports"]["cod:later"] = {
        "order_id": "order", "currency": "SAR", "customer_outstanding": "19.75",
    }
    actual = core._deliveries(*inputs)
    assert actual == scan_output(monkeypatch, core._deliveries, *inputs)
    row = next(row for row in actual["rows"] if row["order_id"] == "order")
    assert row["customer_outstanding"] == "19.75"


def test_later_delivery_event_keeps_ambiguity_with_an_existing_fee_context(monkeypatch):
    inputs = context_inputs([("courier", "a")])
    inputs[1]["facts"]["delivery_event:order"] = {
        "schema": "operational_owner_delivery_event_v1", "order_id": "order",
        "party_type": "store_driver", "party_id": "b", "currency": "SAR",
    }
    actual = core._deliveries(*inputs)
    assert actual == scan_output(monkeypatch, core._deliveries, *inputs)
    rows = [row for row in actual["rows"] if row["order_id"] == "order"]
    assert len(rows) == 3
    attached = next(row for row in rows if row["customer_outstanding"] == "30.00")
    assert attached["party_type"] is None and attached["party_id"] is None


@pytest.mark.parametrize("missing", [None, "", 0, False])
def test_falsey_collection_identity_keeps_existing_fallback(monkeypatch, missing):
    inputs = context_inputs([("store_driver", "a")], {"party_type": missing, "party_id": missing})
    actual = core._deliveries(*inputs)
    assert actual == scan_output(monkeypatch, core._deliveries, *inputs)
    attached = next(row for row in actual["rows"] if row["order_id"] == "order")
    assert attached["party_type"] == "store_driver" and attached["party_id"] == "a"
    assert "collection_party_unproven" in attached["incomplete_reasons"]


@pytest.mark.parametrize("from_obligation", [False, True])
def test_complete_collection_identity_does_not_build_an_unused_index(monkeypatch, from_obligation):
    inputs = delivery_inputs(4)
    for key, source in inputs[1]["cod_reports"].items():
        if from_obligation:
            inputs[0].append({"id": key, "kind": "cod", "party_type": "courier", "party_id": "carrier",
                              "currency": "SAR", "confirmed": "30.00", "direction": "receivable"})
        else:
            source.update(party_type="courier", party_id="carrier")
    expected = scan_output(monkeypatch, core._deliveries, *inputs)

    def forbidden(_shipping):
        raise AssertionError("complete collection identity must not prepare an unused index")

    monkeypatch.setattr(core, "_shipping_context_index", forbidden)
    assert core._deliveries(*inputs) == expected


def test_noncanonical_order_equality_uses_original_scan(monkeypatch):
    class Alias(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            return str.__eq__(self, other) or other == "order"

    inputs = context_inputs([("courier", "a")])
    original = core._shipping_order
    monkeypatch.setattr(core, "_shipping_order", lambda row:
                        Alias("alias") if row.get("order_id") == "order" else original(row))
    expected = scan_output(monkeypatch, core._deliveries, *inputs)
    actual = core._deliveries(*inputs)
    assert actual == expected
    row = next(row for row in actual["rows"] if str(row["order_id"]) == "alias")
    assert row["party_id"] == "a" and row["customer_outstanding"] == "30.00"


def test_noncanonical_collection_order_also_uses_original_scan(monkeypatch):
    class Alias(str):
        __hash__ = str.__hash__

        def __str__(self):
            return self

        def __eq__(self, other):
            return str.__eq__(self, other) or other == "order"

    inputs = context_inputs([("courier", "a")])
    # The preceding ordinary orders have already built the local index.
    inputs[1]["cod_reports"]["cod:order"]["order_id"] = Alias("alias")
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="__str__ returned non-str.*", category=DeprecationWarning)
        expected = scan_output(monkeypatch, core._deliveries, *inputs)
        actual = core._deliveries(*inputs)
    assert actual == expected
    row = next(row for row in actual["rows"] if row["order_id"] == "order")
    assert row["party_id"] == "a" and row["customer_outstanding"] == "30.00"


def test_index_is_built_once_per_call_and_never_reused_after_input_change(monkeypatch):
    inputs = delivery_inputs(8)
    original = core._shipping_context_index
    built = []

    def tracked(shipping):
        built.append(tuple(shipping))
        return original(shipping)

    monkeypatch.setattr(core, "_shipping_context_index", tracked)
    first = core._deliveries(*inputs)
    assert len(built) == 1
    inputs[0][0]["party_id"] = "new-carrier"
    second = core._deliveries(*inputs)
    assert len(built) == 2
    assert first != second
    assert second == scan_output(monkeypatch, core._deliveries, *inputs)
    assert next(row for row in second["rows"] if row["order_id"] == "0")["party_id"] == "new-carrier"


def test_cancellation_during_index_build_leaves_no_partial_reusable_value(monkeypatch):
    inputs = delivery_inputs(8)
    before = deepcopy(inputs)
    expected = scan_output(monkeypatch, core._deliveries, *inputs)
    stop = Event()
    original = core._shipping_context_index
    checkpoint = core.preparation_checkpoint
    calls = []

    def cancelled_index(shipping):
        def checked():
            calls.append(None)
            if len(calls) == 3:
                stop.set()
            checkpoint()

        with monkeypatch.context() as stopping:
            stopping.setattr(core, "preparation_checkpoint", checked)
            return original(shipping)

    with monkeypatch.context() as stopping:
        stopping.setattr(core, "_shipping_context_index", cancelled_index)
        with preparation_scope(stop), pytest.raises(asyncio.CancelledError):
            core._deliveries(*inputs)
    assert len(calls) == 3 and inputs == before
    with preparation_scope(Event()):
        assert core._deliveries(*inputs) == expected


@pytest.mark.parametrize("view", ["summary", "all", "couriers", "drivers", "expected"])
def test_full_financial_report_matches_original_scan_for_every_affected_view(monkeypatch, view):
    from operational_financial_position import build_financial_position
    from test_operational_financial_position_views import rich_state
    from test_operational_balance_financial_position import NOW, cost_exposure, obligation

    state, exposure = rich_state(), cost_exposure()
    for suffix, kind, party in (("a", "courier", "a"), ("b", "store_driver", "b")):
        obligation(state, f"shipping:partial:{suffix}", "shipping", kind, "12.34", party=party,
                   order_id="partial", order_number="SYNTHETIC", completed_at=NOW)
    state["engine"].setdefault("cod_reports", {})["cod:partial"] = {
        "order_id": "partial", "currency": "SAR", "party_type": "store_driver",
        "customer_outstanding": "56.78", "collection_amount": "23.45",
    }
    obligation(state, "shipping:partial:foreign", "shipping", "store_driver", "3.21",
               party="foreign-driver", currency="USD", order_id="partial")
    state["engine"]["cod_reports"]["cod:partial-foreign"] = {
        "order_id": "partial", "currency": "USD", "party_type": "store_driver",
        "customer_outstanding": "4.56",
    }
    before = deepcopy((state, exposure))
    expected = scan_output(monkeypatch, build_financial_position, state,
                           cost_exposure=exposure, generated_at=NOW, view=view)
    actual = build_financial_position(state, cost_exposure=exposure, generated_at=NOW, view=view)
    assert actual == expected
    assert (state, exposure) == before
