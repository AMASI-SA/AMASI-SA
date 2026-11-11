"""Interrupted automatic work keeps evidence and cannot hot-loop uncertain sends."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod_manual import auto_cycle as cycles, auto_send, cycle_manifest
from test_qoyod_plan_b_auto_send import _candidate, _prepare_run
from test_qoyod_real_mongo import real_db


@pytest.mark.asyncio
@pytest.mark.parametrize('during_send', [False, True])
async def test_cancelled_run_is_terminal_and_holds_only_the_interrupted_order(monkeypatch, during_send):
    db = AsyncMongoMockClient().lifecycle
    entered = asyncio.Event()
    writes = []

    async def send(_db, **kwargs):
        writes.append(kwargs['order_number'])
        entered.set()
        await asyncio.Event().wait()

    await _prepare_run(monkeypatch, candidates=[_candidate('1001'), _candidate('1002')], send_one=send)
    if not during_send:
        async def refresh(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(auto_send, '_refresh_and_verify_salla_status', refresh)

    task = asyncio.create_task(auto_send.run_once(db))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    audit = await db.qoyod_manual_auto_runs.find_one({})
    assert audit['status'] == 'cancelled'
    assert audit['finished_at'] >= audit['started_at']
    assert audit['result']['results'][0]['outcome'] == 'manual_review'
    hold = await db.qoyod_manual_auto_quarantines.find_one({'order_number': '1001'})
    assert hold['status'] == 'open'
    assert hold['code'] == 'automatic_run_interrupted'
    assert hold['detail']['provider_outcome_unconfirmed'] is during_send
    assert await db.qoyod_manual_auto_quarantines.count_documents({'order_number': '1002'}) == 0
    assert writes == (['1001'] if during_send else [])

    # A later round may move on, but must never blindly replay an unknown write.
    async def next_send(_db, **kwargs):
        writes.append(kwargs['order_number'])
        return {'invoice_id': 'invoice-1002'}
    await _prepare_run(monkeypatch, candidates=[_candidate('1001'), _candidate('1002')], send_one=next_send)
    result = await auto_send.run_once(db)
    assert [row['order_number'] for row in result['results']] == ['1002']
    assert writes == (['1001', '1002'] if during_send else ['1002'])


@pytest.mark.asyncio
async def test_completed_order_is_durable_before_the_next_external_call(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    manifest_outcomes = []
    entered = asyncio.Event()

    async def record(_db, result):
        manifest_outcomes.extend(result['results'])
    monkeypatch.setattr(cycle_manifest, 'record_results', record)

    async def send(_db, **kwargs):
        if kwargs['order_number'] == '1001':
            return {'invoice_id': 'verified-1001'}
        audit = await db.qoyod_manual_auto_runs.find_one({})
        assert audit.get('results') == [{
            'order_number': '1001', 'outcome': 'sent', 'invoice_only': False,
            'invoice_id': 'verified-1001', 'payment_id': None,
        }]
        assert manifest_outcomes == audit['results']
        entered.set()
        await asyncio.Event().wait()

    await _prepare_run(monkeypatch, candidates=[_candidate('1001'), _candidate('1002')], send_one=send)
    task = asyncio.create_task(auto_send.run_once(db))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    audit = await db.qoyod_manual_auto_runs.find_one({})
    assert audit['status'] == 'cancelled'
    assert audit['result']['sent_count'] == 1
    assert audit['result']['manual_review_count'] == 1
    assert await db.qoyod_manual_auto_quarantines.count_documents({'order_number': '1001'}) == 0


@pytest.mark.asyncio
async def test_cancellation_after_audit_checkpoint_preserves_manifest_result(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    entered = asyncio.Event()
    restored = []
    calls = 0
    async def record(_db, result):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        restored.extend(result['results'])
    monkeypatch.setattr(cycle_manifest, 'record_results', record)
    await _prepare_run(monkeypatch, candidates=[_candidate('1001')],
                       send_one=AsyncMock(return_value={'invoice_id': 'confirmed'}))
    task = asyncio.create_task(auto_send.run_once(db))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    audit = await db.qoyod_manual_auto_runs.find_one({})
    assert audit['status'] == 'cancelled' and audit['result']['sent_count'] == 1
    assert [row['invoice_id'] for row in restored] == ['confirmed']
    assert await db.qoyod_manual_auto_quarantines.count_documents({}) == 0


@pytest.mark.asyncio
async def test_cancellation_after_retry_projection_creates_a_nonretryable_hold(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    entered = asyncio.Event()
    async def project_then_block(_db, *, order_number, exc, run_id):
        await db.qoyod_manual_auto_quarantines.insert_one({
            '_id': f'main:{order_number}', 'user_id': 'main', 'order_number': order_number,
            'status': 'open', 'code': exc.code, 'run_id': run_id,
            'last_seen_at': datetime.now(timezone.utc) + timedelta(seconds=1),
            'next_retry_at': datetime.now(timezone.utc) + timedelta(minutes=1),
        })
        entered.set()
        await asyncio.Event().wait()
    await _prepare_run(monkeypatch, candidates=[_candidate('1001')], send_one=AsyncMock(
        side_effect=auto_send.ManualSendRefused('qoyod_http_error', 'synthetic', {'status_code': 503})))
    monkeypatch.setattr(auto_send, '_quarantine_order', project_then_block)
    task = asyncio.create_task(auto_send.run_once(db))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    hold = await db.qoyod_manual_auto_quarantines.find_one({})
    assert hold['code'] == 'automatic_run_interrupted'
    assert 'next_retry_at' not in hold
    assert await auto_send._open_quarantined_order_numbers(db, ['1001']) == {'1001'}


@pytest.mark.asyncio
async def test_error_after_audit_insertion_also_finishes_that_audit(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    await _prepare_run(monkeypatch, candidates=[_candidate('1001')], send_one=AsyncMock())
    def unavailable():
        raise RuntimeError('synthetic transport setup failure')
    monkeypatch.setattr(auto_send, 'begin_shared_manual_qoyod_client', unavailable)
    result = await auto_send.run_once(db)
    assert result['status'] == 'round_failed'
    audit = await db.qoyod_manual_auto_runs.find_one({})
    assert audit['status'] == 'round_failed'
    assert audit['finished_at'] >= audit['started_at']
    assert audit['result']['exception_type'] == 'RuntimeError'


@pytest.mark.asyncio
async def test_empty_successful_batch_stops_once_instead_of_spinning(monkeypatch):
    db = AsyncMock()
    db.__getitem__.return_value = AsyncMock()
    db.__getitem__.return_value.find_one.return_value = {'owner': 'lease-owner'}
    db.__getitem__.return_value.update_one.return_value.matched_count = 1
    sender = AsyncMock(return_value={
        'ok': True, 'status': 'succeeded', 'sent_count': 0, 'results': [],
        'discovery': {'checked': 5, 'scope': 'cycle_manifest'},
        'runnable_candidate_count': 5,
    })
    result = await cycles._drain_body(db, {'owner': 'lease-owner'}, sender, 5, 3, 0, False)
    assert result['status'] == 'no_progress'
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_final_outcome_for_the_same_orders_is_progress():
    db = AsyncMock()
    db.__getitem__.return_value = AsyncMock()
    db.__getitem__.return_value.find_one.return_value = {'owner': 'lease-owner'}
    db.__getitem__.return_value.update_one.return_value.matched_count = 1
    sender = AsyncMock(side_effect=[
        {'ok': True, 'status': 'succeeded', 'sent_count': 0,
         'results': [{'order_number': 'same', 'outcome': 'skipped_in_progress'}]},
        {'ok': True, 'status': 'succeeded', 'sent_count': 0,
         'results': [{'order_number': 'same', 'outcome': 'already_sent'}]},
        {'ok': True, 'status': 'idle'},
    ])
    result = await cycles._drain_body(db, {'owner': 'lease-owner'}, sender, 5, 3, 0, False)
    assert result['status'] == 'idle'
    assert sender.await_count == 3


@pytest.mark.asyncio
async def test_stale_audit_recovery_is_bounded_and_never_claims_remote_success(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    now = datetime.now(timezone.utc)
    for index in range(4):
        await db.qoyod_manual_auto_runs.insert_one({
            'run_id': f'abandoned-{index}', 'status': 'in_progress',
            'started_at': now - timedelta(hours=1), 'order_numbers': [f'old-{index}'],
        })
    await db.qoyod_manual_auto_runs.insert_one({
        'run_id': 'recent', 'status': 'in_progress', 'started_at': now,
        'order_numbers': ['recent-order'],
    })
    monkeypatch.setattr(auto_send, '_lease_is_current', AsyncMock(return_value=True))
    recovered = await auto_send._recover_abandoned_runs(db, 'owned', limit=2)
    assert recovered == 2
    assert await db.qoyod_manual_auto_runs.count_documents({'status': 'interrupted'}) == 2
    assert await db.qoyod_manual_auto_runs.count_documents({'status': 'in_progress'}) == 3
    assert await db.qoyod_manual_auto_runs.count_documents({'status': 'succeeded'}) == 0
    assert await db.qoyod_manual_auto_quarantines.count_documents({'status': 'open'}) == 2
    assert await db.qoyod_manual_auto_quarantines.count_documents({'order_number': 'recent-order'}) == 0


@pytest.mark.asyncio
async def test_stale_repair_preserves_newer_review_and_unstarted_orders(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    now = datetime.now(timezone.utc)
    await db.qoyod_manual_auto_runs.insert_one({
        'run_id': 'old', 'status': 'in_progress', 'started_at': now - timedelta(hours=1),
        'order_numbers': ['resolved', 'untouched'], 'active_order_number': 'resolved',
    })
    await db.qoyod_manual_auto_quarantines.insert_one({
        '_id': 'main:resolved', 'order_number': 'resolved', 'status': 'resolved',
        'resolved_at': now, 'resolved_invoice_id': 'verified-invoice',
    })
    monkeypatch.setattr(auto_send, '_lease_is_current', AsyncMock(return_value=True))
    assert await auto_send._recover_abandoned_runs(db, 'owned') == 1
    saved = await db.qoyod_manual_auto_quarantines.find_one({'_id': 'main:resolved'})
    assert saved['status'] == 'resolved' and saved['resolved_invoice_id'] == 'verified-invoice'
    assert await db.qoyod_manual_auto_quarantines.count_documents({'order_number': 'untouched'}) == 0


@pytest.mark.asyncio
async def test_stale_repair_stops_if_worker_ownership_changes(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    await db.qoyod_manual_auto_runs.insert_one({
        'run_id': 'old', 'status': 'in_progress',
        'started_at': datetime.now(timezone.utc) - timedelta(hours=1),
        'order_numbers': ['unconfirmed'],
    })
    monkeypatch.setattr(auto_send, '_lease_is_current', AsyncMock(return_value=False))
    assert await auto_send._recover_abandoned_runs(db, 'replaced') == 0
    assert (await db.qoyod_manual_auto_runs.find_one({}))['status'] == 'in_progress'
    assert await db.qoyod_manual_auto_quarantines.count_documents({}) == 0


@pytest.mark.asyncio
async def test_recent_crashed_run_is_held_before_another_provider_call(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    now = datetime.now(timezone.utc)
    # A full historical backlog must not hide the newly interrupted request.
    await db.qoyod_manual_auto_runs.insert_many([{
        'run_id': f'legacy-{index}', 'status': 'in_progress',
        'started_at': now - timedelta(hours=1), 'order_numbers': [f'legacy-{index}'],
    } for index in range(auto_send._ABANDONED_RUN_PAGE)])
    await db.qoyod_manual_auto_runs.insert_one({
        'run_id': 'recent-crash', 'status': 'in_progress', 'started_at': now,
        'lease_owner': 'previous-worker', 'active_order_number': '1001',
        'order_numbers': ['1001', '1002'],
    })
    provider_calls = ['1001']  # Previous process died after starting this call.
    async def send(_db, **kwargs):
        provider_calls.append(kwargs['order_number'])
        return {'invoice_id': 'confirmed-1002'}
    recovery = auto_send._recover_abandoned_runs
    await _prepare_run(monkeypatch, candidates=[_candidate('1001'), _candidate('1002')], send_one=send)
    monkeypatch.setattr(auto_send, '_recover_abandoned_runs', recovery)
    result = await auto_send.run_once(db)
    assert provider_calls == ['1001', '1002']
    assert result['sent_count'] == 1
    old = await db.qoyod_manual_auto_runs.find_one({'run_id': 'recent-crash'})
    assert old['status'] == 'interrupted'
    hold = await db.qoyod_manual_auto_quarantines.find_one({'order_number': '1001'})
    assert hold['code'] == 'automatic_run_interrupted'
    assert hold['detail']['provider_outcome_unconfirmed'] is True
    assert await db.qoyod_manual_auto_runs.count_documents({'status': 'interrupted'}) == auto_send._ABANDONED_RUN_PAGE


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_owner', [None, ''])
async def test_takeover_preserves_current_owner_and_recent_legacy_audits(monkeypatch, legacy_owner):
    db = AsyncMongoMockClient().lifecycle
    now = datetime.now(timezone.utc)
    current = {'run_id': 'current', 'status': 'in_progress', 'lease_owner': 'owned',
               'started_at': now - timedelta(hours=1), 'active_order_number': 'current-order'}
    await db.qoyod_manual_auto_runs.insert_many([
        current,
        {'run_id': 'recent-legacy', 'status': 'in_progress', 'lease_owner': legacy_owner,
         'started_at': now, 'order_numbers': ['recent-order']},
        {'run_id': 'old-legacy', 'status': 'in_progress', 'lease_owner': legacy_owner,
         'started_at': now - timedelta(hours=1), 'order_numbers': ['old-order']},
    ])
    before = await db.qoyod_manual_auto_runs.find_one({'run_id': 'current'})
    monkeypatch.setattr(auto_send, '_lease_is_current', AsyncMock(return_value=True))
    assert await auto_send._recover_abandoned_runs(db, 'owned') == 1
    assert await db.qoyod_manual_auto_runs.find_one({'run_id': 'current'}) == before
    assert (await db.qoyod_manual_auto_runs.find_one({'run_id': 'recent-legacy'}))['status'] == 'in_progress'
    assert (await db.qoyod_manual_auto_runs.find_one({'run_id': 'old-legacy'}))['status'] == 'interrupted'
    assert await db.qoyod_manual_auto_quarantines.distinct('order_number') == ['old-order']


@pytest.mark.asyncio
async def test_stale_repair_preserves_a_later_completed_send(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    now = datetime.now(timezone.utc)
    await db.qoyod_manual_auto_runs.insert_one({
        'run_id': 'old', 'status': 'in_progress', 'started_at': now - timedelta(hours=1),
        'order_numbers': ['completed-later', 'cod-later', 'partial-later'],
    })
    await db.qoyod_manual_send_locks.insert_many([
        {'user_id': 'main', 'order_number': 'completed-later', 'status': 'succeeded',
         'manual_qoyod_invoice_id': '101', 'manual_qoyod_payment_id': '102', 'finished_at': now},
        {'user_id': 'main', 'order_number': 'cod-later', 'status': 'succeeded',
         'manual_qoyod_invoice_id': '301', 'finished_at': now},
        {'user_id': 'main', 'order_number': 'partial-later', 'status': 'partial_payment_failed',
         'manual_qoyod_invoice_id': '201', 'finished_at': now},
    ])
    await db.integration_inbox.insert_one({
        'user_id': 'orders-user', 'salla_order_number': 'cod-later',
        'manual_qoyod_invoice_id': '301', 'manual_send_last_status': 'succeeded_invoice_only',
        'manual_send_at': now,
    })
    monkeypatch.setattr(auto_send, '_lease_is_current', AsyncMock(return_value=True))
    assert await auto_send._recover_abandoned_runs(db, 'owned') == 1
    audit = await db.qoyod_manual_auto_runs.find_one({})
    assert audit['result']['already_sent_count'] == 2
    assert audit['results'][0]['invoice_id'] == '101'
    assert await db.qoyod_manual_auto_quarantines.count_documents({'order_number': 'completed-later'}) == 0
    assert await db.qoyod_manual_auto_quarantines.count_documents({'order_number': 'cod-later'}) == 0
    assert await db.qoyod_manual_auto_quarantines.count_documents({'order_number': 'partial-later', 'status': 'open'}) == 1


@pytest.mark.asyncio
async def test_poll_repairs_stale_audits_even_when_financial_cycle_is_not_due(monkeypatch):
    db = AsyncMongoMockClient().lifecycle
    await db.qoyod_manual_auto_runs.insert_one({
        'run_id': 'old', 'status': 'in_progress',
        'started_at': datetime.now(timezone.utc) - timedelta(hours=1),
        'order_numbers': ['uncertain'],
    })
    observed = {}
    async def not_due(*args, **kwargs):
        observed.update(await db.qoyod_manual_auto_runs.find_one({}))
        raise asyncio.CancelledError
    monkeypatch.setattr(cycles, 'run_due_cycle', not_due)
    sender = AsyncMock(side_effect=AssertionError('metadata recovery must never send'))
    monkeypatch.setattr(auto_send, 'manual_send_one', sender)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(auto_send._loop(db, interval_sec=60, batch_limit=5), timeout=3)
    assert observed['status'] == 'interrupted'
    assert await db.qoyod_manual_auto_quarantines.count_documents({'status': 'open'}) == 1
    sender.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('job_status', ['queued', 'running', 'needs_review'])
async def test_durable_manual_job_fences_auto_send_without_requiring_quarantine(monkeypatch, job_status):
    db = AsyncMongoMockClient().lifecycle
    await db.qoyod_manual_send_jobs.insert_one({
        'user_id': 'main', 'order_number': '1001', 'status': job_status,
    })
    sender = AsyncMock(return_value={'invoice_id': 'completed-1002'})
    await _prepare_run(monkeypatch, candidates=[_candidate('1001'), _candidate('1002')], send_one=sender)
    result = await auto_send.run_once(db)
    assert result['sent_count'] == 1
    assert result['manual_review_count'] == 1
    assert result['results'][0]['code'] == (
        'manual_job_outcome_unknown' if job_status == 'needs_review' else 'manual_job_pending')
    assert [call.kwargs['order_number'] for call in sender.await_args_list] == ['1002']
    assert await db.qoyod_manual_auto_quarantines.count_documents({}) == 0


@pytest.mark.asyncio
async def test_failed_terminal_audit_write_is_reported_and_keeps_completed_evidence(monkeypatch):
    backing = AsyncMongoMockClient().lifecycle
    class Runs:
        def __getattr__(self, name):
            return getattr(backing.qoyod_manual_auto_runs, name)
        async def update_one(self, selector, update):
            if 'status' in update.get('$set', {}):
                raise ConnectionError('synthetic audit write outage')
            return await backing.qoyod_manual_auto_runs.update_one(selector, update)
    class Database:
        qoyod_manual_auto_runs = Runs()
        def __getattr__(self, name):
            return getattr(backing, name)
    sender = AsyncMock(return_value={'invoice_id': 'known-invoice'})
    await _prepare_run(monkeypatch, candidates=[_candidate('1001')], send_one=sender)
    result = await auto_send.run_once(Database())
    assert result['ok'] is False and result['status'] == 'audit_finalize_pending'
    assert result['audit_finalized'] is False
    assert result['sent_count'] == 1
    audit = await backing.qoyod_manual_auto_runs.find_one({})
    assert audit['results'][0]['invoice_id'] == 'known-invoice'
    assert await backing.qoyod_manual_auto_quarantines.count_documents({}) == 0
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_mongo_recovery_lanes_are_indexed_and_prioritize_takeover(real_db):
    db, _ = real_db
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=1)
    await db.qoyod_manual_auto_runs.insert_many([
        {'run_id': f'completed-{index}', 'status': 'succeeded', 'started_at': old}
        for index in range(500)
    ] + [
        {'run_id': f'legacy-{index}', 'status': 'in_progress', 'started_at': old,
         **({'lease_owner': ''} if index % 2 else {})}
        for index in range(500)
    ] + [
        {'run_id': 'current', 'status': 'in_progress', 'started_at': old,
         'lease_owner': 'replacement', 'active_order_number': 'current-order'},
        {'run_id': 'recent-unknown', 'status': 'in_progress', 'started_at': now,
         'lease_owner': 'previous', 'active_order_number': 'unknown-order'},
    ])
    for selector, index_name in (
        ({'lease_owner': {'$type': 'string', '$nin': ['', 'replacement']}},
         'qoyod_manual_auto_runs_takeover'),
        ({'lease_owner': {'$in': [None, '']}, 'started_at': {'$lt': now - timedelta(minutes=15)}},
         'qoyod_manual_auto_runs_legacy_recovery'),
    ):
        plan = await db.command('explain', {
            'find': 'qoyod_manual_auto_runs', 'filter': {'status': 'in_progress', **selector},
            'sort': {'started_at': 1}, 'limit': auto_send._ABANDONED_RUN_PAGE, 'hint': index_name,
        }, verbosity='executionStats')
        assert index_name in str(plan['queryPlanner']['winningPlan'])
        assert plan['executionStats']['totalDocsExamined'] <= auto_send._ABANDONED_RUN_PAGE
    await db.qoyod_manual_auto_worker_leases.insert_one({
        '_id': 'main', 'owner': 'replacement', 'lease_until': now + timedelta(minutes=1),
    })
    assert await auto_send._recover_abandoned_runs(db, 'replacement', limit=1) == 1
    assert (await db.qoyod_manual_auto_runs.find_one({'run_id': 'recent-unknown'}))['status'] == 'interrupted'
    assert (await db.qoyod_manual_auto_runs.find_one({'run_id': 'current'}))['status'] == 'in_progress'
    assert await db.qoyod_manual_auto_runs.count_documents({'status': 'interrupted'}) == 1
    assert await db.qoyod_manual_auto_quarantines.distinct('order_number') == ['unknown-order']
