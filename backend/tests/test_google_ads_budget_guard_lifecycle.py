"""Response lifetime cannot cancel or outlive tracked driver execution."""
import asyncio

from fastapi import HTTPException
import pytest

from integrations_control_center import google_ads_automation_budget_guard as guard
from tests.test_google_ads_automation_budget_guard import DB, supported, mutation


@pytest.mark.asyncio
async def test_commit_executor_future_remains_awaitable_after_response_timeout():
    db=DB(); capability=await supported(db)
    future=asyncio.get_running_loop().create_future();original=db.start_session
    async def start_session():
        session=await original()
        def commit():session.commit_calls+=1;return future
        session.commit_transaction=commit
        return session
    db.start_session=start_session
    try:
        with pytest.raises(HTTPException) as error:
            await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
                operation_id='pending_commit',capability=capability,planner=mutation,timeout_seconds=.02)
        assert error.value.detail['commit_attempted'] is True
        assert error.value.detail['preserve_execution_fence'] is True
        assert not future.cancelled(), 'Motor executor thread can continue after its Future is cancelled'
    finally:
        if not future.done():future.set_result(None)
        await asyncio.gather(*(state["task"] for state in list(guard._pending_operations.values())),return_exceptions=True)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cancelled_precommit_waiter_never_starts_commit_after_io_finishes():
    db=DB();capability=await supported(db);entered=asyncio.Event();release=asyncio.Event()
    original=guard._read_policy
    async def blocked(*args,**kwargs):
        entered.set();await release.wait();return await original(*args,**kwargs)
    from unittest.mock import patch
    with patch.object(guard,'_read_policy',blocked):
        waiter=asyncio.create_task(guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
            operation_id='cancel_precommit',capability=capability,planner=mutation))
        await entered.wait();waiter.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:await waiter
        assert caught.value.preserve_execution_fence and not caught.value.commit_attempted
        assert guard._pending_operations
        release.set()
        await asyncio.gather(*(state['task'] for state in list(guard._pending_operations.values())),return_exceptions=True)
    await asyncio.sleep(0)
    assert not db.rows and db.sessions[-1].commit_calls==0 and not guard._pending_operations


@pytest.mark.asyncio
async def test_pending_duplicate_and_resource_cap_do_not_cancel_work_and_read_has_own_capacity(monkeypatch):
    monkeypatch.setattr(guard,'MAX_PENDING_WRITE_TASKS',1)
    db=DB();capability=await supported(db);release=asyncio.Event()
    async def blocked():await release.wait()
    db.commit_gate=blocked
    with pytest.raises(HTTPException):
        await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
            operation_id='held',capability=capability,planner=mutation,timeout_seconds=.02)
    try:
        for operation in ['held','different']:
            with pytest.raises(HTTPException) as error:
                await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
                    operation_id=operation,capability=capability,planner=mutation)
            assert error.value.detail['code']=='google_budget_guard_operation_pending'
            assert error.value.detail['preserve_execution_fence'] is True
        proof=await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id='held')
        assert proof['storage_outcome']=='not_proven' and proof['preserve_execution_fence']
        assert proof['automatic_retry_allowed'] is False
    finally:
        release.set()
        await asyncio.gather(*(state['task'] for state in list(guard._pending_operations.values())),return_exceptions=True)
        await asyncio.sleep(0)
    proof=await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id='held')
    assert proof['storage_outcome']=='committed' and proof['provider_dispatch_authorized'] is False


@pytest.mark.asyncio
async def test_unknown_commit_reply_cannot_become_success_after_cleanup(monkeypatch):
    db=DB();capability=await supported(db);original=db.command
    async def command(name,*,session):
        if isinstance(name,dict) and 'commitTransaction' in name:
            await session.commit_transaction()
            return {'ok':1,'writeConcernError':{'code':64}}
        return await original(name,session=session)
    db.command=command
    with pytest.raises(HTTPException) as error:
        await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
            operation_id='write_concern_unknown',capability=capability,planner=mutation)
    assert error.value.detail['commit_attempted'] is True
    assert error.value.detail['preserve_execution_fence'] is True
    assert error.value.detail['automatic_retry_allowed'] is False
    assert db.sessions[-1].commit_calls==1 and db.rows[guard.BUDGET_GUARDS][0]['epoch']==1


