import logging

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
import pytest

import observability_middleware as middleware
from observability_metrics import Metrics


@pytest.mark.asyncio
async def test_request_timings_are_available_with_optional_metrics_off_and_never_log_identifiers(caplog, monkeypatch):
    monkeypatch.setattr(middleware, 'metrics', Metrics(False))
    app = FastAPI()
    app.add_middleware(middleware.DiagnosticsMiddleware)
    @app.get('/api/orders/{order_id}')
    async def order(order_id: str):
        return {'ok': True}
    with caplog.at_level(logging.INFO, logger='mezan.api_timing'):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            result = await client.get('/api/orders/private-order-812?token=private-secret',
                headers={'Authorization': 'Bearer private-bearer'})
    assert result.status_code == 200
    logs = [r.getMessage() for r in caplog.records if r.name == 'mezan.api_timing']
    assert len(logs) == 1
    assert 'route=/api/orders/{order_id}' in logs[0]
    assert 'status=200' in logs[0] and 'duration_ms=' in logs[0]
    assert all(secret not in logs[0] for secret in ('private-order', 'private-secret', 'private-bearer'))
    assert middleware.metrics.snapshot()['histograms'] == {}


@pytest.mark.asyncio
async def test_streaming_response_logs_once_after_final_body(caplog):
    from starlette.responses import StreamingResponse
    async def app(scope, receive, send):
        async def stream():
            yield b'part one'
            yield b'part two'
        await StreamingResponse(stream())(scope, receive, send)
    wrapped = middleware.DiagnosticsMiddleware(app)
    with caplog.at_level(logging.INFO, logger='mezan.api_timing'):
        async with AsyncClient(transport=ASGITransport(app=wrapped), base_url='http://test') as client:
            assert (await client.get('/private-path')).content == b'part onepart two'
    logs = [r.getMessage() for r in caplog.records if r.name == 'mezan.api_timing']
    assert len(logs) == 1 and 'route=unmatched' in logs[0]
    assert 'private-path' not in logs[0]
