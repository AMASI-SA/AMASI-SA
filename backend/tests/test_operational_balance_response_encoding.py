"""Saved response encoding is owned, serialized, and off the HTTP event loop."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event, Lock
import time

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import pytest


@pytest.mark.asyncio
async def test_encoded_response_is_byte_identical_for_saved_financial_types():
    from operational_response_encoding import encode_saved_response
    value = {'label': 'كشف الحساب', 'amount': Decimal('12.50'),
             'at': datetime(2026, 11, 24, tzinfo=timezone.utc),
             'items': [{'id': str(i), 'value': i, 'none': None} for i in range(100)]}
    expected = JSONResponse(content=jsonable_encoder(value), headers={'Cache-Control': 'no-store'})
    actual = await encode_saved_response(value)
    assert actual.body == expected.body
    assert actual.status_code == expected.status_code and actual.raw_headers == expected.raw_headers


@pytest.mark.asyncio
async def test_encoding_yields_http_loop_and_cancels_queued_work_without_start(monkeypatch):
    import operational_response_encoding as encoding
    entered, release = Event(), Event()
    original, calls, active, peak = encoding.jsonable_encoder, [], 0, 0
    counter = Lock()

    def blocked(value):
        nonlocal active, peak
        with counter:
            calls.append(value['id'])
            active += 1
            peak = max(peak, active)
        entered.set()
        try:
            assert release.wait(3)
            return original(value)
        finally:
            with counter:
                active -= 1

    monkeypatch.setattr(encoding, 'jsonable_encoder', blocked)
    first = asyncio.create_task(encoding.encode_saved_response({'id': 'first'}))
    queued = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # A response encoder is blocked in its worker, yet this independent
        # coroutine can execute immediately on the HTTP loop.
        async def health():
            await asyncio.sleep(0)
            return 'responsive'
        assert await asyncio.wait_for(health(), .5) == 'responsive'
        queued = asyncio.create_task(encoding.encode_saved_response({'id': 'cancelled'}))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert calls == ['first'] and peak == 1
        release.set()
        assert (await first).body == b'{"id":"first"}'
    finally:
        release.set()
        await asyncio.gather(*[task for task in (first, queued) if task], return_exceptions=True)


@pytest.mark.asyncio
async def test_running_cancellation_joins_before_releasing_serialization_gate(monkeypatch):
    import operational_response_encoding as encoding
    entered, release = Event(), Event()
    original, calls = encoding.jsonable_encoder, []
    def blocked(value):
        calls.append(value['id'])
        if value['id'] == 'first':
            entered.set()
            assert release.wait(3)
        return original(value)
    monkeypatch.setattr(encoding, 'jsonable_encoder', blocked)
    first = asyncio.create_task(encoding.encode_saved_response({'id': 'first'}))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        second = asyncio.create_task(encoding.encode_saved_response({'id': 'second'}))
        for _ in range(4):
            await asyncio.sleep(0)
        assert not first.done() and calls == ['first']
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second).body == b'{"id":"second"}'
        assert calls == ['first', 'second']
    finally:
        release.set()
        await asyncio.gather(*[task for task in (first, second) if task], return_exceptions=True)


@pytest.mark.asyncio
async def test_multiple_event_loops_share_one_process_encoder(monkeypatch):
    import operational_response_encoding as encoding
    original, active, peak, counter = encoding.jsonable_encoder, 0, 0, Lock()
    def observed(value):
        nonlocal active, peak
        with counter:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(.02)
            return original(value)
        finally:
            with counter:
                active -= 1
    monkeypatch.setattr(encoding, 'jsonable_encoder', observed)
    def separate_loop(value):
        return asyncio.run(encoding.encode_saved_response(value)).body
    values = await asyncio.gather(*[asyncio.to_thread(separate_loop, {'id': i}) for i in range(4)])
    assert values == [b'{"id":0}', b'{"id":1}', b'{"id":2}', b'{"id":3}']
    assert peak == 1 and active == 0


def ready_route(monkeypatch, *, authorize):
    import demand_read_store
    import operational_workspace_demand_routes as routes
    from test_operational_balance_demand_refresh import Request
    request = Request()
    request.headers = {}
    async def make_spec(*args, **kwargs):
        return {'owner_id': 'owner', 'actor_id': 'owner', 'source_revision': {'revision': 7}}
    async def read_ready(*args, **kwargs):
        return {'total': 0, 'items': None, 'header': {'private': 'authorized-owner-only'},
                'snapshot': 'saved-generation', 'generated_at': '2026-11-24T12:00:00+00:00'}
    monkeypatch.setattr(routes, 'make_spec', make_spec)
    monkeypatch.setattr(routes, 'authorize_spec', authorize)
    monkeypatch.setattr(demand_read_store, 'projection_key', lambda spec: 'synthetic-key')
    monkeypatch.setattr(demand_read_store, 'read_ready', read_ready)
    return request, lambda: routes.serve_projection(object(), {}, request, 'operational_report')


@pytest.mark.asyncio
async def test_permissions_revoked_during_encoding_cannot_return_saved_bytes(monkeypatch):
    import operational_response_encoding as encoding
    entered, release = Event(), Event()
    original, revoked, checks = encoding.jsonable_encoder, False, []
    async def authorize(*args):
        checks.append('checked')
        if revoked:
            raise HTTPException(403, {'code': 'revoked'})
    def blocked(value):
        entered.set()
        assert release.wait(3)
        return original(value)
    monkeypatch.setattr(encoding, 'jsonable_encoder', blocked)
    _, run = ready_route(monkeypatch, authorize=authorize)
    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        revoked = True
        release.set()
        with pytest.raises(HTTPException) as failure:
            await task
        assert failure.value.status_code == 403 and len(checks) == 2
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_page_disconnect_during_encoding_joins_and_never_returns_private_response(monkeypatch):
    import operational_response_encoding as encoding
    entered, release = Event(), Event()
    original = encoding.jsonable_encoder
    async def authorize(*args):
        pass
    def blocked(value):
        entered.set()
        assert release.wait(3)
        return original(value)
    monkeypatch.setattr(encoding, 'jsonable_encoder', blocked)
    request, run = ready_route(monkeypatch, authorize=authorize)
    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        request.disconnect()
        for _ in range(4):
            await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(HTTPException) as failure:
            await task
        assert failure.value.status_code == 499
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
