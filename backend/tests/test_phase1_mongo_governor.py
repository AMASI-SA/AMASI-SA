"""Phase1 listener and admission tests; no Mongo server or business writes."""
import asyncio
from types import SimpleNamespace

import pytest

import mongo_observability as mongo
import resource_governor as resources


class Recorder:
    enabled = True

    def __init__(self):
        self.samples = []

    def observe(self, name, seconds):
        if self.enabled:
            self.samples.append((name, seconds))


@pytest.fixture
def recorder(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(mongo, "metrics", recorder)
    monkeypatch.setattr(resources, "metrics", recorder)
    monkeypatch.setattr(resources, "memory_snapshot", lambda: resources.MemorySnapshot(
        None, None, None, {}, None, None, None, None))
    return recorder


def test_command_commit_latency_and_error_are_separate_without_payload(recorder):
    listener = mongo.MongoMetrics()
    listener.succeeded(SimpleNamespace(command_name="commitTransaction", duration_micros=2_400_000))
    listener.failed(SimpleNamespace(command_name="commitTransaction", duration_micros=3_000_000,
                                   failure="timeout: secret-token customer-invoice"))
    listener.succeeded(SimpleNamespace(command_name="insert", duration_micros=5_000))
    listener.succeeded(SimpleNamespace(command_name="unbounded-customer-id", duration_micros=1_000))
    assert recorder.samples == [
        ("mongo.command.commitTransaction.ok", 2.4),
        ("mongo.command.commitTransaction.error", 3.0),
        ("mongo.command.write.ok", .005),
        ("mongo.command.other.ok", .001),
    ]
    assert "secret" not in repr(recorder.samples)
    assert listener.snapshot()["operation_timeouts"] == 1


def test_pool_wait_success_and_timeout_callback_measurements(recorder, monkeypatch):
    listener = mongo.MongoMetrics()
    ticks = iter([10., 10.5, 20., 22.])
    monkeypatch.setattr(mongo.time, "monotonic", lambda: next(ticks))
    listener.connection_check_out_started(None)
    listener.connection_checked_out(None)
    listener.connection_checked_in(None)
    listener.connection_check_out_started(None)
    listener.connection_check_out_failed(SimpleNamespace(reason="timeout"))
    assert recorder.samples == [("mongo.pool.wait.ok", .5), ("mongo.pool.wait.error", 2.)]
    assert listener.snapshot()["checkout_timeouts"] == 1
    assert listener.snapshot()["checked_out_connections"] == 0


def test_unmatched_checkout_timers_are_bounded(recorder, monkeypatch):
    listener = mongo.MongoMetrics()
    for identity in range(2048):
        monkeypatch.setattr(mongo.threading, "get_ident", lambda: identity)
        listener.connection_check_out_started(None)
    assert len(listener._checkout_started) == 1024


@pytest.mark.asyncio
async def test_held_global_capacity_records_wait_and_hold(recorder):
    governor = resources.ResourceGovernor()
    holder, _ = await governor.acquire("dashboard", task_name="private-name")
    waiter = asyncio.create_task(governor.acquire("ads", task_name="private-other"))
    await asyncio.sleep(.025)
    assert not waiter.done()
    assert governor._global.pending == 1
    await governor.release(holder)
    token, _ = await asyncio.wait_for(waiter, .5)
    await governor.release(token)
    assert any(name == "governor.wait.global.ads" and duration >= .02
               for name, duration in recorder.samples)
    assert any(name == "governor.hold.dashboard" and duration >= .02
               for name, duration in recorder.samples)
    assert governor._global.in_use == 0
    assert "private" not in repr(recorder.samples)


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_kind,wait_stage", [("snapchat", "kind"), ("dashboard", "global")])
async def test_cancelled_wait_is_recorded_without_leaking_capacity(recorder, waiting_kind, wait_stage):
    governor = resources.ResourceGovernor()
    holder, _ = await governor.acquire("snapchat", task_name="test")
    waiter = asyncio.create_task(governor.acquire(waiting_kind, task_name="test"))
    await asyncio.sleep(.025)
    assert not waiter.done()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert any(name == f"governor.wait.{wait_stage}.{waiting_kind}" and duration >= .02
               for name, duration in recorder.samples)
    await governor.release(holder)
    assert governor._global.in_use == 0
    token, _ = await asyncio.wait_for(governor.acquire(waiting_kind, task_name="test"), .5)
    await governor.release(token)


@pytest.mark.asyncio
async def test_disabled_adds_no_governor_timer_and_no_command_observation(recorder, monkeypatch):
    recorder.enabled = False
    # asyncio itself reads time.monotonic; patch the module reference only.
    monkeypatch.setattr(resources, "time", SimpleNamespace(monotonic=lambda: pytest.fail("timer read")))
    governor = resources.ResourceGovernor()
    token, _ = await governor.acquire("dashboard", task_name="test")
    assert token.hold_started is None
    await governor.release(token)
    mongo.MongoMetrics().succeeded(SimpleNamespace(command_name="find", duration_micros=10))
    assert recorder.samples == []


@pytest.mark.asyncio
async def test_disabling_during_hold_preserves_release(recorder):
    governor = resources.ResourceGovernor()
    token, _ = await governor.acquire("unexpected-kind", task_name="test")
    recorder.enabled = False
    await governor.release(token)
    assert governor._global.in_use == 0
    assert all("unexpected-kind" not in name for name, _ in recorder.samples)
