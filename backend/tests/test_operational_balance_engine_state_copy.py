"""The real reducer retains a complete independent copy and the copy protocol."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event

from bson import BSON, Binary, Code, Decimal128, Int64, ObjectId, Regex, Timestamp
import pytest

import operational_balance_engine as engine
import operational_demand_refresh as demand
import operational_report_copy as copying
from operational_balance_store import initial
from operational_financial_activity_runtime import preparation_scope
from test_operational_balance_demand_refresh import DB, Request, allowed
from test_operational_balance_engine import NOW, START, order


def inputs():
    state = initial("synthetic-copy-owner")
    state.update(status="active", started_at=START)
    sources = {"orders": [order(id="courier-order", order_number="synthetic-1"),
        order(id="driver-order", order_number="synthetic-2",
              carrier={"id": "driver", "party_type": "store_driver", "cost": "20.01"})],
        "provider_fee_settings": {}}
    state = engine.reconcile(state, sources, NOW)
    state["movements"] = [{"id": "manual", "kind": "payment", "party_type": "courier",
        "party_id": "courier", "currency": "SAR", "amount": "17.25", "allocations": []}]
    state["openings"] = {"bank": {"amount": "300.01", "currency": "SAR"}}
    state["requests"] = {"retained": {"hash": "saved", "result": {"id": "manual", "proof": ["saved"]}}}
    state["audit"] = [{"id": "saved", "after": {"amount": "17.25", "proof": ["saved"]}}]
    state["engine"]["facts"]["retained:proof"] = {"amount": Decimal128("9007199254740993.01"),
        "currency": "SAR", "proof": {"bytes": Binary(b"saved", subtype=128), "values": [Int64(7)]}}
    state["engine"]["profit_snapshot"] = {"retained": ["complete old snapshot"]}
    state["engine"]["issues"] = [{"code": "old diagnostic", "source_id": "retained"}]
    return state, sources


def standard(monkeypatch, state, sources):
    with monkeypatch.context() as legacy:
        legacy.setattr(engine, "copy_report_value", deepcopy, raising=False)
        return engine.reconcile(state, sources, NOW)


@pytest.mark.parametrize("change", ["replay", "updated_quote", "malformed_order"])
def test_complete_real_reconciliation_matches_standard_copy_and_retains_history(monkeypatch, change):
    state, sources = inputs()
    if change == "updated_quote":
        sources["orders"][0].update(carrier={"id": "other", "cost": "25.01"}, updated_at=NOW)
    elif change == "malformed_order":
        sources["orders"][0]["cod_amount"] = "invalid"
    before = deepcopy((state, sources))
    expected = standard(monkeypatch, state, sources)
    actual = engine.reconcile(state, sources, NOW)
    assert actual == expected and BSON.encode(actual) == BSON.encode(expected)
    assert (state, sources) == before
    for field in ("movements", "openings", "requests", "audit"):
        assert actual[field] == state[field] and actual[field] is not state[field]
    assert actual["engine"]["profit_snapshot"] == state["engine"]["profit_snapshot"]
    # Mutating retained nested history in the successor cannot change its input.
    actual["audit"][0]["after"]["proof"].append("changed successor")
    actual["requests"]["retained"]["result"]["proof"].append("changed successor")
    actual["engine"]["facts"]["retained:proof"]["proof"]["values"].append(Int64(8))
    actual["engine"]["audit"][0]["after"]["confirmed"] = "999.99"
    assert (state, sources) == before


def test_reducer_uses_one_complete_optimized_copy_and_keeps_local_deepcopies(monkeypatch):
    state, sources = inputs()
    before = deepcopy((state, sources))
    copies, local = [], []

    def complete(value):
        copies.append(value)
        return copying.copy_report_value(value)

    def scalar_or_local(value, *args, **kwargs):
        assert value is not state, "the whole state still uses recursive scalar deepcopy dispatch"
        local.append(value)
        return deepcopy(value, *args, **kwargs)

    monkeypatch.setattr(engine, "copy_report_value", complete, raising=False)
    monkeypatch.setattr(engine, "deepcopy", scalar_or_local)
    result = engine.reconcile(state, sources, NOW)
    assert len(copies) == 1 and copies[0] is state and local
    assert result is not state and (state, sources) == before


@pytest.mark.parametrize("status,started", [("active", START), ("ACTIVE", START), ("draft", START), ("active", None)])
def test_all_callers_retain_bson_python_types_and_independent_nested_values(monkeypatch, status, started):
    state = initial("synthetic-copy-owner")
    state.update(status=status, started_at=started)
    state["retained"] = {
        "decimal": Decimal("10.001"), "bson_decimal": Decimal128("10.001"),
        "integer": Int64(7), "binary": Binary(b"saved", subtype=128),
        "code": Code("saved", {"value": [1]}), "regex": Regex("saved", "i"),
        "stamp": Timestamp(100, 1), "id": ObjectId("000000000000000000000001"),
        "at": datetime(2026, 10, 1, tzinfo=timezone.utc), "tuple": ({"nested": [1]},),
        "set": {1, 2}, "frozen": frozenset((1, 2)), b"bytes": [None, True, 3, 1.25],
    }
    before = deepcopy(state)
    expected, actual = standard(monkeypatch, state, {}), engine.reconcile(state, {}, NOW)
    assert actual == expected and state == before
    assert {key: type(value) for key, value in actual["retained"].items()} == {
        key: type(value) for key, value in expected["retained"].items()}
    actual["retained"]["tuple"][0]["nested"].append(2)
    actual["retained"]["code"].scope["value"].append(2)
    assert state == before


def test_aliases_and_cycles_survive_the_real_reducer_and_fallback_tuple(monkeypatch):
    state = initial("synthetic-copy-owner")
    state.update(status="active", started_at=START)
    shared = {"values": []}
    state.update(first=shared, second=shared, self=state, retained_tuple=(shared, state))
    shared["values"].append(state)
    for copier in (deepcopy, copying.copy_report_value):
        with monkeypatch.context() as variant:
            variant.setattr(engine, "copy_report_value", copier, raising=False)
            result = engine.reconcile(state, {}, NOW)
        assert result is not state and result["first"] is not shared
        assert result["first"] is result["second"] is result["retained_tuple"][0]
        assert result["self"] is result["retained_tuple"][1] is result["first"]["values"][0] is result
        result["first"]["values"].append("changed successor")
        assert len(shared["values"]) == 1 and shared["values"][0] is state
    assert not state["engine"] and state["self"] is state


def test_custom_copy_hooks_aliases_and_failures_match_standard_reconciliation(monkeypatch):
    calls = []

    class Saved(dict):
        def __deepcopy__(self, memo):
            calls.append("saved")
            result = Saved()
            memo[id(self)] = result
            result.update({key: deepcopy(value, memo) for key, value in self.items()})
            return result

    state = initial("synthetic-copy-owner")
    state.update(status="active", started_at=START)
    state["first"] = state["second"] = Saved(proof=["saved"])
    for copier in (deepcopy, copying.copy_report_value):
        calls.clear()
        with monkeypatch.context() as variant:
            variant.setattr(engine, "copy_report_value", copier, raising=False)
            result = engine.reconcile(state, {}, NOW)
        assert calls == ["saved"] and type(result["first"]) is Saved
        assert result["first"] is result["second"] and result["first"] is not state["first"]
        result["first"]["proof"].append("changed successor")
        assert state["first"] == {"proof": ["saved"]}

    class Rejected:
        def __deepcopy__(self, memo):
            raise ValueError("synthetic copy failure")

    state["rejected"] = Rejected()
    for copier in (deepcopy, copying.copy_report_value):
        with monkeypatch.context() as variant:
            variant.setattr(engine, "copy_report_value", copier, raising=False)
            with pytest.raises(ValueError, match="synthetic copy failure"):
                engine.reconcile(state, {}, NOW)
    assert not state["engine"] and state["first"] == {"proof": ["saved"]}


def test_existing_preparation_scope_can_cancel_inside_the_real_reducer_copy(monkeypatch):
    state = initial("synthetic-copy-owner")
    state.update(status="active", started_at=START)
    state["retained"] = [{"nested": [index]} for index in range(2000)]
    before, checks, stop = deepcopy(state), [], Event()
    original_checkpoint = copying.preparation_checkpoint

    def checkpoint():
        checks.append(True)
        if len(checks) == 3:
            stop.set()
        original_checkpoint()

    monkeypatch.setattr(copying, "preparation_checkpoint", checkpoint)
    with preparation_scope(stop):
        with pytest.raises(asyncio.CancelledError):
            engine.reconcile(state, {}, NOW)
    assert len(checks) == 3 and state == before


def test_cancelled_demand_joins_the_complete_copy_before_returning(monkeypatch):
    async def scenario():
        state = initial("synthetic-copy-owner")
        state.update(status="active", started_at=START)
        state["retained"] = [{"nested": [index]} for index in range(2000)]
        before = deepcopy(state)
        loop, entered, finished, release = asyncio.get_running_loop(), asyncio.Event(), Event(), Event()
        checks = []
        original_checkpoint = copying.preparation_checkpoint
        context = demand.DemandRefreshContext(Request(), DB(), state["owner_id"], state["owner_id"], allowed)
        token = demand._CURRENT.set(context)

        def checkpoint():
            checks.append(True)
            if len(checks) == 3:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5), "test did not release the owned copy"
            # Keep the real checkpoint semantics. Demand cancellation is
            # checked by the reducer after its complete copy; no test hook
            # adds a cancellation capability to the copier itself.
            original_checkpoint()

        def calculate():
            try:
                return engine.reconcile(state, {}, NOW)
            finally:
                finished.set()

        monkeypatch.setattr(copying, "preparation_checkpoint", checkpoint)
        task = asyncio.create_task(demand.run_calculation(calculate))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            # Let run_calculation mark the shared demand cancelled and join its
            # worker, while the worker remains deliberately inside the copy.
            await asyncio.sleep(0)
            assert context.cancelled.is_set() and not task.done() and not finished.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert finished.is_set() and state == before and len(checks) > 3
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            demand._CURRENT.reset(token)
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())
