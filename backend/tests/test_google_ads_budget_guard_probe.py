"""Capability proof must outlive HTTP waiters, never a swallowed abort error."""
import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from integrations_control_center import google_ads_automation_budget_guard as guard
from tests.test_google_ads_automation_budget_guard import DB


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', [None, {'ok':0}, {'ok':True}, {'ok':1,'writeConcernError':{}}, {'ok':1,'writeErrors':[]}])
async def test_bad_abort_reply_never_issues_capability_even_if_cleanup_succeeds(reply):
    db=DB(); original=db.command
    async def command(name,*,session):
        if isinstance(name,dict):return reply
        return await original(name,session=session)
    db.command=command
    with pytest.raises(HTTPException) as error:await guard.probe_google_budget_guard_transactions(db)
    assert error.value.detail['code']=='google_budget_guard_transactions_unavailable'
    assert db.sessions[0].aborted and not db.rows


@pytest.mark.asyncio
async def test_server_abort_failure_not_swallowed_by_successful_driver_cleanup():
    db=DB(); original=db.command
    async def command(name,*,session):
        if isinstance(name,dict):raise ConnectionError('private-host')
        return await original(name,session=session)
    db.command=command
    with pytest.raises(HTTPException) as error:await guard.probe_google_budget_guard_transactions(db)
    assert error.value.status_code==503 and 'private-host' not in str(error.value.detail)
    assert db.sessions[0].aborted and not db.rows


def delayed_db():
    db=DB(); release=asyncio.Event(); entered=asyncio.Event(); original=db.command
    async def command(name,*,session):
        if isinstance(name,dict):
            entered.set(); await release.wait()
        return await original(name,session=session)
    db.command=command
    return db,entered,release


@pytest.mark.asyncio
async def test_same_db_join_uses_one_session_and_cancelled_waiter_does_not_cancel_probe():
    db,entered,release=delayed_db()
    first=asyncio.create_task(guard.probe_google_budget_guard_transactions(db))
    await entered.wait()
    second=asyncio.create_task(guard.probe_google_budget_guard_transactions(db))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):await first
    assert len(db.sessions)==1 and not guard._pending_probe['task'].done()
    release.set(); capability=await second
    assert capability.supported and capability.database is db
    # Completed proof is never cached: a new call uses a fresh session.
    await guard.probe_google_budget_guard_transactions(db)
    assert len(db.sessions)==2


@pytest.mark.asyncio
async def test_original_deadline_cannot_be_renewed_and_expired_probe_never_issues_proof(monkeypatch):
    monkeypatch.setattr(guard,'PROBE_TIMEOUT_SECONDS',.06)
    db,entered,release=delayed_db()
    first=asyncio.create_task(guard.probe_google_budget_guard_transactions(db))
    await entered.wait(); state=guard._pending_probe; deadline=state['deadline']
    await asyncio.sleep(.035)
    second=asyncio.create_task(guard.probe_google_budget_guard_transactions(db))
    with pytest.raises(HTTPException):await first
    with pytest.raises(HTTPException):await second
    assert state['expired'] and state['deadline']==deadline and len(db.sessions)==1
    with pytest.raises(HTTPException) as busy:await guard.probe_google_budget_guard_transactions(db)
    assert busy.value.detail['code']=='google_budget_guard_probe_busy'
    assert len(db.sessions)==1
    release.set(); await state['task']; await asyncio.sleep(0)
    assert guard._pending_probe is None and not db.rows


@pytest.mark.asyncio
async def test_different_database_cannot_start_second_probe():
    db,entered,release=delayed_db()
    first=asyncio.create_task(guard.probe_google_budget_guard_transactions(db));await entered.wait()
    other=DB()
    try:
        with pytest.raises(HTTPException) as busy:await guard.probe_google_budget_guard_transactions(other)
        assert busy.value.detail['code']=='google_budget_guard_probe_busy'
        assert not other.sessions
    finally:release.set();await first


@pytest.mark.asyncio
async def test_async_cleanup_future_survives_waiter_deadline_and_owns_retained_slot(monkeypatch):
    monkeypatch.setattr(guard,'PROBE_TIMEOUT_SECONDS',.02)
    db=DB(); original=db.start_session; cleanup=asyncio.get_running_loop().create_future()
    async def session():
        value=await original()
        def end_session():return cleanup
        value.end_session=end_session
        return value
    db.start_session=session
    with pytest.raises(HTTPException):await guard.probe_google_budget_guard_transactions(db)
    state=guard._pending_probe
    try:
        assert not cleanup.cancelled() and not state['task'].done()
        state['task'].cancel();await asyncio.sleep(0)
        assert not cleanup.cancelled() and not state['task'].done()
    finally:
        cleanup.set_result(None)
        await asyncio.gather(state['task'],return_exceptions=True);await asyncio.sleep(0)
    assert guard._pending_probe is None


@pytest.mark.asyncio
async def test_mongos_is_rejected_without_write_or_capability_until_commit_contract_proved():
    db=DB()
    async def command(name,*,session):
        assert name=='hello'
        return {'msg':'isdbgrid','logicalSessionTimeoutMinutes':30}
    db.command=command
    with pytest.raises(HTTPException) as error:await guard.probe_google_budget_guard_transactions(db)
    assert error.value.detail['code']=='google_budget_guard_transactions_unavailable'
    assert not db.rows and not db.calls
    assert db.sessions[0].commit_calls==0 and not db.sessions[0].in_transaction


@pytest.mark.asyncio
async def test_engine_probe_only_writes_allowlisted_technical_namespace():
    db=DB();capability=await guard.probe_google_budget_guard_transactions(db)
    assert capability.supported and not db.rows
    assert db.calls and all(name==guard.TECHNICAL_PROBES for name,session in db.calls)
    assert all(session.aborted for session in db.sessions)
