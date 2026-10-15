import asyncio
import contextlib
import json
import logging
import os
import time

import pytest
import latency_evidence as evidence


@pytest.fixture(autouse=True)
def clean(monkeypatch, caplog):
    monkeypatch.setattr(evidence, "_enabled", False)
    monkeypatch.setattr(evidence, "_configured", False)
    monkeypatch.setattr(evidence, "_permit", "")
    monkeypatch.setattr(evidence, "_last", {})
    monkeypatch.setattr(evidence, "_active", {})
    monkeypatch.setattr(evidence, "_identity", {"source_git_sha": "test", "release_id": "synthetic"})
    caplog.set_level(logging.INFO, logger="mezan.latency_evidence")


def records(caplog):
    return [json.loads(r.message.split("latency_evidence ", 1)[1])
            for r in caplog.records if r.name == "mezan.latency_evidence"]


def test_disabled_is_silent(caplog):
    evidence.refresh()
    with evidence.capture("dashboard"), evidence.phase("computation_wall"):
        evidence.lag_observed(5000)
    assert records(caplog) == []


@pytest.mark.skipif(os.name == "nt", reason="Linux owner/mode contract")
def test_permit_lifecycle_and_unsafe_files(monkeypatch, tmp_path):
    path = tmp_path / "permit"
    monkeypatch.setattr(evidence, "_configured", True)
    monkeypatch.setattr(evidence, "_permit", str(path))
    evidence.refresh()
    assert not evidence._enabled
    path.touch(mode=0o600)
    evidence.refresh()
    assert evidence._enabled
    path.chmod(0o644)
    evidence.refresh()
    assert not evidence._enabled
    path.chmod(0o600)
    os.utime(path, (time.time() - 901, time.time() - 901))
    evidence.refresh()
    assert not evidence._enabled
    path.unlink()
    os.mkfifo(path, 0o600)
    evidence.refresh()
    assert not evidence._enabled
    path.unlink()
    target = tmp_path / "target"
    target.touch(mode=0o600)
    path.symlink_to(target)
    evidence.refresh()
    assert not evidence._enabled
    path.unlink()
    target.rename(path)
    evidence.refresh()
    assert evidence._enabled
    path.unlink()
    evidence.refresh()
    assert not evidence._enabled


def test_threshold_rate_privacy_and_bounds(monkeypatch, caplog):
    monkeypatch.setattr(evidence, "_enabled", True)
    evidence.lag_observed(249)
    for _ in range(100):
        with evidence.capture("dashboard"):
            with evidence.phase("mongo_cursor_await"):
                pass
            with evidence.phase("customer-secret-token"):
                pass
            evidence.lag_observed(350)
    rows = records(caplog)
    assert len(rows) == 2
    assert rows[0]["lag_ms"] == 350
    assert rows[0]["active"][0]["trace"] == rows[1]["trace"]
    assert rows[1]["phases"]["mongo_cursor_await"]["calls"] == 1
    assert "customer-secret-token" not in caplog.text
    assert evidence._active == {}


def test_cancel_and_exception_semantics(monkeypatch, caplog):
    monkeypatch.setattr(evidence, "_enabled", True)
    events = []

    @contextlib.asynccontextmanager
    async def manager():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    async def run():
        with evidence.capture("snapchat"):
            async with evidence.admission(manager()):
                raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert events == ["enter", "exit"]
    assert records(caplog)[0]["status"] == "failed_or_cancelled"
    assert evidence._current.get() is None


def test_parallel_phase_counts_and_unchanged_result(monkeypatch, caplog):
    monkeypatch.setattr(evidence, "_enabled", True)

    @evidence.timed("provider_http_await")
    async def provider(value):
        await asyncio.sleep(.005)
        return value

    async def run():
        with evidence.capture("snapchat"):
            return await asyncio.gather(*(provider(n) for n in range(10)))
    assert asyncio.run(run()) == list(range(10))
    phase = records(caplog)[0]["phases"]["provider_http_await"]
    assert phase["calls"] == 10
    assert phase["sum_ms"] >= 50


def test_actual_existing_monitor_detects_stall(monkeypatch, caplog):
    import runtime_diagnostics as runtime
    monkeypatch.setattr(evidence, "refresh", lambda: None)
    monkeypatch.setattr(evidence, "_enabled", True)

    async def run():
        task = asyncio.create_task(runtime._lag_monitor())
        await asyncio.sleep(.9)
        time.sleep(.45)  # intentional isolated loop stall, never Production
        await asyncio.sleep(.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert any(r.get("lag_ms", 0) >= 250 for r in records(caplog))


def test_disable_during_sample_suppresses_completion(monkeypatch, caplog):
    monkeypatch.setattr(evidence, "_enabled", True)
    with evidence.capture("dashboard"), evidence.phase("computation_wall"):
        evidence._enabled = False
    assert records(caplog) == []
    assert evidence._active == {}


def test_logging_failure_does_not_change_result(monkeypatch):
    monkeypatch.setattr(evidence, "_enabled", True)
    monkeypatch.setattr(evidence.log, "info", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    with evidence.capture("dashboard"):
        evidence.lag_observed(500)


def test_actual_snapchat_transport_unchanged_and_private(monkeypatch, caplog):
    import httpx
    from integrations_control_center.snapchat_native_data_common import SnapchatSyncContext
    monkeypatch.setattr(evidence, "_enabled", True)
    calls = []

    async def transport(request):
        calls.append(request.method)
        return httpx.Response(200, json={"customer": "private-value"})

    async def run():
        context = SnapchatSyncContext(None, "private-tenant")
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            with evidence.capture("snapchat"):
                response = await context._provider_get(
                    client, "https://synthetic.invalid/private-account",
                    headers={"Authorization": "Bearer private-token"}, params=None)
                return response.json(), context.provider_calls
    assert asyncio.run(run()) == ({"customer": "private-value"}, 1)
    assert calls == ["GET"]
    assert records(caplog)[0]["phases"]["provider_http_await"]["calls"] == 1
    assert "private" not in caplog.text
