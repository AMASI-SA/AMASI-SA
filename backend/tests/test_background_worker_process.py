import asyncio
from pathlib import Path
import os
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from background_worker_process import ManagedWorker
from test_qoyod_real_mongo import real_db


@pytest.mark.asyncio
async def test_cpu_busy_child_does_not_block_health_and_stops_on_parent_pipe_close(tmp_path):
    marker = tmp_path / 'started'
    stopped = tmp_path / 'stopped'
    script = tmp_path / 'busy.py'
    script.write_text(
        'import sys, threading, pathlib, time\n'
        f'pathlib.Path({str(marker)!r}).write_text("ready")\n'
        'stop = threading.Event()\n'
        'def parent():\n sys.stdin.buffer.read(); stop.set()\n'
        'threading.Thread(target=parent, daemon=True).start()\n'
        'while not stop.is_set():\n sum(range(30000))\n'
        f'pathlib.Path({str(stopped)!r}).write_text("stopped")\n', encoding='utf-8')
    worker = ManagedWorker(worker_path=script, restart_delay=.05, shutdown_timeout=5)
    worker.start()
    app = FastAPI()
    @app.get('/health')
    async def health():
        return {'ok': True}
    try:
        async with asyncio.timeout(8):
            while not marker.exists():
                await asyncio.sleep(.01)
        assert worker.status()['running'] is True
        assert worker.process.pid != os.getpid()
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            async with asyncio.timeout(2):
                replies = await asyncio.gather(*[client.get('/health') for _ in range(40)])
        assert all(r.status_code == 200 for r in replies)
    finally:
        await worker.stop()
    assert stopped.read_text() == 'stopped'
    assert worker.status()['running'] is False
    assert worker.process.returncode == 0


@pytest.mark.asyncio
async def test_child_failure_restarts_without_running_work_inside_parent(tmp_path):
    marker = tmp_path / 'attempts'
    script = tmp_path / 'failure.py'
    script.write_text(
        'import pathlib\n'
        f'p=pathlib.Path({str(marker)!r})\n'
        'p.write_text(p.read_text()+"x" if p.exists() else "x")\n'
        'raise SystemExit(7)\n', encoding='utf-8')
    worker = ManagedWorker(worker_path=script, restart_delay=.05, shutdown_timeout=2)
    worker.start()
    try:
        async with asyncio.timeout(8):
            while not marker.exists() or len(marker.read_text()) < 2:
                await asyncio.sleep(.02)
        assert worker.status()['restarts'] >= 1
    finally:
        await worker.stop()
    attempts = marker.read_text()
    await asyncio.sleep(.1)
    assert marker.read_text() == attempts


@pytest.mark.asyncio
async def test_web_ads_startup_cannot_launch_provider_work(monkeypatch):
    from integrations_control_center import ads_auto_sync_scheduler as ads
    from boot_runtime import process_local_readiness_event
    from fastapi import APIRouter
    monkeypatch.delenv('MEZAN_BACKGROUND_WORKER_PROCESS', raising=False)
    monkeypatch.setattr(ads, 'auto_sync_enabled', lambda: True)
    async def forbidden(*args):
        pytest.fail('provider loop started inside the web process')
    monkeypatch.setattr(ads, 'auto_sync_loop', forbidden)
    router = APIRouter()
    ads.attach_ads_auto_sync_scheduler(router, object(), lambda: None, lambda x: x)
    process_local_readiness_event.set()
    try:
        for callback in router.on_startup:
            await callback()
        await asyncio.sleep(.01)
        for callback in reversed(router.on_shutdown):
            await callback()
    finally:
        process_local_readiness_event.clear()


@pytest.mark.asyncio
async def test_canonical_runtime_boots_against_empty_isolated_database_and_drains(real_db, monkeypatch):
    db, _ = real_db
    uri = os.environ['QOYOD_TEST_MONGO_URI']
    assert uri.startswith('mongodb://127.0.0.1:')
    monkeypatch.setenv('MONGO_URL', uri)
    monkeypatch.setenv('DB_NAME', db.name)
    monkeypatch.setenv('JWT_SECRET', 'isolated-background-worker-test-secret')
    monkeypatch.setenv('EMAIL_OTP_SMTP_HOST', 'smtp.example.invalid')
    monkeypatch.setenv('EMAIL_OTP_FROM_EMAIL', 'test@example.invalid')
    monkeypatch.delenv('EMAIL_OTP_SMTP_USERNAME', raising=False)
    monkeypatch.delenv('EMAIL_OTP_SMTP_PASSWORD', raising=False)
    worker = ManagedWorker(restart_delay=2, shutdown_timeout=15)
    worker.start()
    try:
        async with asyncio.timeout(60):
            while True:
                row = await db.background_worker_status.find_one({'_id': worker.parent_id})
                if row:
                    break
                assert worker.restarts == 0, 'canonical child exited before publishing readiness'
                await asyncio.sleep(.1)
        assert row['jobs']['qoyod-plan-b-auto-send-worker'] is True
        assert row['jobs']['qoyod-followup'] is True
        assert row['jobs']['qoyod-full-scan'] is True
        assert row['jobs']['operational-balances'] is True
        assert await db.qoyod_manual_auto_runs.count_documents({}) == 0
        assert await db.qoyod_auto_cycles.count_documents({}) == 0
    finally:
        await worker.stop()
    assert worker.process.returncode == 0
