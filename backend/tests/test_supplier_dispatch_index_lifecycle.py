import asyncio
from unittest.mock import AsyncMock

import pytest

import preparation_supplier_dispatch as dispatch


def workspace(db):
    router = dispatch.make_preparation_supplier_dispatch_router(db, lambda: {})
    return next(route.endpoint for route in router.routes if route.path.endswith('/workspace'))


@pytest.fixture
def reads(monkeypatch):
    worker = {'id': 'employee', 'created_by': 'merchant'}
    permission = AsyncMock(return_value=worker)
    load = AsyncMock(return_value={'files': [], 'summary': {}})
    monkeypatch.setattr(dispatch, '_require_preparation_worker', permission)
    monkeypatch.setattr(dispatch, '_employee_workspace', load)
    return permission, load


@pytest.mark.asyncio
async def test_repeated_workspace_reads_skip_ddl_but_refresh_authority_and_data(monkeypatch, reads):
    setup = AsyncMock()
    monkeypatch.setattr(dispatch, 'ensure_supplier_dispatch_indexes', setup)
    endpoint = workspace(object())
    for _ in range(3):
        assert (await endpoint(limit=10, grain='piece', user={}))['ok']
    assert setup.await_count == 1
    assert reads[0].await_count == reads[1].await_count == 3


@pytest.mark.asyncio
async def test_concurrent_initial_reads_wait_for_one_successful_setup(monkeypatch, reads):
    started, release = asyncio.Event(), asyncio.Event()
    async def initialize(db):
        started.set()
        await release.wait()
    setup = AsyncMock(side_effect=initialize)
    monkeypatch.setattr(dispatch, 'ensure_supplier_dispatch_indexes', setup)
    endpoint = workspace(object())
    tasks = [asyncio.create_task(endpoint(limit=10, grain='piece', user={})) for _ in range(3)]
    try:
        await started.wait()
        await asyncio.sleep(0)
        assert reads[1].await_count == 0
        assert setup.await_count == 1
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert reads[1].await_count == 3


@pytest.mark.asyncio
async def test_failed_setup_is_retried_before_any_workspace_read(monkeypatch, reads):
    setup = AsyncMock(side_effect=[RuntimeError('index creation failed'), None])
    monkeypatch.setattr(dispatch, 'ensure_supplier_dispatch_indexes', setup)
    endpoint = workspace(object())
    with pytest.raises(RuntimeError, match='index creation failed'):
        await endpoint(limit=10, grain='piece', user={})
    assert reads[1].await_count == 0
    await endpoint(limit=10, grain='piece', user={})
    await endpoint(limit=10, grain='piece', user={})
    assert setup.await_count == 2


@pytest.mark.asyncio
async def test_different_router_databases_initialize_independently(monkeypatch, reads):
    setup = AsyncMock()
    monkeypatch.setattr(dispatch, 'ensure_supplier_dispatch_indexes', setup)
    first_db, second_db = object(), object()
    for endpoint in (workspace(first_db), workspace(second_db)):
        await endpoint(limit=10, grain='piece', user={})
    assert [call.args[0] for call in setup.await_args_list] == [first_db, second_db]


@pytest.mark.asyncio
async def test_cancelled_initialization_is_not_treated_as_ready(monkeypatch, reads):
    started = asyncio.Event()
    async def initialize(db):
        started.set()
        await asyncio.Event().wait()
    setup = AsyncMock(side_effect=initialize)
    monkeypatch.setattr(dispatch, 'ensure_supplier_dispatch_indexes', setup)
    endpoint = workspace(object())
    first = asyncio.create_task(endpoint(limit=10, grain='piece', user={}))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    setup.side_effect = None
    await endpoint(limit=10, grain='piece', user={})
    assert setup.await_count == 2
    assert reads[1].await_count == 1
