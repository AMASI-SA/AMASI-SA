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


@pytest.mark.asyncio
@pytest.mark.parametrize("method,template,label", [
    ("GET", "/api/order-reviews-v1/pages", "pending_reviews"),
    ("GET", "/reviewed-products-v1/catalog", "reviewed_products"),
    ("GET", "/api/order-reviews-v1/reviewed", "reviewed_orders"),
    ("GET", "/api/preparation-work-v1/assembly/orders", "assembly_orders"),
    ("GET", "/api/fulfillment-v2/ready-to-ship", "ready_to_ship"),
    ("POST", "/api/operational-notifications/devices/register", "push_registration"),
    ("GET", "/api/supplier-dispatch-v1/workspace", "supplier_workspace"),
    ("POST", "/supplier-dispatch-v1/dispatches", "supplier_dispatch_create"),
    ("GET", "/api/operational-notifications/devices/register", None),
    ("GET", "/api/order-reviews-v1/{order_number}", None),
    ("GET", None, None),
])
@pytest.mark.parametrize("enabled,status", [(True, 200), (True, 403), (True, 504), (False, 200)])
async def test_employee_route_metrics_use_only_declared_template_and_method(
    method, template, label, enabled, status, monkeypatch,
):
    m = registry.Metrics(enabled)
    monkeypatch.setattr(middleware, "metrics", m)

    async def app(scope, receive, send):
        if template:
            scope["route"] = SimpleNamespace(path=template)
        await send({"type": "http.response.start", "status": status})
        await send({"type": "http.response.body", "body": b"sensitive"})

    async def send(_):
        pass

    scope = {"type": "http", "method": method, "path": "/api/order-reviews-v1/pages",
             "query_string": b"sensitive", "headers": [(b"authorization", b"sensitive")]}
    await middleware.DiagnosticsMiddleware(app)(scope, None, send)
    snapshot = m.snapshot()
    hist, counts = snapshot["histograms"], snapshot["counters"]
    assert "sensitive" not in json.dumps(snapshot)
    if not enabled:
        assert not hist and not counts
        return
    global_status = {200: "2xx", 403: "4xx", 504: "504"}[status]
    legacy_group = "supplier_invoice" if label in ("supplier_workspace", "supplier_dispatch_create") else "other"
    expected_hist = {"api.duration." + legacy_group}
    expected_counts = {"api.status." + global_status: 1}
    if label:
        expected_hist.add("employee_route.duration." + label)
        expected_counts[f"employee_route.status.{label}.{403 if status == 403 else global_status}"] = 1
    assert set(hist) == expected_hist
    assert all(row["count"] == 1 for row in hist.values())
    assert counts == expected_counts


@pytest.mark.asyncio
async def test_real_fastapi_permission_denial_counts_both_route_groups(monkeypatch):
    from fastapi import APIRouter, Depends, FastAPI, HTTPException
    import httpx

    recorder = registry.Metrics(True)
    monkeypatch.setattr(middleware, "metrics", recorder)
    app = FastAPI()
    app.add_middleware(middleware.DiagnosticsMiddleware)
    router = APIRouter(prefix="/supplier-dispatch-v1")

    async def deny():
        raise HTTPException(status_code=403, detail="synthetic-private-detail")

    @router.post("/dispatches", dependencies=[Depends(deny)])
    async def create_dispatch():
        pytest.fail("the permission dependency must stop execution")

    app.include_router(router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/supplier-dispatch-v1/dispatches?probe=synthetic-private",
                                     json={"id": "synthetic-private-id"})
    assert response.status_code == 403
    snapshot = recorder.snapshot()
    assert "synthetic-private" not in json.dumps(snapshot)
    assert set(snapshot["histograms"]) == {"employee_route.duration.supplier_dispatch_create", "api.duration.supplier_invoice"}
    assert snapshot["counters"] == {"employee_route.status.supplier_dispatch_create.403": 1, "api.status.4xx": 1}