@pytest.mark.asyncio
async def test_write_and_recovery_never_use_motor_async_context_sync_exit(monkeypatch):
    from tests.test_google_ads_automation_budget_guard import Session
    db=DB();capability=await supported(db)
    async def forbidden(*args):raise AssertionError('Motor asynccontext synchronous delegate forbidden')
    monkeypatch.setattr(Session,'__aenter__',forbidden);monkeypatch.setattr(Session,'__aexit__',forbidden)
    result=await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
        operation_id='context_free',capability=capability,planner=mutation)
    assert result['committed'] and not result['provider_dispatch_authorized']
    proof=await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id='context_free')
    assert proof['storage_outcome']=='committed' and proof['preserve_execution_fence']


@pytest.mark.asyncio
async def test_recovery_abort_future_survives_deadline_without_false_evidence(monkeypatch):
    monkeypatch.setattr(guard,'RECOVERY_TIMEOUT_SECONDS',.02)
    db=DB();await supported(db);future=asyncio.get_running_loop().create_future();original=db.command
    async def command(name,*,session):
        if isinstance(name,dict) and 'abortTransaction' in name:return await future
        return await original(name,session=session)
    db.command=command
    try:
        with pytest.raises(HTTPException) as error:
            await guard.read_google_budget_guard_commit(db,user_id='owner',account_id='123',operation_id='unknown')
        assert error.value.detail['code']=='google_budget_guard_recovery_unconfirmed'
        assert error.value.detail['preserve_execution_fence'] and not error.value.detail['automatic_retry_allowed']
        assert not future.cancelled() and guard._pending_operations
    finally:
        future.set_result({'ok':1})
        await asyncio.gather(*(state['task'] for state in list(guard._pending_operations.values())),return_exceptions=True)
        await asyncio.sleep(0)
    assert not guard._pending_operations


@pytest.mark.asyncio
async def test_worker_checks_original_deadline_before_commit_without_waiter_wakeup(monkeypatch):
    db=DB();capability=await supported(db);clock=[100.0]
    monkeypatch.setattr(guard,'_operation_clock',lambda:clock[0])
    def delayed_planner(snapshot):
        clock[0]+=20  # model work completing after deadline, not a global event-loop block
        return mutation(snapshot)
    with pytest.raises(HTTPException) as error:
        await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
            operation_id='deadline_before_waiter',capability=capability,planner=delayed_planner,timeout_seconds=1)
    assert error.value.detail['commit_attempted'] is False
    assert error.value.detail['preserve_execution_fence'] is True
    assert db.sessions[-1].commit_calls==0 and not db.rows


@pytest.mark.asyncio
async def test_post_wait_deadline_cannot_report_late_commit_as_confirmed_success(monkeypatch):
    db=DB();capability=await supported(db);clock=[100.0];original=guard._commit_once
    monkeypatch.setattr(guard,'_operation_clock',lambda:clock[0])
    async def delayed_ack(database,session):
        await original(database,session)
        clock[0]+=20  # ACK already applied but caller resumes after original deadline
    monkeypatch.setattr(guard,'_commit_once',delayed_ack)
    with pytest.raises(HTTPException) as error:
        await guard.run_google_budget_guard_transaction(db,user_id='owner',account_id='123',
            operation_id='late_confirmed_ack',capability=capability,planner=mutation,timeout_seconds=1)
    assert error.value.detail['commit_attempted'] is True
    assert error.value.detail['preserve_execution_fence'] is True
    assert error.value.detail['automatic_retry_allowed'] is False
    assert db.sessions[-1].commit_calls==1 and db.rows[guard.BUDGET_GUARDS][0]['epoch']==1
