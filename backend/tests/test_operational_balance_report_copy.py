"""Report copies retain financial isolation and the standard copy protocol."""
import asyncio
from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json

from bson import Binary, Code, Decimal128, Int64, Regex, Timestamp
import pytest

import operational_report_copy as copying
from operational_balance_service import report
from operational_balance_store import initial
from operational_financial_position import build_financial_position
from test_operational_financial_position_views import rich_state, cost_exposure
from test_operational_provider_report_profile_memo import repeated_state, scoped_state, VIEWS


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.mark.parametrize("fixture", [rich_state, scoped_state, lambda: repeated_state(40)])
@pytest.mark.parametrize("view", VIEWS)
def test_all_twelve_views_match_standard_copy_and_preserve_inputs(monkeypatch, fixture, view):
    state, exposure = fixture(), cost_exposure()
    before = deepcopy((state, exposure))
    with monkeypatch.context() as standard:
        standard.setattr(copying, "copy_report_value", deepcopy)
        expected = build_financial_position(state, cost_exposure=exposure, view=view)
    actual = build_financial_position(state, cost_exposure=exposure, view=view)
    assert encoded(actual) == encoded(expected)
    assert (state, exposure) == before


def credit_state():
    state = initial("synthetic-report-copy-owner")
    state.update(status="active", started_at="2030-04-01T00:00:00+00:00")
    state["engine"].update(as_of="2030-04-05T00:00:00+00:00", obligations={}, facts={})
    state["openings"] = {"bank": {"id": "bank-opening", "party_type": "bank", "party_id": "bank",
        "currency": "SAR", "name": "Synthetic bank", "amount": "0.00", "direction": "for_us"}}
    for kind, direction in (("supplier", "payable"), ("provider", "receivable")):
        identity = kind + ":synthetic"
        state["engine"]["obligations"][identity] = {"id": identity, "kind": kind,
            "party_type": kind, "party_id": "tabby" if kind == "provider" else "synthetic-supplier",
            "currency": "SAR", "direction": direction, "confirmed": "100.00", "expected": "0.00",
            "business_date": "2030-04-03", "evidence_ids": ["synthetic-proof"],
            "evidence": {"retained": ["original"]}}
    for index, (kind, amount, direction, identity) in enumerate([
        ("supplier", "150.00", "outgoing", "supplier:synthetic"),
        ("supplier", "25.00", "incoming", "credit:supplier:synthetic"),
        ("provider", "120.00", "incoming", "provider:synthetic"),
        ("provider", "5.00", "outgoing", "credit:provider:synthetic"),
    ]):
        state["movements"].append({"id": "synthetic-movement-" + str(index), "kind": "payment",
            "party_type": kind, "party_id": "tabby" if kind == "provider" else "synthetic-supplier",
            "currency": "SAR", "amount": amount, "direction": direction,
            "bank_kind": "bank", "bank_id": "bank", "occurred_at": "2030-04-04T00:00:00+00:00",
            "allocations": [{"obligation_id": identity, "amount": amount}]})
    state["engine"]["facts"]["synthetic-proof"] = {"evidence": ["original"]}
    return state


def test_credit_derivation_settlement_and_returned_data_remain_isolated(monkeypatch):
    state = credit_state()
    before = deepcopy(state)
    with monkeypatch.context() as standard:
        standard.setattr(copying, "copy_report_value", deepcopy)
        expected = report(state)
    actual = report(state)
    assert encoded(actual) == encoded(expected)
    claims = {row["id"]: row for row in actual["obligations"]}
    for identity, confirmed, outstanding in (("supplier", "50.00", "25.00"),
                                              ("provider", "20.00", "15.00")):
        row = claims["credit:" + identity + ":synthetic"]
        assert row["confirmed"] == confirmed and row["outstanding"] == outstanding
    assert next(row for row in actual["parties"] if row["party_type"] == "bank")["actual"] == "-10.00"
    actual["details"]["facts"]["synthetic-proof"]["evidence"].append("changed output")
    claims["supplier:synthetic"]["evidence"]["retained"].append("changed output")
    assert state == before and not any(key.startswith("credit:") for key in state["engine"]["obligations"])


@pytest.mark.parametrize("view", VIEWS)
def test_credit_snapshot_is_byte_identical_in_every_view(monkeypatch, view):
    state = credit_state()
    before = deepcopy(state)
    with monkeypatch.context() as standard:
        standard.setattr(copying, "copy_report_value", deepcopy)
        expected = build_financial_position(state, view=view)
    assert encoded(build_financial_position(state, view=view)) == encoded(expected)
    assert state == before


