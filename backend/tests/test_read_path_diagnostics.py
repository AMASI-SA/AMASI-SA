"""Public observation and unchanged scheduling contracts for read diagnostics."""
import asyncio
from contextvars import copy_context
import json
from types import SimpleNamespace

import pytest

import read_path_diagnostics as diagnostic


class Clock:
    wall = 100.0
    cpu = 10.0

    def monotonic(self):
        return self.wall

    def thread_time(self):
        return self.cpu


@pytest.fixture(autouse=True)
def isolated_sampler(monkeypatch):
    clock = Clock()
    diagnostic._last.clear()
    diagnostic._active.clear()
    token = diagnostic._current.set(None)
    monkeypatch.setattr(diagnostic, "time", clock)
    yield clock
    diagnostic._current.reset(token)
    diagnostic._last.clear()
    diagnostic._active.clear()


@pytest.fixture
def records(monkeypatch):
    values = []
    monkeypatch.setattr(diagnostic.log, "info", lambda template, text: values.append(json.loads(text)))
    return values


def test_sample_caps_and_skipped_nested_context_do_not_mix(records, isolated_sampler):
    clock = isolated_sampler
    with diagnostic.capture("operational_context", mode="home") as outer:
        diagnostic.count("resources", 4)
        clock.wall += 65
        with diagnostic.capture("operational_context") as skipped:
            assert skipped is None
            assert diagnostic.current() is None
            diagnostic.count("resources", 999)
        assert diagnostic.current() is outer
    assert records[0]["counts"] == {"resources": 4}
    with diagnostic.capture("operational_context") as fresh:
        assert fresh is not None
    with diagnostic.capture("operational_context") as rate_limited:
        assert rate_limited is None
    assert len(records) == 2
    assert diagnostic.current() is None
    assert not diagnostic._active


def test_wall_cpu_and_worker_queue_are_distinct(records, isolated_sampler):
    clock = isolated_sampler
    def worker(value):
        clock.wall += .040
        clock.cpu += .015
        return value
    with diagnostic.capture("operational_context"):
        wrapped = diagnostic.thread_call("report", worker)
        clock.wall += .200
        assert wrapped("same result") == "same result"
        with diagnostic.phase("state_read"):
            clock.wall += .300
            clock.cpu += 5
    phases = records[0]["phases"]
    assert phases["report"] == {
        "calls": 1, "sum_ms": 40.0, "max_ms": 40.0,
        "sum_thread_cpu_ms": 15.0, "queue_sum_ms": 200.0, "queue_max_ms": 200.0,
    }
    assert phases["state_read"] == {"calls": 1, "sum_ms": 300.0, "max_ms": 300.0}


def test_cpu_is_not_collected_on_async_phase_even_if_requested(records, isolated_sampler):
    with diagnostic.capture("operational_context"):
        with diagnostic.phase("access_initial", cpu=True):
            isolated_sampler.wall += .1
            isolated_sampler.cpu += 9
    assert "sum_thread_cpu_ms" not in records[0]["phases"]["access_initial"]


def test_closed_record_rejects_copied_context_and_delayed_worker_updates(records, isolated_sampler):
    with diagnostic.capture("operational_context"):
        copied = copy_context()
        worker = diagnostic.thread_call("report", lambda: 37)
        diagnostic.count("resources", 1)
    before = json.dumps(records, sort_keys=True)
    assert copied.run(diagnostic.current) is None
    copied.run(diagnostic.count, "resources", 999)
    assert worker() == 37
    assert json.dumps(records, sort_keys=True) == before


@pytest.mark.parametrize("bad", ["customer-secret-order", {"secret": "payload"}, ["payload"]])
def test_unapproved_labels_and_modes_are_omitted_without_changing_result(records, bad):
    with diagnostic.capture("operational_context", mode=bad):
        diagnostic.set_mode(bad)
        diagnostic.count(bad, 7)
        with diagnostic.phase(bad):
            pass
        assert diagnostic.thread_call(bad, int)("7") == 7
    encoded = json.dumps(records)
    assert "customer-secret" not in encoded and "payload" not in encoded
    assert records[0]["mode"] is None
    assert records[0]["phases"] == {} and records[0]["counts"] == {}


