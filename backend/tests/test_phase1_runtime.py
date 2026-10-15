import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import observability_metrics as registry
import observability_middleware as middleware
import runtime_diagnostics as runtime


def test_bounded_names_and_active_slots():
    m = registry.Metrics(True)
    for n in range(5000):
        m.observe('secret-customer-' + str(n), .1)
        m.begin_request()
    result = m.snapshot()
    assert result['histograms'] == {}
    assert result['api']['active'] == 2048
    assert result['api']['overflow'] == 2952
    assert 'secret' not in json.dumps(result)
    assert result['counters']['rejected_metric'] == 5000


def test_histogram_counts_and_percentiles_are_upper_bounds():
    m = registry.Metrics(True)
    for _ in range(100):
        m.observe('event_loop.lag', .12)
    row = m.snapshot()['histograms']['event_loop.lag']
    assert row['count'] == sum(row['buckets']) == 100
    assert row['p50'] == row['p95'] == row['p99'] == .25


def test_disabled_and_kill_file(monkeypatch, tmp_path):
    m = registry.Metrics(False)
    m.observe('event_loop.lag', 20)
    assert m.begin_request() is None
    assert not m.snapshot()['histograms']
    monkeypatch.setattr(registry, 'metrics', m)
    monkeypatch.setattr(registry, '_configured', True)
    monkeypatch.setattr(registry, '_next_control_check', 0.)
    control = tmp_path / 'switch'
    monkeypatch.setenv('OBS_CONTROL_FILE', str(control))
    registry.refresh_control()
    assert not m.enabled
    control.write_text('enabled')
    monkeypatch.setattr(registry, '_next_control_check', 0.)
    registry.refresh_control()
    assert m.enabled
    control.write_text('disabled')
    monkeypatch.setattr(registry, '_next_control_check', 0.)
    registry.refresh_control()
    assert not m.enabled


@pytest.mark.asyncio
async def test_real_loop_stall(monkeypatch):
    m = registry.Metrics(True)
    monkeypatch.setattr(runtime, 'metrics', m)
    monkeypatch.setattr(runtime, 'refresh_control', lambda: None)
    task = asyncio.create_task(runtime._lag_monitor())
    try:
        await asyncio.sleep(.05)
        time.sleep(1.15)
        await asyncio.sleep(.02)
        row = m.snapshot()['histograms']['event_loop.lag']
        assert row['sum'] >= .15
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_api_active_cleanup_privacy_and_exception(monkeypatch):
    m = registry.Metrics(True)
    monkeypatch.setattr(middleware, 'metrics', m)
    async def app(scope, receive, send):
        assert m.snapshot()['api']['active'] == 1
        scope['route'] = SimpleNamespace(path='/api/preparation-work-v1/assembly/pieces/{piece_id}/ready')
        await send({'type': 'http.response.start', 'status': 503})
        raise ValueError('secret invoice contents')
    async def send(message):
        pass
    with pytest.raises(ValueError):
        await middleware.DiagnosticsMiddleware(app)({'type':'http', 'method':'POST', 'path':'/secret'}, None, send)
    result = m.snapshot()
    assert result['api']['active'] == 0
    assert result['histograms']['api.duration.ready']['count'] == 1
    assert result['counters']['api.status.503'] == 1
    assert 'secret' not in json.dumps(result)


@pytest.mark.asyncio
async def test_disabled_middleware_is_passthrough(monkeypatch):
    m = registry.Metrics(False)
    monkeypatch.setattr(middleware, 'metrics', m)
    async def app(scope, receive, send):
        return 'unchanged'
    assert await middleware.DiagnosticsMiddleware(app)({'type':'http'}, None, None) == 'unchanged'
    assert m.snapshot()['api']['active'] == 0
    assert not m.snapshot()['histograms']


def test_restart_resets_identity_and_counters(monkeypatch):
    m = registry.Metrics(True)
    m.observe('event_loop.lag', 2.)
    previous = m.started_at
    monkeypatch.setattr(registry, 'metrics', m)
    registry._after_fork()
    assert m.started_at >= previous
    assert not m.snapshot()['histograms']


def test_server_installs_middleware():
    from pathlib import Path
    text = (Path(__file__).parents[1] / 'server.py').read_text(encoding='utf-8')
    assert 'app.add_middleware(DiagnosticsMiddleware)' in text


@pytest.mark.asyncio
async def test_health_not_ready_operation_and_background_not_api(monkeypatch):
    m = registry.Metrics(True)
    monkeypatch.setattr(middleware, 'metrics', m)
    async def app(scope, receive, send):
        scope['route'] = SimpleNamespace(path='/api/ready')
        await send({'type': 'http.response.start', 'status': 200})
        await send({'type': 'http.response.body', 'body': b'ok'})
        before = m.snapshot()
        assert before['api']['active'] == 0
        await asyncio.sleep(.04)
        assert m.snapshot()['histograms'] == before['histograms']
    async def send(message):
        pass
    await middleware.DiagnosticsMiddleware(app)({'type':'http', 'method':'GET'}, None, send)
    assert 'api.duration.ready' not in m.snapshot()['histograms']
    assert m.snapshot()['histograms']['api.duration.other']['count'] == 1