def test_bson_python_types_and_nonstring_keys_use_the_standard_semantics():
    typed = {"decimal": Decimal("10.001"), "bson_decimal": Decimal128("10.001"),
        "integer": Int64(7), "binary": Binary(b"synthetic", subtype=128),
        "code": Code("synthetic", {"value": [1]}), "regex": Regex("synthetic", "i"),
        "stamp": Timestamp(100, 1), "at": datetime(2030, 4, 1, tzinfo=timezone.utc),
        "tuple": ({"nested": [1]},), "set": {1, 2}, "frozen": frozenset((1, 2)),
        "ordered": OrderedDict((("z", [1]), ("a", [2]))),
        "default": defaultdict(list, {"x": [3]}), ("tuple", 7): {b"bytes": [None, True, 3, 1.25]}}
    expected, actual = deepcopy(typed), copying.copy_report_value(typed)
    assert actual == expected
    assert {key: type(value) for key, value in actual.items()} == {key: type(value) for key, value in expected.items()}
    actual["tuple"][0]["nested"].append(2)
    assert typed["tuple"][0]["nested"] == [1]
    assert actual["default"].default_factory is list


def test_aliases_and_cycles_cross_plain_containers_and_fallback_tuples():
    shared = {"values": []}
    source = {"first": shared, "second": shared}
    source["self"] = source
    source["tuple"] = (shared, source)
    shared["values"].append(source)
    for clone in (deepcopy(source), copying.copy_report_value(source)):
        assert clone is not source
        assert clone["first"] is clone["second"] is clone["tuple"][0]
        assert clone["self"] is clone["tuple"][1] is clone["first"]["values"][0] is clone
        assert clone["first"] is not shared


def test_custom_hooks_subclasses_shared_memo_and_original_keepalive():
    seen = []

    class CustomString(str):
        def __deepcopy__(self, memo):
            seen.append("string")
            return CustomString(self + "-copied")

    class Hook:
        def __deepcopy__(self, memo):
            seen.append("hook")
            assert any(value is self.parent["first"] for value in memo[id(memo)])
            result = Hook()
            memo[id(self)] = result
            result.parent = deepcopy(self.parent, memo)
            return result

    class CustomDict(dict):
        def __deepcopy__(self, memo):
            seen.append("dict")
            return {"custom_hook": True}

    source = {"first": [{"plain": True}], "text": CustomString("synthetic")}
    hook = Hook(); hook.parent = source
    source.update(hook=hook, repeated=hook, custom=CustomDict(ignored=True))
    for copier in (deepcopy, copying.copy_report_value):
        seen.clear()
        value = copier(source)
        assert seen == ["string", "hook", "dict"]
        assert type(value["text"]) is CustomString and value["text"] == "synthetic-copied"
        assert value["hook"] is value["repeated"] and value["hook"].parent is value
        assert value["custom"] == {"custom_hook": True}
    assert source["text"] == "synthetic" and source["custom"] == {"ignored": True}


def test_copy_failure_and_cooperative_cancellation_return_no_partial_value(monkeypatch):
    class Invalid:
        def __deepcopy__(self, memo):
            raise ValueError("synthetic copy rejected")

    for copier in (deepcopy, copying.copy_report_value):
        with pytest.raises(ValueError, match="synthetic copy rejected"):
            copier({"already": [1], "invalid": Invalid()})
    rows = [{"nested": [index]} for index in range(2000)]
    before, checks = deepcopy(rows), []

    def stop():
        checks.append(1)
        if len(checks) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(copying, "preparation_checkpoint", stop)
    with pytest.raises(asyncio.CancelledError):
        copying.copy_report_value(rows)
    assert len(checks) == 3 and rows == before


def test_custom_hook_memo_override_applies_to_later_atomic_values_and_keys():
    atom = "synthetic-atomic-memo-value"

    class Override:
        def __deepcopy__(self, memo):
            memo[id(atom)] = "replaced-by-copy-hook"
            return "hook-result"

    source = {"first": Override(), atom: [atom]}
    assert copying.copy_report_value(source) == deepcopy(source) == {
        "first": "hook-result", "replaced-by-copy-hook": ["replaced-by-copy-hook"]}
    assert source[atom] == [atom]


def test_completed_container_restores_its_memo_after_a_custom_hook():
    class OverrideParent:
        def __deepcopy__(self, memo):
            memo[id(self.parent)] = {"temporary": True}
            return "hook-result"

    parent = {}
    hook = OverrideParent(); hook.parent = parent
    parent["hook"] = hook
    for copier in (deepcopy, copying.copy_report_value):
        result = copier([parent, parent])
        assert result == [{"hook": "hook-result"}, {"hook": "hook-result"}]
        assert result[0] is result[1]
    assert parent["hook"] is hook