def test_invalid_scope_is_noop_and_does_not_emit_payload(records):
    with diagnostic.capture({"tenant": "secret"}) as record:
        assert record is None
    assert records == []


def test_original_exception_survives_and_its_message_is_never_logged(records):
    original = ValueError("customer-private-value")
    with pytest.raises(ValueError) as caught:
        with diagnostic.capture("assembly_board"):
            with diagnostic.phase("dto_map", cpu=True):
                raise original
    assert caught.value is original
    assert records[0]["outcome"] == "failed"
    assert "customer-private" not in json.dumps(records)
    assert not diagnostic._active


def test_log_failure_preserves_business_result_and_exception(monkeypatch):
    def failed_log(*_):
        raise OSError("unavailable log sink")
    monkeypatch.setattr(diagnostic.log, "info", failed_log)
    with diagnostic.capture("operational_context"):
        value = 91
    assert value == 91 and diagnostic.current() is None
    original = RuntimeError("business failure")
    with pytest.raises(RuntimeError) as caught:
        with diagnostic.capture("assembly_board"):
            raise original
    assert caught.value is original and not diagnostic._active


def test_clock_failure_does_not_replace_business_result(records, monkeypatch):
    def broken():
        raise OSError("clock unavailable")
    monkeypatch.setattr(diagnostic, "time", SimpleNamespace(monotonic=broken, thread_time=broken))
    with diagnostic.capture("operational_context") as record:
        assert record is None
        with diagnostic.phase("state_read"):
            value = 7
    assert value == 7 and records == []


def test_unsampled_wrappers_are_original_objects(records):
    function = lambda: 2
    original = object()
    assert diagnostic.thread_call("report", function) is function
    assert diagnostic.iterator("hydration_cursor", original) is original
    assert records == []


@pytest.mark.asyncio
async def test_concurrent_scopes_keep_counts_and_cancellation_separate(records):
    entered = asyncio.Event()
    blocked = asyncio.Event()
    async def first():
        with diagnostic.capture("operational_context", mode="directory"):
            diagnostic.count("resources", 3)
            entered.set()
            await blocked.wait()
    task = asyncio.create_task(first())
    await entered.wait()
    with diagnostic.capture("assembly_board", mode="completed"):
        diagnostic.count("mapped_rows", 9)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    by_scope = {record["scope"]: record for record in records}
    assert by_scope["assembly_board"]["counts"] == {"mapped_rows": 9}
    assert by_scope["operational_context"]["counts"] == {"resources": 3}
    assert by_scope["operational_context"]["outcome"] == "cancelled"
    assert not diagnostic._active and diagnostic.current() is None


@pytest.mark.asyncio
async def test_iterator_preserves_duplicates_and_advancement_without_closing(records):
    class Cursor:
        advances = 0
        iterations = 0
        closes = 0
        def __init__(self):
            self.rows = iter([{"id": 1}, {"id": 1}, {"id": 2}])
        def __aiter__(self):
            self.iterations += 1
            return self
        async def __anext__(self):
            self.advances += 1
            try:
                return next(self.rows)
            except StopIteration:
                raise StopAsyncIteration
        async def close(self):
            self.closes += 1
    cursor = Cursor()
    with diagnostic.capture("assembly_board"):
        rows = [row async for row in diagnostic.iterator("hydration_cursor", cursor)]
    assert rows == [{"id": 1}, {"id": 1}, {"id": 2}]
    assert (cursor.iterations, cursor.advances, cursor.closes) == (1, 4, 0)
    assert records[0]["phases"]["hydration_cursor"]["calls"] == 4


@pytest.mark.asyncio
async def test_iterator_propagates_pending_cancellation_without_an_extra_advance(records):
    started = asyncio.Event()
    class Cursor:
        advances = 0
        def __aiter__(self):
            return self
        async def __anext__(self):
            self.advances += 1
            started.set()
            await asyncio.Event().wait()
    cursor = Cursor()
    async def consume():
        with diagnostic.capture("assembly_board"):
            return [row async for row in diagnostic.iterator("hydration_cursor", cursor)]
    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cursor.advances == 1
    assert records[0]["outcome"] == "cancelled"
