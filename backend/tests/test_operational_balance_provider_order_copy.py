"""Retained provider inputs stay independent through refresh and reconciliation."""
import asyncio
from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event

from bson import BSON, Binary, Code, Decimal128, Int64, ObjectId, Regex, Timestamp
import pytest

import operational_balance_engine as engine
import operational_configured_provider_fees as fees
import operational_report_copy as copying
from operational_balance_store import initial
from operational_financial_activity_runtime import preparation_scope
from test_operational_balance_engine import NOW, START, order, policy


def retained_inputs():
    state = initial("synthetic-provider-copy-owner")
    state.update(status="active", started_at=START)
    payment = {"provider": "tabby", "gross": "100.00", "currency": "SAR",
               "plan_id": "retained-plan", "payment_method": "tabby_installment",
               "captures": [{"id": "retained-capture", "amount": "100.00", "captured_at": NOW}],
               "refunds": []}
    state["engine"]["provider_fee_inputs"] = {
        "orders": {"retained": {"order_number": "retained-reference", "currency": "SAR",
            "created_at": START, "updated_at": NOW, "payment": payment}},
        "settings": {"old": ["saved"]}, "policies": [], "source_mode": "legacy_settings",
        "statement_events": [{"id": "saved-statement", "proof": ["retained"]}],
        "statement_evidence_as_of": NOW, "average_profiles": [{"proof": ["saved-profile"]}],
    }
    sources = {"provider_fee_settings": {"payment_methods": [{"name": "تابي",
        "commission_percent": "2", "fixed_fee": "1", "vat_percent": "0"}]},
        "fee_policies": [policy()], "provider_fee_source_mode": "mz2_policies", "orders": []}
    return state, sources


def standard_refresh(monkeypatch, state, sources):
    with monkeypatch.context() as standard:
        standard.setattr(fees, "copy_report_value", deepcopy, raising=False)
        fees.refresh_provider_fee_inputs(state, sources)


def test_retained_map_uses_one_complete_copy_and_keeps_other_local_deepcopies(monkeypatch):
    state, sources = retained_inputs()
    retained = state["engine"]["provider_fee_inputs"]["orders"]
    sources["orders"] = [order(id="current", payment={"provider": "tabby", "gross": "90.00"})]
    before, complete, local = deepcopy((state, sources)), [], []

    def copy_map(value):
        complete.append(value)
        return copying.copy_report_value(value)

    def local_copy(value, *args, **kwargs):
        assert value is not retained, "retained orders still use repeated scalar deepcopy dispatch"
        local.append(value)
        return deepcopy(value, *args, **kwargs)

    monkeypatch.setattr(fees, "copy_report_value", copy_map, raising=False)
    monkeypatch.setattr(fees, "deepcopy", local_copy)
    fees.refresh_provider_fee_inputs(state, sources)
    assert len(complete) == 1 and complete[0] is retained and local
    assert set(state["engine"]["provider_fee_inputs"]["orders"]) == {"retained", "current"}
    assert retained == before[0]["engine"]["provider_fee_inputs"]["orders"] and sources == before[1]


def test_missing_settings_preserves_the_original_branch_without_copying(monkeypatch):
    state, _ = retained_inputs()
    previous = state["engine"]["provider_fee_inputs"]

    def unexpected(_value):
        raise AssertionError("no copy is admitted without provider settings")

    monkeypatch.setattr(fees, "copy_report_value", unexpected, raising=False)
    assert fees.refresh_provider_fee_inputs(state, {"orders": []}) is None
    assert state["engine"]["provider_fee_inputs"] is previous


def test_retained_adjustment_changes_only_the_new_independent_input_branch(monkeypatch):
    state, sources = retained_inputs()
    previous = state["engine"]["provider_fee_inputs"]
    # The caller may retain aliases into the old fee inputs elsewhere in state.
    state["retained_history"] = previous["orders"]
    state["engine"]["other_payment_history"] = previous["orders"]["retained"]["payment"]
    observation = {"order_id": "retained", "order_number": "retained-reference", "currency": "SAR",
        "provider": "tabby", "proof_hash": "synthetic-adjustment-proof",
        "customer_adjustment": {"proof_hash": "synthetic-adjustment-proof", "amount": "25.00",
                                "evidence": ["reviewed-adjustment"]}}
    sources["orders"] = [{"id": "retained", "order_number": "retained-reference", "currency": "SAR",
        "payment": {}, "retained_customer_adjustment": observation}]
    before, expected = deepcopy((state, sources)), deepcopy(state)
    standard_refresh(monkeypatch, expected, sources)
    fees.refresh_provider_fee_inputs(state, sources)
    assert state == expected and BSON.encode(state) == BSON.encode(expected)
    saved = state["engine"]["provider_fee_inputs"]["orders"]["retained"]
    assert saved["payment"]["customer_adjustment"] == observation["customer_adjustment"]
    for key in ("captures", "plan_id", "payment_method", "gross"):
        assert saved["payment"][key] == previous["orders"]["retained"]["payment"][key]
    assert previous == before[0]["engine"]["provider_fee_inputs"] and sources == before[1]
    assert state["retained_history"] is previous["orders"]
    assert state["engine"]["other_payment_history"] is previous["orders"]["retained"]["payment"]
    saved["payment"]["captures"][0]["amount"] = "999.00"
    saved["retained_customer_adjustment"]["customer_adjustment"]["evidence"].append("changed output")
    state["engine"]["provider_fee_inputs"]["statement_events"][0]["proof"].append("changed output")
    assert previous == before[0]["engine"]["provider_fee_inputs"] and sources == before[1]


