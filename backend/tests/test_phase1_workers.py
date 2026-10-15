"""Spawned-worker contract rehearsal, never imports business/server startup.

ASGI requests and governor waits are real; Mongo events here are driver callback
fixtures. Real Mongo pool/commit fault injection lives in test_phase1_real_mongo.
No inference about Production worker discovery or overhead follows from this test.
"""
import asyncio
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace


def _collector_module():
    path = Path(__file__).resolve().parents[2] / "scripts/observability_phase1_collector.py"
    spec = importlib.util.spec_from_file_location("worker_collector_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker(pipe, enabled, stall, collector_failed=None):
    # Child imports after setting configuration: exercise actual module singleton.
    os.environ.pop("OBS_CONTROL_FILE", None)
    os.environ.pop("OBS_METRICS_ENABLED", None)
    if enabled:
        os.environ["OBS_METRICS_ENABLED"] = "true"
    import observability_metrics as registry
    import observability_middleware as middleware
    import runtime_diagnostics as runtime
    import mongo_observability as mongo
    import resource_governor as resources

    async def scenario():
        if collector_failed is not None:
            assert await asyncio.to_thread(collector_failed.wait, 5)
        before = registry.metrics.snapshot()
        assert before["enabled"] is enabled
        assert not before["histograms"]
        resources.memory_snapshot = lambda: resources.MemorySnapshot(
            None, None, None, {}, None, None, None, None)
        monitor = runtime.start_lag_monitor()
        responses = []
        active_seen = []

        async def app(scope, receive, send):
            active_seen.append(registry.metrics.snapshot()["api"]["active"])
            scope["route"] = SimpleNamespace(path="/api/preparation-work-v1/assembly/pieces/{piece_id}/ready")
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"synthetic-ready"})

        async def send(message):
            responses.append(message)

        measured = middleware.DiagnosticsMiddleware(app)
        for _ in range(100):
            await measured({"type": "http", "method": "POST"}, None, send)
        listener = mongo.MongoMetrics()
        listener.succeeded(SimpleNamespace(command_name="find", duration_micros=2000))
        listener.succeeded(SimpleNamespace(command_name="commitTransaction", duration_micros=250000))
        listener.connection_check_out_started(None)
        await asyncio.sleep(.01)
        listener.connection_checked_out(None)
        listener.connection_checked_in(None)
        governor = resources.ResourceGovernor()
        holder, _ = await governor.acquire("dashboard", task_name="synthetic")
        waiting = asyncio.create_task(governor.acquire("ads", task_name="synthetic"))
        await asyncio.sleep(.03)
        assert not waiting.done()
        await governor.release(holder)
        token, _ = await asyncio.wait_for(waiting, 1)
        await governor.release(token)
        # Put the stall across the real 1-second monitor deadline.
        await asyncio.sleep(.8)
        if stall:
            time.sleep(.6)
        else:
            await asyncio.sleep(.6)
        await asyncio.sleep(.03)
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass
        after = runtime.diagnostics()
        assert len(responses) == 200
        assert all(response.get("status", 200) == 200 for response in responses)
        assert all(value == int(enabled) for value in active_seen)
        pipe.send({"before": before, "after": after, "responses": 100})

    try:
        asyncio.run(scenario())
    finally:
        pipe.close()


def _collector_failure(pipe, output, failed_event):
    collector = _collector_module()
    # A bound but non-listening local port guarantees connection refusal without
    # contacting an external service or racing against another port allocation.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{reserved.getsockname()[1]}"
        result = collector.fetch(origin, "/api/health/diagnostics", "fixture-token")
    assert result["outcome"] == "unavailable"
    # A full/unwritable collector store is simulated with a file as parent.
    obstruction = Path(output) / "not-a-directory"
    obstruction.write_text("fixture")
    try:
        collector.BoundedStore(obstruction / "store")
    except OSError:
        storage_failed = True
    else:
        storage_failed = False
    failed_event.set()
    pipe.send({"fetch": result, "storage_failed": storage_failed})
    pipe.close()


def _spawn(context, target, *args):
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(send, *args))
    process.start()
    send.close()
    return process, receive


def _result(pair):
    process, pipe = pair
    try:
        assert pipe.poll(10), "child did not report within bounded timeout"
        result = pipe.recv()
        process.join(2)
        assert process.exitcode == 0
        return result
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        pipe.close()


def test_spawned_worker_isolation_restart_and_collector_failure(tmp_path):
    context = multiprocessing.get_context("spawn")
    collector_failed = context.Event()
    a = _spawn(context, _worker, True, True, collector_failed)
    b = _spawn(context, _worker, True, False, collector_failed)
    failure = _spawn(context, _collector_failure, str(tmp_path), collector_failed)
    first, second, failed = _result(a), _result(b), _result(failure)
    assert failed["storage_failed"]
    assert "fixture-token" not in json.dumps(failed)
    first_metrics, second_metrics = (row["after"]["phase1"] for row in (first, second))
    assert first_metrics["worker"]["pid"] != second_metrics["worker"]["pid"]
    for row in (first_metrics, second_metrics):
        hist = row["histograms"]
        assert hist["api.duration.ready"]["count"] == 100
        assert hist["api.duration.ready"]["p99"] is not None
        assert hist["mongo.command.find.ok"]["count"] == 1
        assert hist["mongo.command.commitTransaction.ok"]["sum"] == .25
        assert hist["mongo.pool.wait.ok"]["sum"] >= .005
        assert hist["governor.wait.global.ads"]["sum"] >= .02
        assert hist["governor.hold.dashboard"]["sum"] >= .02
        assert row["api"]["active"] == 0
        assert row["worker"]["cpu_seconds"] > 0
    assert first_metrics["histograms"]["event_loop.lag"]["sum"] > .2
    assert (first_metrics["histograms"]["event_loop.lag"]["sum"] >
            second_metrics["histograms"]["event_loop.lag"]["sum"] + .15)
    restarted = _result(_spawn(context, _worker, True, False))
    boots = _collector_module().WorkerBoots()
    boots.annotate("worker-1", {"metrics": first_metrics})
    changed = boots.annotate("worker-1", {"metrics": restarted["after"]["phase1"]})
    assert changed["observed_restarts"] == 1
    assert restarted["before"]["histograms"] == {}
    assert restarted["after"]["phase1"]["histograms"]["api.duration.ready"]["count"] == 100


def test_spawned_default_disabled_worker_still_serves_business():
    result = _result(_spawn(multiprocessing.get_context("spawn"), _worker, False, False))
    metrics = result["after"]["phase1"]
    assert result["responses"] == 100
    assert metrics["enabled"] is False
    assert metrics["histograms"] == metrics["counters"] == {}
    assert metrics["api"]["active"] == 0
