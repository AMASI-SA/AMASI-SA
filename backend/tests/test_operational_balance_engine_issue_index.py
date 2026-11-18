"""Reducer diagnostics retain exact list behavior without repeated whole scans."""
import asyncio
from copy import deepcopy
import dis
import inspect
import json
import sys

import pytest

import operational_issue_index as diagnostic_index
from operational_balance_engine import reconcile


START = "2026-11-01T00:00:00+00:00"
NOW = "2026-11-18T00:00:00+00:00"
MISSING_CREATED = "'created_at'"


def state():
    return {"owner_id": "synthetic-owner", "status": "active", "started_at": START}


def legacy_append(out, entry):
    # The pre-change reducer's diagnostic contract, independent of the index.
    if entry not in out["issues"]:
        out["issues"].append(entry)


def test_duplicate_rich_diagnostics_keep_exact_order_and_full_equality(monkeypatch):
    sources = {"issues": [
        {"code": MISSING_CREATED, "source_id": "same"},
        {"code": MISSING_CREATED, "source_id": "same"},
        {"code": MISSING_CREATED, "source_id": "rich", "detail": {"parts": [1, True]}},
    ], "orders": [{"id": "same"}, {"id": "rich"}, {"id": "new"}, {"id": "new"}]}
    original = deepcopy(sources)
    with monkeypatch.context() as patch:
        patch.setattr(diagnostic_index, "append_unique_issue", legacy_append)
        before = reconcile(state(), sources, NOW)
    after = reconcile(state(), sources, NOW)
    assert after == before
    assert after["engine"]["issues"] == [*sources["issues"],
        {"code": MISSING_CREATED, "source_id": "rich"},
        {"code": MISSING_CREATED, "source_id": "new"}]
    assert sources == original
    assert type(after["engine"]["issues"]) is list
    assert diagnostic_index.INDEX_KEY not in json.dumps(after)


@pytest.mark.parametrize("seed", [
    ["unusual diagnostic"],
    [{"source_id": "same", "code": MISSING_CREATED}],
    [{"code": MISSING_CREATED, "source_id": ["same"]}],
    [{"code": MISSING_CREATED, "source_id": 1}],
])
def test_unusual_source_rows_preserve_ordinary_list_comparison(monkeypatch, seed):
    sources = {"issues": seed, "orders": [{"id": "same"}, {"id": "same"}]}
    with monkeypatch.context() as patch:
        patch.setattr(diagnostic_index, "append_unique_issue", legacy_append)
        expected = reconcile(state(), sources, NOW)
    assert reconcile(state(), sources, NOW) == expected


def test_custom_row_equality_keeps_the_original_error():
    class Unusual(dict):
        def __eq__(self, other):
            raise RuntimeError("original diagnostic comparison")
    sources = {"issues": [Unusual(code="old", source_id="old")], "orders": [{"id": "new"}]}
    with pytest.raises(RuntimeError, match="original diagnostic comparison"):
        reconcile(state(), sources, NOW)


def test_diagnostic_indexes_do_not_cross_reconciliation_attempts():
    original = state()
    sources = {"issues": [], "orders": [{"id": "one"}, {"id": "two"}]}
    first = reconcile(original, sources, NOW)
    first["engine"]["issues"][0]["source_id"] = "changed-only-in-first-result"
    second = reconcile(original, sources, NOW)
    assert second["engine"]["issues"] == [
        {"code": MISSING_CREATED, "source_id": "one"},
        {"code": MISSING_CREATED, "source_id": "two"}]
    assert original == state() and sources["issues"] == []
    assert diagnostic_index.INDEX_KEY not in json.dumps(second)


def test_reducer_index_construction_observes_request_cancellation(monkeypatch):
    original = state()
    sources = {"issues": [{"code": "source_gap", "source_id": str(i)} for i in range(1000)],
               "orders": [{"id": "new"}]}
    untouched = deepcopy(sources)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        if calls == 10:
            raise asyncio.CancelledError
    monkeypatch.setattr(diagnostic_index, "_checkpoint", cancelled)
    with pytest.raises(asyncio.CancelledError):
        reconcile(original, sources, NOW)
    assert calls == 10
    assert original == state() and sources == untouched


def test_many_reducer_diagnostics_have_a_linear_inspection_budget():
    count = 1000
    sources = {"issues": [{"code": "source_gap", "source_id": str(i), "status": "incomplete"}
                           for i in range(count)], "orders": [{"id": str(i)} for i in range(count)]}
    issue_code = next(code for code in reconcile.__code__.co_consts
                      if inspect.iscode(code) and code.co_name == "issue")
    containment_lines = {code: {instruction.positions.lineno for instruction in dis.get_instructions(code)
                               if instruction.opname == "CONTAINS_OP"}
                         for code in (issue_code, diagnostic_index.append_unique_issue.__code__)}
    key_code = diagnostic_index._key.__code__
    work = {"whole_list_rows": 0, "key_inspections": 0}
    def trace(frame, event, arg):
        if event == "call" and frame.f_code is key_code:
            work["key_inspections"] += 1
        if frame.f_code not in containment_lines:
            return None
        if event == "line" and frame.f_lineno in containment_lines[frame.f_code]:
            # Every candidate is absent in this ordinary-list cohort, so a
            # containment check must inspect its entire offered list. Count
            # that work without custom dict/list equality or a clock threshold.
            rows = frame.f_locals.get("issues", frame.f_locals.get("rows"))
            work["whole_list_rows"] += len(rows)
        return trace
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        actual = reconcile(state(), sources, NOW)
    finally:
        sys.settrace(previous)
    assert len(actual["engine"]["issues"]) == count * 2
    assert actual["engine"]["issues"] == sources["issues"] + [
        {"code": MISSING_CREATED, "source_id": str(i)} for i in range(count)]
    assert sum(work.values()) < 20 * count * 2, work