def test_internal_aliases_cycles_and_fallback_tuple_keep_the_same_copy_boundary(monkeypatch):
    for copier in (deepcopy, copying.copy_report_value):
        state, sources = retained_inputs()
        original = state["engine"]["provider_fee_inputs"]["orders"]
        shared = original["retained"]
        original.update(alias=shared, self=original, tuple=(shared, original))
        shared["back_reference"] = original
        state["outside_copy"] = shared
        with monkeypatch.context() as variant:
            variant.setattr(fees, "copy_report_value", copier, raising=False)
            fees.refresh_provider_fee_inputs(state, sources)
        copied = state["engine"]["provider_fee_inputs"]["orders"]
        assert copied is not original
        assert copied["retained"] is copied["alias"] is copied["tuple"][0]
        assert copied["self"] is copied["tuple"][1] is copied["retained"]["back_reference"] is copied
        assert state["outside_copy"] is shared and copied["retained"] is not shared
        copied["retained"]["payment"]["captures"].append({"id": "new"})
        assert len(shared["payment"]["captures"]) == 1


def test_bson_types_and_mutable_code_scope_match_the_original_copy(monkeypatch):
    state, sources = retained_inputs()
    typed = {"decimal": Decimal128("9007199254740993.01"), "integer": Int64(7),
        "binary": Binary(b"saved", subtype=128), "code": Code("saved", {"values": [1]}),
        "regex": Regex("saved", "i"), "stamp": Timestamp(100, 1),
        "id": ObjectId("000000000000000000000001"), "at": datetime(2026, 10, 1, tzinfo=timezone.utc)}
    state["engine"]["provider_fee_inputs"]["orders"]["retained"]["typed"] = typed
    before, expected = deepcopy((state, sources)), deepcopy(state)
    standard_refresh(monkeypatch, expected, sources)
    fees.refresh_provider_fee_inputs(state, sources)
    assert state == expected and BSON.encode(state) == BSON.encode(expected)
    actual = state["engine"]["provider_fee_inputs"]["orders"]["retained"]["typed"]
    assert {key: type(value) for key, value in actual.items()} == {key: type(value) for key, value in typed.items()}
    actual["code"].scope["values"].append(2)
    assert typed == before[0]["engine"]["provider_fee_inputs"]["orders"]["retained"]["typed"]
    assert sources == before[1]


@pytest.mark.parametrize("factory", [dict, OrderedDict, lambda value: defaultdict(list, value)])
def test_python_container_and_key_fallback_preserves_types_and_independence(monkeypatch, factory):
    state, sources = retained_inputs()
    original = factory({("tuple", 7): {b"bytes": [Decimal("1.001"), {1, 2}, frozenset((3, 4))]},
                        "retained": state["engine"]["provider_fee_inputs"]["orders"]["retained"]})
    state["engine"]["provider_fee_inputs"]["orders"] = original
    expected = deepcopy(state)
    standard_refresh(monkeypatch, expected, sources)
    fees.refresh_provider_fee_inputs(state, sources)
    result = state["engine"]["provider_fee_inputs"]["orders"]
    assert state == expected and type(result) is type(original)
    result[("tuple", 7)][b"bytes"][1].add(5)
    assert original[("tuple", 7)][b"bytes"][1] == {1, 2}
    if isinstance(original, defaultdict):
        assert result.default_factory is list


def test_custom_hook_shared_memo_order_and_atomic_overrides_match_deepcopy(monkeypatch):
    calls = []
    atom = "synthetic-provider-memo-atom"

    class Saved(dict):
        def __deepcopy__(self, memo):
            calls.append("saved")
            result = Saved()
            memo[id(self)] = result
            result.update({key: deepcopy(value, memo) for key, value in self.items()})
            return result

    class Override:
        def __deepcopy__(self, memo):
            calls.append("override")
            memo[id(atom)] = "copied-atom"
            return "copied-hook"

    for copier in (deepcopy, copying.copy_report_value):
        state, sources = retained_inputs()
        shared = Saved(evidence=["retained"])
        original = {"first": shared, "alias": shared, "hook": Override(), atom: [atom]}
        state["engine"]["provider_fee_inputs"]["orders"] = original
        calls.clear()
        with monkeypatch.context() as variant:
            variant.setattr(fees, "copy_report_value", copier, raising=False)
            fees.refresh_provider_fee_inputs(state, sources)
        result = state["engine"]["provider_fee_inputs"]["orders"]
        assert calls == ["saved", "override"]
        assert type(result["first"]) is Saved and result["first"] is result["alias"]
        assert result["first"] is not shared and result["copied-atom"] == ["copied-atom"]
        result["first"]["evidence"].append("changed output")
        assert shared == {"evidence": ["retained"]} and original[atom] == [atom]


