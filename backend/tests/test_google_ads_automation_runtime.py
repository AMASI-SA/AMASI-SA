"""Default-off runtime contracts; no subprocess, Mongo or provider is started."""
from __future__ import annotations

import asyncio
import logging
import sys
from types import SimpleNamespace

import pytest

import google_ads_automation_scheduler as scheduler
import google_ads_automation_runner as runner


@pytest.fixture(autouse=True)
def reset_process_state(monkeypatch):
    monkeypatch.setattr(scheduler, "_active_child", None)
    monkeypatch.setattr(scheduler, "_launch_busy", False)
    # Older launcher revisions have no pending-spawn reservation.
    monkeypatch.setattr(scheduler, "_pending_spawn", None, raising=False)


@pytest.mark.asyncio
async def test_disabled_runtime_never_spawns_or_bootstraps_database(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", raising=False)
    async def forbidden(*args, **kwargs):
        raise AssertionError("disabled runtime must do no work")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(runner, "_load_runtime", forbidden)
    assert await scheduler.run_worker_once() == 0
    assert await runner.run_once() == {"status": "disabled", "accounts": 0}


@pytest.mark.parametrize("value", ["1", "true", "YES", "on", "enabled"])
def test_explicit_worker_flag_enables_scheduler_without_execution_flag(monkeypatch, value):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", value)
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", "false")
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", raising=False)
    assert scheduler.scheduler_enabled() is True
    assert runner.worker_enabled() is True


def test_child_marker_prevents_nested_scheduler(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", "1")
    assert scheduler.scheduler_enabled() is False
    assert runner.worker_enabled() is True


@pytest.mark.asyncio
async def test_fixed_isolated_child_argv_and_suppressed_output(monkeypatch, caplog):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", raising=False)
    monkeypatch.setenv("EXAMPLE_PRIVATE_VALUE", "private-value-not-for-output")
    captured = {}
    process = SimpleNamespace(returncode=0)
    async def wait(): return 0
    process.wait = wait
    async def spawn(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    caplog.set_level(logging.INFO)
    assert await scheduler.run_worker_once() == 0
    assert captured["args"] == (sys.executable, "-B", str(scheduler.WORKER_PATH))
    assert captured["kwargs"]["cwd"] == str(scheduler.ROOT_DIR)
    assert captured["kwargs"]["stdout"] == asyncio.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] == asyncio.subprocess.DEVNULL
    assert captured["kwargs"]["env"]["GOOGLE_ADS_AUTOMATION_CHILD_PROCESS"] == "1"
    assert captured["kwargs"]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "private-value-not-for-output" not in caplog.text


class HangingProcess:
    def __init__(self, *, ignore_terminate=False):
        self.returncode = None
        self.terminated = self.killed = False
        self.ignore_terminate = ignore_terminate
    async def wait(self):
        if self.killed or (self.terminated and not self.ignore_terminate):
            self.returncode = -9 if self.killed else -15
            return self.returncode
        await asyncio.Future()
    def terminate(self): self.terminated = True
    def kill(self): self.killed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("ignore_terminate", [False, True])
async def test_timeout_stops_only_owned_child_then_escalates_when_needed(monkeypatch, ignore_terminate):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", raising=False)
    monkeypatch.setattr(scheduler, "STOP_GRACE_SECONDS", 0.01)
    process = HangingProcess(ignore_terminate=ignore_terminate)
    async def spawn(*args, **kwargs): return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await scheduler.run_worker_once(timeout_seconds=0.01) == 124
    assert process.terminated is True
    assert process.killed is ignore_terminate


@pytest.mark.asyncio
async def test_parent_cancellation_stops_child_and_preserves_cancellation(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", raising=False)
    created = asyncio.Event()
    process = HangingProcess()
    async def spawn(*args, **kwargs):
        created.set()
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(scheduler.run_worker_once())
    await created.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert process.terminated is True


@pytest.mark.asyncio
async def test_concurrent_launcher_calls_cannot_create_two_children(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", raising=False)
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []
    process = SimpleNamespace(returncode=None)
    async def wait():
        await finish.wait()
        process.returncode = 0
        return 0
    process.wait = wait
    async def spawn(*args, **kwargs):
        calls.append(args); entered.set(); return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    first = asyncio.create_task(scheduler.run_worker_once())
    await entered.wait()
    assert await scheduler.run_worker_once() == 75
    finish.set()
    assert await first == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unconfirmed_child_exit_never_admits_another_launch(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", raising=False)
    monkeypatch.setattr(scheduler, "STOP_GRACE_SECONDS", 0.001)
    class UnconfirmedProcess(HangingProcess):
        async def wait(self): await asyncio.Future()
    process = UnconfirmedProcess(ignore_terminate=True)
    calls = []
    async def spawn(*args, **kwargs): calls.append(args); return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await scheduler.run_worker_once(timeout_seconds=0.001) == 124
    assert process.killed is True
    assert await scheduler.run_worker_once() == 75
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_lost_spawn_ack_blocks_second_child_until_late_handle_cleanup(monkeypatch, cancel):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", raising=False)
    monkeypatch.setattr(scheduler, "STOP_GRACE_SECONDS", 0.005)
    spawned, acknowledge, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    class LateProcess(HangingProcess):
        def terminate(self):
            super().terminate()
            stopped.set()
    first_process = LateProcess()
    completed = SimpleNamespace(returncode=0)
    async def completed_wait(): return 0
    completed.wait = completed_wait
    async def spawn(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            # The OS child exists, but the subprocess API has not acknowledged
            # its handle. Cancelling this await cannot prove that child absent.
            spawned.set()
            await acknowledge.wait()
            return first_process
        return completed
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    first = asyncio.create_task(scheduler.run_worker_once(timeout_seconds=0.01 if not cancel else 1))
    await spawned.wait()
    try:
        if cancel:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(first, timeout=0.2)
        else:
            assert await asyncio.wait_for(first, timeout=0.2) == 124
        assert await scheduler.run_worker_once(timeout_seconds=0.01) == 75
        assert len(calls) == 1
        acknowledge.set()
        await asyncio.wait_for(stopped.wait(), timeout=0.2)
        for _ in range(8):
            await asyncio.sleep(0)
        assert first_process.returncode == -15
        assert await scheduler.run_worker_once(timeout_seconds=0.05) == 0
        assert len(calls) == 2
    finally:
        acknowledge.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_failed_spawn_without_handle_retains_unknown_ownership(monkeypatch, caplog):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS", raising=False)
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("private-unacknowledged-spawn-body")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await scheduler.run_worker_once(timeout_seconds=0.01) == 1
    assert await scheduler.run_worker_once(timeout_seconds=0.01) == 75
    assert len(calls) == 1
    assert "private-unacknowledged-spawn-body" not in caplog.text


@pytest.mark.asyncio
async def test_enabled_runner_missing_database_config_fails_without_creating_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    def forbidden(*args, **kwargs): raise AssertionError("no configured database")
    monkeypatch.setattr(runner, "_load_runtime", lambda: (forbidden, forbidden))
    with pytest.raises(RuntimeError, match="google_automation_database_config_missing"):
        await runner.run_once()


@pytest.mark.asyncio
async def test_default_off_startup_registers_but_starts_no_task(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", raising=False)
    callbacks = {}
    class Router:
        def on_event(self, kind):
            def attach(fn): callbacks[kind] = fn; return fn
            return attach
    def forbidden(*args, **kwargs): raise AssertionError("disabled startup")
    scheduler.attach_google_ads_automation_scheduler(Router())
    monkeypatch.setattr(asyncio, "create_task", forbidden)
    await callbacks["startup"]()
    await callbacks["shutdown"]()


@pytest.mark.asyncio
async def test_scheduler_serializes_children_and_stops_after_operator_disables(monkeypatch):
    enabled = True
    calls = []
    monkeypatch.setattr(scheduler, "scheduler_enabled", lambda: enabled)
    async def work(): calls.append("child")
    async def sleep(seconds):
        nonlocal enabled
        calls.append(("sleep", seconds))
        if seconds == scheduler.POLL_INTERVAL_SECONDS:
            enabled = False
    monkeypatch.setattr(scheduler, "run_worker_once", work)
    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    await scheduler.scheduler_loop()
    assert calls == [("sleep", scheduler.INITIAL_DELAY_SECONDS), "child", ("sleep", 300)]


@pytest.mark.asyncio
async def test_router_idempotence_readiness_gate_and_shutdown(monkeypatch):
    callbacks = {"startup": [], "shutdown": []}
    class Router:
        def on_event(self, kind):
            def attach(fn): callbacks[kind].append(fn); return fn
            return attach
    ready, started, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def wait_ready(): await ready.wait()
    async def loop():
        started.set()
        try: await asyncio.Future()
        finally: stopped.set()
    monkeypatch.setattr(scheduler, "scheduler_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "scheduler_loop", loop)
    import boot_runtime
    monkeypatch.setattr(boot_runtime, "wait_for_local_readiness", wait_ready)
    router = Router()
    scheduler.attach_google_ads_automation_scheduler(router)
    scheduler.attach_google_ads_automation_scheduler(router)
    assert {key: len(value) for key, value in callbacks.items()} == {"startup": 1, "shutdown": 1}
    await callbacks["startup"][0]()
    await callbacks["startup"][0]()
    await asyncio.sleep(0)
    assert not started.is_set()
    ready.set()
    await started.wait()
    await callbacks["shutdown"][0]()
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_runner_uses_existing_database_bootstrap_and_closes_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("MONGO_URL", "mongodb://test-only")
    monkeypatch.setenv("DB_NAME", "test-only-db")
    calls = []
    class Client:
        def __init__(self, uri, **kwargs): calls.append(("client", uri, kwargs))
        def __getitem__(self, name): calls.append(("db", name)); return "injected-db"
        def close(self): calls.append("close")
    async def cycle(db):
        assert db == "injected-db"
        return {"status": "recovery_only", "accounts": 2, "results": [{"raw": "must-not-print"}]}
    monkeypatch.setattr(runner, "_load_runtime", lambda: (Client, cycle))
    result = await runner.run_once()
    assert result["status"] == "recovery_only" and calls[-1] == "close"
    assert calls[0][0:2] == ("client", "mongodb://test-only")
    assert calls[1] == ("db", "test-only-db")


@pytest.mark.asyncio
async def test_runner_timeout_cancels_cycle_and_closes_database(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    monkeypatch.setenv("MONGO_URL", "mongodb://test-only")
    monkeypatch.setenv("DB_NAME", "test-only-db")
    monkeypatch.setattr(runner, "CYCLE_TIMEOUT_SECONDS", 0.01)
    events = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        def __getitem__(self, name): return object()
        def close(self): events.append("close")
    async def cycle(db):
        try: await asyncio.Future()
        finally: events.append("cancelled")
    monkeypatch.setattr(runner, "_load_runtime", lambda: (Client, cycle))
    with pytest.raises(asyncio.TimeoutError): await runner.run_once()
    assert events == ["cancelled", "close"]


def test_runner_main_never_prints_provider_or_raw_results(monkeypatch, capsys, caplog):
    async def fake():
        return {"status": "recovery_only", "accounts": 2, "results": [{"secret": "private-model-body"}]}
    monkeypatch.setattr(runner, "run_once", fake)
    assert runner.main() == 0
    output = capsys.readouterr()
    assert output.out.strip() == '{"status":"recovery_only","accounts":2}'
    assert "private-model-body" not in output.out + output.err + caplog.text


def test_runner_failure_logs_constant_code_only(monkeypatch, capsys, caplog):
    async def fake(): raise RuntimeError("private-provider-body")
    monkeypatch.setattr(runner, "run_once", fake)
    assert runner.main() == 1
    output = capsys.readouterr()
    assert "private-provider-body" not in output.out + output.err + caplog.text
    assert "google_automation_child_failed" in caplog.text
