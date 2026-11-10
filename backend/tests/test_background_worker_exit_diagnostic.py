import asyncio
from unittest.mock import AsyncMock

import pytest

from background_worker_runner import record_worker_exit


@pytest.mark.asyncio
async def test_completed_job_records_cause_without_exception_message(monkeypatch):
    monkeypatch.setenv('MEZAN_BACKGROUND_PARENT_ID', 'parent')
    db = AsyncMock()

    async def fail():
        raise TimeoutError('SECRET provider payload')

    task = asyncio.create_task(fail(), name='qoyod-full-scan')
    await asyncio.gather(task, return_exceptions=True)
    await record_worker_exit(db, {task}, [task])
    args = db.background_worker_status.update_one.call_args.args
    assert args[0] == {'_id': 'parent'}
    payload = args[1][0]['$set']['last_exit']
    assert payload['jobs'] == [{'job': 'qoyod-full-scan', 'outcome': 'failed',
                                 'error_type': 'TimeoutError'}]
    assert 'SECRET' not in str(args)


@pytest.mark.asyncio
async def test_clean_job_return_is_distinct_from_parent_shutdown(monkeypatch):
    monkeypatch.setenv('MEZAN_BACKGROUND_PARENT_ID', 'parent')
    db = AsyncMock()
    task = asyncio.create_task(asyncio.sleep(0), name='finite-job')
    await task
    await record_worker_exit(db, {task}, [task])
    assert db.background_worker_status.update_one.call_args.args[1][0]['$set']['last_exit']['jobs'] == [
        {'job': 'finite-job', 'outcome': 'returned'}]
    db.reset_mock()
    await record_worker_exit(db, {task}, [])
    assert db.background_worker_status.update_one.call_args.args[1][0]['$set']['last_exit']['jobs'] == []


@pytest.mark.asyncio
async def test_monitor_failure_is_named_and_diagnostic_failure_does_not_escape(monkeypatch):
    monkeypatch.setenv('MEZAN_BACKGROUND_PARENT_ID', 'parent')
    db = AsyncMock()

    async def fail():
        raise ConnectionError('SECRET')

    task = asyncio.create_task(fail(), name='heartbeat')
    await asyncio.gather(task, return_exceptions=True)
    await record_worker_exit(db, {task}, [], [task])
    payload = db.background_worker_status.update_one.call_args.args[1][0]['$set']['last_exit']
    assert payload == {'jobs': [], 'monitors': [{'job': 'heartbeat', 'outcome': 'failed',
                                               'error_type': 'ConnectionError'}]}
    db.background_worker_status.update_one.side_effect = ConnectionError('SECRET')
    await record_worker_exit(db, {task}, [], [task])
