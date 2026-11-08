"""Concurrency regressions against a disposable real replica set."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import supplier_receiving_routes as r
import supplier_scan_attempts as attempts
from test_supplier_scan_recovery import env, seed, post_scan

pytestmark = pytest.mark.asyncio


async def test_same_piece_recovery_releases_lock_for_next_piece(env):
    db, http, _ = env
    session, pieces = await seed(env, 2)
    assert (await post_scan(http, session['id'], pieces[0], 'original-0001', 1)).status_code == 200
    assert (await post_scan(http, session['id'], pieces[0], 'rescan-000001', 1)).status_code == 200
    saved = await db[r.SESSIONS].find_one({'id': session['id']})
    assert not saved.get('scan_lock_token'), 'early recovery leaked the session lock'
    response = await post_scan(http, session['id'], pieces[1], 'next-piece-001', 1)
    assert response.status_code == 200, response.text


async def outcome(http, session_id, request_id):
    response = await http.get(f'/supplier-receiving-v1/sessions/{session_id}/scan-requests/{request_id}')
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('count', [10, 50])
async def test_sequential_pieces_with_lost_slow_response(env, monkeypatch, count):
    db, http, _ = env
    session, pieces = await seed(env, count)
    original = r._supplier_product_reference_price
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        if kwargs['piece']['piece_id'] == pieces[count // 2]['piece_id']:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(r, '_supplier_product_reference_price', delayed)
    for index, piece in enumerate(pieces):
        request_id = f'sequence-{index:05}'
        task = asyncio.create_task(post_scan(http, session['id'], piece, request_id, 1))
        if index == count // 2:
            await asyncio.wait_for(entered.wait(), 5)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), .01)
            pending = await outcome(http, session['id'], request_id)
            assert pending['outcome'] == 'in_progress' and not pending['committed']
            # Uncommitted piece/event changes must not escape the transaction.
            assert await db[r.RECEIVING_EVENTS].count_documents({'event_type': 'supplier_piece_scanned'}) == index
            release.set()
        response = await task
        assert response.status_code == 200, response.text
        recovered = await outcome(http, session['id'], request_id)
        assert recovered['outcome'] == 'confirmed' and recovered['committed']
    assert await db[r.RECEIVING_EVENTS].count_documents({'event_type': 'supplier_piece_scanned'}) == count
    assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == count
    saved = await db[r.SESSIONS].find_one({'id': session['id']})
    assert saved['scan_count'] == count and not saved.get('scan_lock_token')


async def test_cancel_after_piece_write_aborts_entire_receipt(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    entered = asyncio.Event()
    async def pause(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(r, '_supplier_product_reference_price', pause)
    task = asyncio.create_task(post_scan(http, session['id'], pieces[0], 'partial-cancel-01'))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == 0
    assert await db[r.RECEIVING_EVENTS].count_documents({}) == 0
    assert (await db[r.SESSIONS].find_one({'id': session['id']}))['scan_count'] == 0
    assert (await outcome(http, session['id'], 'partial-cancel-01'))['outcome'] == 'rejected'


async def test_lost_commit_ack_does_not_rollback_confirmed_receipt(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    original = attempts.transaction
    async def lose_ack(db, callback):
        result = await original(db, callback)
        if callback.__name__ == 'execute':
            raise ConnectionError('commit succeeded, acknowledgement lost')
        return result
    monkeypatch.setattr(attempts, 'transaction', lose_ack)
    response = await post_scan(http, session['id'], pieces[0], 'lost-commit-0001')
    assert response.status_code == 200, response.text
    assert response.json()['outcome'] == 'confirmed'
    assert await db[r.RECEIVING_EVENTS].count_documents({'event_type': 'supplier_piece_scanned'}) == 1
    assert (await db[r.SESSIONS].find_one({'id': session['id']}))['scan_count'] == 1


async def test_old_owner_cannot_write_or_clear_new_owner_lock(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    entered, release = asyncio.Event(), asyncio.Event()
    original = r.resolve_scanned_piece
    async def pause(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(r, 'resolve_scanned_piece', pause)
    task = asyncio.create_task(post_scan(http, session['id'], pieces[0], 'old-owner-00001'))
    await asyncio.wait_for(entered.wait(), 5)
    await db[r.SESSIONS].update_one({'id': session['id']}, {'$set': {'scan_lock_token': 'new-owner-token'}})
    release.set()
    result = await task
    assert result.status_code == 409, result.text
    assert (await db[r.SESSIONS].find_one({'id': session['id']}))['scan_lock_token'] == 'new-owner-token'
    assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == 0
    assert await db[r.RECEIVING_EVENTS].count_documents({}) == 0


async def test_crashed_worker_expires_without_replaying_post(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    original = attempts.settle_aborted
    async def unavailable(*args, **kwargs):
        raise ConnectionError('process cannot clean up')
    async def crash(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(attempts, 'settle_aborted', unavailable)
    monkeypatch.setattr(r, 'resolve_scanned_piece', crash)
    with pytest.raises(asyncio.CancelledError):
        await post_scan(http, session['id'], pieces[0], 'crashed-worker-01')
    assert (await outcome(http, session['id'], 'crashed-worker-01'))['outcome'] == 'in_progress'
    await db[attempts.ATTEMPTS].update_many({}, {'$set': {'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    monkeypatch.setattr(attempts, 'settle_aborted', original)
    await attempts.expire_attempts(db)
    assert (await outcome(http, session['id'], 'crashed-worker-01'))['outcome'] == 'rejected'
    assert not (await db[r.SESSIONS].find_one({'id': session['id']})).get('scan_lock_token')
    assert await db[r.RECEIVING_EVENTS].count_documents({}) == 0


async def test_pending_attempt_blocks_cancel_and_other_employee(env, monkeypatch):
    db, http, identity = env
    session, pieces = await seed(env, 2)
    entered, release = asyncio.Event(), asyncio.Event()
    original = r.resolve_scanned_piece
    async def pause(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(r, 'resolve_scanned_piece', pause)
    task = asyncio.create_task(post_scan(http, session['id'], pieces[0], 'active-request-1', 1))
    await asyncio.wait_for(entered.wait(), 5)
    busy = await post_scan(http, session['id'], pieces[1], 'competing-request-1', 1)
    assert busy.status_code == 409
    assert (await outcome(http, session['id'], 'competing-request-1'))['outcome'] == 'rejected'
    cancel = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/cancel", json={})
    assert cancel.status_code == 409 and cancel.json()['detail']['code'] == 'supplier_receiving_scans_pending'
    identity['id'] = 'another-employee'
    denied = await post_scan(http, session['id'], pieces[1], 'other-employee-1', 1)
    assert denied.status_code == 403
    identity['id'] = 'receiver'
    release.set()
    assert (await task).status_code == 200
    cancel = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/cancel", json={})
    assert cancel.status_code == 200, cancel.text
    assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == 0
    assert (await db[r.SESSIONS].find_one({'id': session['id']}))['status'] == 'cancelled'


async def test_alias_lost_response_for_one_piece_of_quantity_group(env):
    db, http, _ = env
    session, pieces = await seed(env, 3)
    assert (await post_scan(http, session['id'], pieces[0], 'group-original-1', 3)).status_code == 200
    assert (await post_scan(http, session['id'], pieces[1], 'alias-request-01', 1)).status_code == 200
    recovered = await outcome(http, session['id'], 'alias-request-01')
    assert recovered['outcome'] == 'confirmed'
    assert recovered['client_request_id'] == 'alias-request-01'
    assert [p['piece_id'] for p in recovered['scans']] == [pieces[1]['piece_id']]
    assert (await db[r.SESSIONS].find_one({'id': session['id']}))['scan_count'] == 3
    removed = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scans/remove", json={
        'barcode': 'MEZAN-PIECE:' + pieces[1]['piece_id'],
        'expected_event_id': recovered['scan']['id'],
    })
    assert removed.status_code == 200, removed.text
    for request_id in ['group-original-1', 'alias-request-01']:
        cancelled = await outcome(http, session['id'], request_id)
        assert cancelled['outcome'] == 'rejected' and cancelled['cancelled']
    assert (await db[r.SESSIONS].find_one({'id': session['id']}))['scan_count'] == 2


async def test_execution_timeout_is_terminal_and_releases_atomic_receipt(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(attempts, 'EXECUTION_SECONDS', .1)
    monkeypatch.setattr(r, '_supplier_product_reference_price', slow)
    response = await post_scan(http, session['id'], pieces[0], 'server-timeout-1')
    assert response.status_code == 200, response.text
    assert response.json()['outcome'] == 'rejected'
    assert not (await db[r.SESSIONS].find_one({'id': session['id']})).get('scan_lock_token')
    assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == 0


async def test_cancellation_after_committed_admission_cleans_exact_lease(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    original = attempts.transaction
    async def lose_ack(db, callback):
        result = await original(db, callback)
        if callback.__name__ == 'admit':
            raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(attempts, 'transaction', lose_ack)
    with pytest.raises(asyncio.CancelledError):
        await post_scan(http, session['id'], pieces[0], 'admission-cancel-1')
    assert not (await db[r.SESSIONS].find_one({'id': session['id']})).get('scan_lock_token')
    assert (await outcome(http, session['id'], 'admission-cancel-1'))['outcome'] == 'rejected'


async def test_absent_sent_intent_expires_read_only_and_late_post_cannot_receive(env):
    db, http, _ = env
    session, pieces = await seed(env)
    deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    url = f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/absent-intent-001"
    response = await http.get(url, params={'client_expires_at': deadline})
    assert response.json()['outcome'] == 'rejected'
    assert await db[attempts.ATTEMPTS].count_documents({}) == 0, 'GET must never admit or fence writes'
    late = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan", json={
        'barcode': 'MEZAN-PIECE:' + pieces[0]['piece_id'], 'client_request_id': 'absent-intent-001',
        'client_expires_at': deadline,
    })
    assert late.status_code == 409, late.text
    assert await db[r.RECEIVING_EVENTS].count_documents({}) == 0
    assert not (await db[r.SESSIONS].find_one({'id': session['id']})).get('scan_lock_token')
    # No immutable deadline on a historical attempt: absence remains unknown.
    assert (await outcome(http, session['id'], 'old-absent-intent'))['outcome'] == 'unresolved'


async def test_admission_commit_after_absence_proof_cannot_start_receipt(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    entered, release = asyncio.Event(), asyncio.Event()
    original = attempts.transaction
    async def pause_admission(db, callback):
        if callback.__name__ != 'admit':
            return await original(db, callback)
        async def paused(scoped):
            result = await callback(scoped)
            entered.set()
            await release.wait()
            return result
        return await original(db, paused)
    monkeypatch.setattr(attempts, 'transaction', pause_admission)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=.2)
    task = asyncio.create_task(http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan", json={
        'barcode': 'MEZAN-PIECE:' + pieces[0]['piece_id'], 'client_request_id': 'late-admission-1',
        'client_expires_at': deadline.isoformat(),
    }))
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.sleep(max(0, (deadline-datetime.now(timezone.utc)).total_seconds())+.01)
    proof = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/late-admission-1",
                           params={'client_expires_at': deadline.isoformat()})
    assert proof.json()['outcome'] == 'rejected'
    release.set()
    assert (await task).status_code == 409
    assert await db[r.RECEIVING_EVENTS].count_documents({}) == 0
    assert await db[r.PIECES].count_documents({'supplier_receiving_session_id': session['id']}) == 0


async def test_application_lifespan_reconciles_crashed_worker(env, monkeypatch):
    from fastapi import APIRouter, FastAPI
    db, _, _ = env
    session, _ = await seed(env)
    await db[r.SESSIONS].update_one({'id': session['id']}, {'$set': {'scan_lock_token': 'dead-worker'}})
    await db[attempts.ATTEMPTS].insert_one({
        '_id': 'dead-worker', 'user_id': 'merchant', 'session_id': session['id'],
        'token': 'dead-worker', 'state': 'in_progress',
        'expires_at': datetime.now(timezone.utc)-timedelta(seconds=1),
    })
    settled = asyncio.Event()
    original = attempts.settle_aborted
    async def observed(*args, **kwargs):
        await original(*args, **kwargs)
        settled.set()
    monkeypatch.setattr(attempts, 'settle_aborted', observed)
    router, parent, app = APIRouter(), APIRouter(), FastAPI()
    attempts.install_reconciler(router, db)
    parent.include_router(router)
    app.include_router(parent)
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(settled.wait(), 5)
        assert (await db[attempts.ATTEMPTS].find_one({'_id': 'dead-worker'}))['state'] == 'rejected'
        assert not (await db[r.SESSIONS].find_one({'id': session['id']})).get('scan_lock_token')


async def test_deadline_uses_primary_database_clock_not_application_clock(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    class FastClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=1)
    monkeypatch.setattr(attempts, 'datetime', FastClock)
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    recovered = await http.get(f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/database-clock-1",
                               params={'client_expires_at': deadline})
    assert recovered.json()['outcome'] == 'unresolved', recovered.text
    response = await http.post(f"/supplier-receiving-v1/sessions/{session['id']}/scan", json={
        'barcode': 'MEZAN-PIECE:' + pieces[0]['piece_id'], 'client_request_id': 'database-clock-1',
        'client_expires_at': deadline,
    })
    assert response.status_code == 200 and response.json()['outcome'] == 'confirmed', response.text


async def test_cancelled_request_after_lock_does_not_strand_session(env, monkeypatch):
    db, http, _ = env
    session, pieces = await seed(env)
    entered = asyncio.Event()
    original = r.resolve_scanned_piece

    async def pause(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(r, 'resolve_scanned_piece', pause)
    task = asyncio.create_task(post_scan(http, session['id'], pieces[0], 'cancelled-0001'))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = await db[r.SESSIONS].find_one({'id': session['id']})
    assert not saved.get('scan_lock_token'), 'request cancellation leaked the lock'
    monkeypatch.setattr(r, 'resolve_scanned_piece', original)
    response = await post_scan(http, session['id'], pieces[0], 'after-cancel-001')
    assert response.status_code == 200, response.text
