"""Linux-only, isolated control rehearsal. No server/business imports or network.

SIGSTOP proves an explicit limit: a frozen process cannot acknowledge internal
disable. The test kills only its own synthetic child, never a shared worker.
Timing output is raw evidence, not a Production latency guarantee.
"""
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

import observability_metrics as registry


def _write(path, value):
    temporary = path.with_name(path.name + ".next")
    temporary.write_text(value, encoding="ascii")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "control acknowledgement timed out"
        time.sleep(0.005)


@pytest.fixture
def control(tmp_path):
    assert sys.platform == "linux", "run this acceptance suite in isolated Linux"
    tmp_path.chmod(0o700)
    path = tmp_path / "control"
    _write(path, "disabled")
    return path


def _new(path, **options):
    target = registry.Metrics(False)
    watcher = registry.ControlFileWatcher(str(path), target, **options)
    return target, watcher


def _counts(target):
    snapshot = target.snapshot()
    return snapshot["histograms"], snapshot["counters"], snapshot["api"]["overflow"]


def test_default_off_and_explicit_dynamic_capability(control):
    code = """
import json
import observability_metrics as m
print(json.dumps({'enabled':m.metrics.enabled,'control':m._control is not None}))
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("OBS_CONTROL_FILE", None)
    env.pop("OBS_METRICS_ENABLED", None)
    def run():
        result = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                                text=True, capture_output=True, check=True, timeout=10)
        return json.loads(result.stdout)
    assert run() == {"enabled": False, "control": False}
    env["OBS_METRICS_ENABLED"] = "true"
    assert run() == {"enabled": True, "control": False}
    _write(control, "enabled")
    env["OBS_CONTROL_FILE"] = str(control)
    assert run() == {"enabled": False, "control": True}


def test_actual_watcher_activation_deactivation_latency(control):
    target, watcher = _new(control)
    watcher.start()
    try:
        assert not target.enabled
        start = time.monotonic()
        _write(control, "enabled")
        _until(lambda: target.enabled)
        activation = time.monotonic() - start
        target.increment("api.status.2xx")
        assert target.snapshot()["counters"] == {"api.status.2xx": 1}
        start = time.monotonic()
        _write(control, "disabled")
        _until(lambda: not target.enabled)
        deactivation = time.monotonic() - start
        target.increment("api.status.2xx")
        assert target.snapshot()["counters"] == {"api.status.2xx": 1}
        print(json.dumps({"case": "actual_control_latency", "activation_seconds": activation,
                          "deactivation_seconds": deactivation}))
    finally:
        watcher.stop()
    assert not target.enabled
    assert watcher.thread is None or not watcher.thread.is_alive()


@pytest.mark.parametrize("contents", ["", "true", "enabled extra", "x" * 100])
def test_invalid_content_fail_closed(control, contents):
    target, watcher = _new(control)
    target.set_enabled(True)
    _write(control, contents)
    watcher.poll()
    assert not target.enabled


def test_missing_file_and_relative_path_fail_closed(control):
    target, watcher = _new(control)
    target.set_enabled(True)
    control.unlink()
    watcher.poll()
    assert not target.enabled
    target, watcher = _new(Path("relative-control"))
    target.set_enabled(True)
    watcher.poll()
    assert not target.enabled


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o666, 0o700])
def test_unsafe_file_permissions_fail_closed(control, mode):
    _write(control, "enabled")
    control.chmod(mode)
    target, watcher = _new(control)
    watcher.poll()
    assert not target.enabled


def test_symlink_fifo_hardlink_are_rejected_without_blocking(control):
    other = control.with_name("real")
    _write(other, "enabled")
    control.unlink()
    control.symlink_to(other)
    target, watcher = _new(control)
    watcher.poll()
    assert not target.enabled
    control.unlink()
    os.mkfifo(control, 0o600)
    start = time.monotonic()
    watcher.poll()
    assert time.monotonic() - start < 0.5
    assert not target.enabled
    control.unlink()
    os.link(other, control)
    watcher.poll()
    assert not target.enabled


def test_unsafe_parent_and_symlink_ancestor_fail_closed(control):
    _write(control, "enabled")
    target, watcher = _new(control)
    control.parent.chmod(0o777)
    try:
        watcher.poll()
        assert not target.enabled
    finally:
        control.parent.chmod(0o700)
    alias = control.parent / "alias"
    actual = control.parent / "actual"
    actual.mkdir(mode=0o700)
    nested = actual / "control"
    _write(nested, "enabled")
    alias.symlink_to(actual, target_is_directory=True)
    target, watcher = _new(alias / "control")
    watcher.poll()
    assert not target.enabled


def test_callbacks_cannot_grow_after_atomic_disable_ack(control):
    target, watcher = _new(control)
    _write(control, "enabled")
    watcher.poll()
    assert target.enabled
    halt = threading.Event()
    def callbacks():
        while not halt.is_set():
            target.increment("api.status.2xx")
            target.observe("mongo.command.find.ok", 0.001)
            token = target.begin_request()
            target.end_request(token)
    threads = [threading.Thread(target=callbacks) for _ in range(4)]
    for thread in threads:
        thread.start()
    try:
        _until(lambda: bool(target.snapshot()["counters"]))
        _write(control, "disabled")
        watcher.poll()  # This return is the disable acknowledgement.
        assert not target.enabled
        before = _counts(target)
        time.sleep(0.1)
        assert _counts(target) == before
    finally:
        halt.set()
        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()
    assert target.snapshot()["api"]["active"] == 0


def test_real_event_loop_stall_does_not_block_file_disable(control):
    target, watcher = _new(control, poll_seconds=0.02)
    _write(control, "enabled")
    watcher.start()
    _until(lambda: target.enabled)
    result = {}
    blocked = threading.Event()
    def disable():
        assert blocked.wait(2)
        started = time.monotonic()
        _write(control, "disabled")
        _until(lambda: not target.enabled)
        result["disabled_at"] = time.monotonic()
        result["disable_seconds"] = result["disabled_at"] - started
    thread = threading.Thread(target=disable)
    thread.start()
    async def scenario():
        watcher.heartbeat()
        blocked.set()
        time.sleep(2.0)  # Deliberately blocks the actual event loop.
        result["loop_resumed_at"] = time.monotonic()
    try:
        asyncio.run(scenario())
        thread.join(3)
        assert not thread.is_alive()
        assert result["disabled_at"] < result["loop_resumed_at"]
        assert result["disable_seconds"] < 2.0
        print(json.dumps({"case": "event_loop_stall_explicit_disable", **result}))
    finally:
        watcher.stop()


def test_stall_auto_disable_latches_until_explicit_rearm(control):
    target, watcher = _new(control, poll_seconds=0.02, stall_seconds=0.2)
    _write(control, "enabled")
    watcher.start()
    try:
        _until(lambda: target.enabled)
        started = time.monotonic()
        _until(lambda: watcher.latched and not target.enabled)
        print(json.dumps({"case": "heartbeat_stall_latch", "seconds": time.monotonic() - started,
                          "test_stall_threshold_seconds": 0.2}))
        watcher.heartbeat()
        watcher.poll()
        assert watcher.latched and not target.enabled
        _write(control, "disabled")
        watcher.poll()
        assert not target.enabled
        watcher.heartbeat()
        _write(control, "enabled")
        watcher.poll()
        assert target.enabled and not watcher.latched
    finally:
        watcher.stop()


def test_default_five_second_stall_latches_during_real_blocked_loop(control):
    target, watcher = _new(control)  # Real defaults: poll 1s, heartbeat limit 5s.
    _write(control, "enabled")
    watcher.start()
    _until(lambda: target.enabled)
    result = {}
    block_entered = threading.Event()
    def witness():
        assert block_entered.wait(2)
        _until(lambda: watcher.latched and not target.enabled, timeout=7)
        result["disable_at"] = time.monotonic()
    observer = threading.Thread(target=witness)
    observer.start()
    async def scenario():
        watcher.heartbeat()
        result["heartbeat_at"] = time.monotonic()
        block_entered.set()
        time.sleep(6.5)
        result["loop_resumed_at"] = time.monotonic()
    try:
        asyncio.run(scenario())
        observer.join(2)
        assert not observer.is_alive()
        assert result["disable_at"] < result["loop_resumed_at"]
        elapsed = result["disable_at"] - result["heartbeat_at"]
        assert 4.9 <= elapsed < 6.5
        watcher.heartbeat()
        watcher.poll()
        assert watcher.latched and not target.enabled
        print(json.dumps({"case": "default_five_second_real_loop_stall",
                          "automatic_disable_seconds": elapsed,
                          "loop_stall_seconds": 6.5, "latched_after_resume": True}))
    finally:
        watcher.stop()


def _child_control(pipe, path):
    # Synthetic process: no network, Mongo, server startup, or customer data.
    import observability_metrics as module
    target = module.Metrics(False)
    watcher = module.ControlFileWatcher(path, target, poll_seconds=0.02)
    watcher.start()
    pipe.send({"pid": os.getpid(), "enabled": target.enabled})
    try:
        while True:
            watcher.heartbeat()
            if pipe.poll(0.02):
                command = pipe.recv()
                if command == "stop":
                    return
                pipe.send({"enabled": target.enabled, "pid": os.getpid()})
    finally:
        watcher.stop()
        pipe.close()


def _spawn(path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_child_control, args=(child, str(path)))
    process.start()
    child.close()
    assert parent.poll(10)
    initial = parent.recv()
    return process, parent, initial


def _finish(process, pipe):
    if process.is_alive():
        pipe.send("stop")
        process.join(3)
    if process.is_alive():
        process.kill()
        process.join(3)
    pipe.close()


def test_two_processes_control_only_selected_worker(control):
    second_path = control.with_name("second")
    _write(second_path, "disabled")
    first, first_pipe, first_initial = _spawn(control)
    second, second_pipe, second_initial = _spawn(second_path)
    try:
        assert first_initial["pid"] != second_initial["pid"]
        assert not first_initial["enabled"] and not second_initial["enabled"]
        _write(control, "enabled")
        def selected_enabled():
            first_pipe.send("status")
            assert first_pipe.poll(2)
            return first_pipe.recv()["enabled"]
        _until(selected_enabled)
        second_pipe.send("status")
        assert second_pipe.poll(2)
        assert not second_pipe.recv()["enabled"]
    finally:
        _finish(first, first_pipe)
        _finish(second, second_pipe)


def test_entire_process_freeze_has_no_internal_ack_and_external_stop(control):
    process, pipe, _ = _spawn(control)
    try:
        _write(control, "enabled")
        def active():
            pipe.send("status")
            assert pipe.poll(2)
            return pipe.recv()["enabled"]
        _until(active)
        os.kill(process.pid, signal.SIGSTOP)
        status = Path(f"/proc/{process.pid}/status")
        _until(lambda: any(line.startswith("State:") and "T" in line
                           for line in status.read_text().splitlines()))
        _write(control, "disabled")
        pipe.send("status")
        assert not pipe.poll(0.25), "frozen process must not acknowledge internal disable"
        assert process.is_alive()
        started = time.monotonic()
        process.kill()  # Never SIGCONT: kill only this isolated synthetic child.
        process.join(3)
        assert process.exitcode == -signal.SIGKILL
        print(json.dumps({"case": "entire_process_frozen", "internal_disable": "UNAVAILABLE",
                          "external_action": "SIGKILL_OWN_SYNTHETIC_CHILD",
                          "external_stop_seconds": time.monotonic() - started}))
    finally:
        if process.is_alive():
            process.kill()
            process.join(3)
        pipe.close()


def test_fork_child_is_disabled_until_explicit_child_start(control):
    _write(control, "enabled")
    code = r'''
import json, os, time
import observability_metrics as m
m.start_control()
deadline=time.monotonic()+2
while not m.metrics.enabled:
    assert time.monotonic()<deadline
    time.sleep(.005)
read_fd, write_fd=os.pipe()
pid=os.fork()
if pid==0:
    os.close(read_fd)
    before=m.metrics.enabled
    no_thread=m._control.thread is None or not m._control.thread.is_alive()
    m.start_control()
    deadline=time.monotonic()+2
    while not m.metrics.enabled:
        assert time.monotonic()<deadline
        time.sleep(.005)
    after=m.metrics.enabled
    m.stop_control()
    os.write(write_fd,json.dumps(dict(before=before,no_thread=no_thread,after=after)).encode())
    os.close(write_fd)
    os._exit(0)
os.close(write_fd)
result=json.loads(os.read(read_fd,4096))
os.close(read_fd)
_,status=os.waitpid(pid,0)
assert status==0
assert m.metrics.enabled
m.stop_control()
print(json.dumps(result))
'''
    env = dict(os.environ, OBS_CONTROL_FILE=str(control), OBS_METRICS_ENABLED="false",
               PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                            text=True, capture_output=True, timeout=10, check=True)
    assert json.loads(result.stdout) == {"before": False, "no_thread": True, "after": True}


def test_runtime_monitor_starts_and_stops_control_lifecycle(control):
    _write(control, "enabled")
    code = r'''
import asyncio, json
import observability_metrics as m
import runtime_diagnostics as runtime
async def run():
    assert not m.metrics.enabled
    monitor=runtime.start_lag_monitor()
    for _ in range(200):
        if m.metrics.enabled:
            break
        await asyncio.sleep(.01)
    assert m.metrics.enabled
    monitor.cancel()
    try:
        await monitor
    except asyncio.CancelledError:
        pass
    assert not m.metrics.enabled
    assert m._control.thread is None or not m._control.thread.is_alive()
    print(json.dumps({'case':'runtime_control_lifecycle','enabled_then_disabled':True}))
asyncio.run(run())
'''
    env = dict(os.environ, OBS_CONTROL_FILE=str(control), OBS_METRICS_ENABLED="false",
               PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                            text=True, capture_output=True, timeout=10, check=True)
    assert json.loads(result.stdout)["enabled_then_disabled"] is True