@pytest.mark.parametrize("failure_at", ["retained_map", "new_payment", "settings", "policies", "history"])
def test_copy_failures_leave_the_previous_branch_and_sources_unmodified(monkeypatch, failure_at):
    class Rejected(dict):
        def __deepcopy__(self, memo):
            raise ValueError("synthetic provider input copy failure")

    for copier in (deepcopy, copying.copy_report_value):
        state, sources = retained_inputs()
        previous = state["engine"]["provider_fee_inputs"]
        original_payment = previous["orders"]["retained"]["payment"]
        if failure_at == "retained_map":
            previous["orders"]["rejected"] = Rejected(saved=["proof"])
        elif failure_at == "new_payment":
            sources["orders"] = [order(id="new", payment=Rejected(provider="tabby", gross="100.00"))]
        elif failure_at == "settings":
            sources["provider_fee_settings"] = Rejected(values=["saved"])
        elif failure_at == "policies":
            sources["fee_policies"] = Rejected(values=["saved"])
        else:
            previous["statement_events"] = Rejected(values=["saved"])
        before_payment = deepcopy(original_payment)
        before_fields = {key: value for key, value in previous.items()}
        source_fields = {key: value for key, value in sources.items()}
        with monkeypatch.context() as variant:
            variant.setattr(fees, "copy_report_value", copier, raising=False)
            with pytest.raises(ValueError, match="synthetic provider input copy failure"):
                fees.refresh_provider_fee_inputs(state, sources)
        assert state["engine"]["provider_fee_inputs"] is previous
        assert all(previous[key] is value for key, value in before_fields.items())
        assert all(sources[key] is value for key, value in source_fields.items())
        assert original_payment == before_payment


@pytest.mark.parametrize("variant", ["replay", "missing_current", "malformed_order"])
def test_complete_reducer_retains_paid_facts_history_and_replay_parity(monkeypatch, variant):
    state = initial("synthetic-provider-copy-owner")
    state.update(status="active", started_at=START)
    sources = {"provider_fee_settings": {}, "fee_policies": [policy()], "orders": [order(
        id="paid", cod_amount="0", payment={"provider": "tabby", "currency": "SAR", "gross": "100.00",
        "captures": [{"id": "paid-capture", "amount": "100.00", "captured_at": NOW}], "refunds": []})]}
    state = engine.reconcile(state, sources, NOW)
    assert state["engine"]["obligations"]["provider:paid"]["confirmed"] == "100.00"
    state["audit"] = [{"id": "saved", "proof": ["manual-history"]}]
    state["requests"] = {"saved": {"result": {"id": "saved", "proof": ["manual-history"]}}}
    if variant == "missing_current":
        sources["orders"] = []
    elif variant == "malformed_order":
        sources["orders"][0]["cod_amount"] = "invalid"
    before = deepcopy((state, sources))
    with monkeypatch.context() as standard:
        standard.setattr(fees, "copy_report_value", deepcopy, raising=False)
        expected = engine.reconcile(state, sources, NOW)
    actual = engine.reconcile(state, sources, NOW)
    assert actual == expected and BSON.encode(actual) == BSON.encode(expected)
    assert (state, sources) == before
    assert actual["engine"]["obligations"]["provider:paid"]["confirmed"] == "100.00"
    assert actual["movements"] == state["movements"] == []
    assert actual["audit"] == state["audit"] and actual["requests"] == state["requests"]
    if variant == "replay":
        assert actual == state
    copied_payment = actual["engine"]["provider_fee_inputs"]["orders"]["paid"]["payment"]
    copied_payment["captures"][0]["amount"] = "999.00"
    actual["audit"][0]["proof"].append("changed output")
    assert (state, sources) == before


def test_cooperative_cancellation_discards_the_unassigned_retained_copy(monkeypatch):
    state, sources = retained_inputs()
    previous = state["engine"]["provider_fee_inputs"]
    previous["orders"].update({str(index): {"proof": [index]} for index in range(2000)})
    before, checks, stop = deepcopy((state, sources)), [], Event()
    original_checkpoint = copying.preparation_checkpoint

    def checkpoint():
        checks.append(True)
        if len(checks) == 3:
            stop.set()
        original_checkpoint()

    monkeypatch.setattr(copying, "preparation_checkpoint", checkpoint)
    with preparation_scope(stop):
        with pytest.raises(asyncio.CancelledError):
            fees.refresh_provider_fee_inputs(state, sources)
    assert len(checks) == 3 and state["engine"]["provider_fee_inputs"] is previous
    assert (state, sources) == before
