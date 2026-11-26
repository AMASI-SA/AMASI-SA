"""Completed report copies must not keep acyclic inputs alive until cyclic GC."""
import asyncio
from copy import deepcopy
import gc
from threading import Event
import weakref

import pytest

import operational_report_copy as copying
from operational_financial_activity_runtime import preparation_scope


class Marker:
    def __init__(self):
        self.values = [42]


class Rejected:
    def __deepcopy__(self, memo):
        raise ValueError("synthetic copy rejected")


@pytest.fixture
def without_cyclic_gc():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        # Cleanup only after the observation, including the failing old-code
        # control. Reference counting must satisfy the lifetime assertion.
        gc.collect()
        if enabled:
            gc.enable()


def copy_attempt(monkeypatch, mode):
    marker = Marker()
    first = [marker]
    source = {"first": first, "alias": first}
    if mode == "error":
        source["invalid"] = Rejected()
    elif mode == "during":
        source["rows"] = [{"nested": [index]} for index in range(600)]
    calls, result, outcome = [], None, "returned"
    stop = Event()
    original_checkpoint = copying.preparation_checkpoint

    def checkpoint():
        calls.append(True)
        if mode in {"entry", "during", "exit"} and len(calls) == (1 if mode == "entry" else 2):
            stop.set()
        original_checkpoint()

    with monkeypatch.context() as patched, preparation_scope(stop):
        patched.setattr(copying, "preparation_checkpoint", checkpoint)
        try:
            result = copying.copy_report_value(source)
        except ValueError as error:
            assert mode == "error" and str(error) == "synthetic copy rejected"
            outcome = "ValueError"
        except asyncio.CancelledError:
            assert mode in {"entry", "during", "exit"}
            outcome = "CancelledError"
    assert marker.values == [42] and source["first"] is source["alias"] is first
    if result is not None:
        assert result["first"] is result["alias"] and result["first"] is not first
        assert result["first"][0] is not marker
    # This frame and any handled exception traceback are gone before the test
    # checks the weakref. Only the copied output intentionally stays alive.
    return weakref.ref(marker), result, outcome, len(calls)


def test_success_releases_original_while_independent_copy_remains(monkeypatch, without_cyclic_gc):
    original, result, outcome, checks = copy_attempt(monkeypatch, "success")
    assert outcome == "returned" and checks == 2
    assert result["first"][0].values == [42]
    assert original() is None
    result["first"][0].values.append(43)
    assert result["alias"][0].values == [42, 43]


def test_copy_error_releases_already_copied_original(monkeypatch, without_cyclic_gc):
    original, result, outcome, checks = copy_attempt(monkeypatch, "error")
    assert outcome == "ValueError" and checks == 1 and result is None
    assert original() is None


@pytest.mark.parametrize("boundary", ["entry", "during", "exit"])
def test_cancellation_releases_original_without_returning_partial_copy(
        monkeypatch, without_cyclic_gc, boundary):
    original, result, outcome, checks = copy_attempt(monkeypatch, boundary)
    assert outcome == "CancelledError" and result is None
    assert checks == (1 if boundary == "entry" else 2)
    assert original() is None


def copy_with_escaped_memo(copier):
    captured = []

    class Hook:
        def __deepcopy__(self, memo):
            captured.append(memo)
            return "copied-hook"

    marker = Marker()
    first = [marker]
    source = {"first": first, "alias": first, "hook": Hook()}
    result = copier(source)
    return weakref.ref(marker), result, captured, id(source), id(first)


@pytest.mark.parametrize("copier", [deepcopy, copying.copy_report_value], ids=["stdlib", "optimized"])
def test_escaped_hook_memo_stays_intact_until_its_owner_releases_it(without_cyclic_gc, copier):
    original, result, captured, source_id, first_id = copy_with_escaped_memo(copier)
    assert original() is not None  # The custom hook deliberately owns the memo.
    memo = captured.pop()
    assert memo[source_id] is result
    assert memo[first_id] is result["first"] is result["alias"]
    assert result["hook"] == "copied-hook"
    assert any(id(value) == source_id for value in memo[id(memo)])
    assert any(id(value) == first_id for value in memo[id(memo)])
    del memo
    assert original() is None
    assert result["first"][0].values == [42]
