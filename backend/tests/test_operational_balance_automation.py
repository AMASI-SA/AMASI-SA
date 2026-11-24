"""Explicit owner automation: disabled by default, fenced and request independent."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

import operational_demand_refresh as demand
from operational_balance_store import STATES
from test_operational_balance_demand_refresh import DB, Request, allowed, update


def test_scheduled_lifecycle_is_distinct_and_saves_once_without_a_request():
    async def scenario():
        db, observed = DB(), []

        async def operation():
            context = demand.demand_context()
            observed.append((type(context).__name__, context.request))
            return await update(db)

        result = await demand.run_scheduled_refresh(db, 'owner', 'owner', allowed, operation)
        assert result['revision'] == 2
        assert observed == [('ScheduledRefreshContext', None)]
        assert db.data[demand.LEASES] == {}
        assert [op for name, op, _ in db.writes if name == STATES] == ['replace']
        assert demand.demand_context() is None
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_scheduled_refresh_contends_with_existing_visible_lease():
    async def scenario():
        db, entered, released = DB(), asyncio.Event(), asyncio.Event()
        async def pending():
            entered.set()
            await released.wait()
        manual = asyncio.create_task(demand.run_visible_refresh(
            Request(), db, 'owner', 'staff', allowed, pending))
        try:
            await entered.wait()
            with pytest.raises(HTTPException) as failure:
                await demand.run_scheduled_refresh(db, 'owner', 'owner', allowed, lambda: update(db))
            assert failure.value.detail['code'] == 'operational_refresh_in_progress'
            assert db.data[demand.LEASES]['owner']['actor_id'] == 'staff'
            assert not [op for name, op, _ in db.writes if name == STATES]
        finally:
            released.set()
            await manual
    asyncio.run(scenario())


def test_scheduler_parent_stop_cancels_and_joins_sources_without_writes():
    async def scenario():
        db, stop, entered, joined = DB(), asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def pending():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                joined.set()
        task = asyncio.create_task(demand.run_scheduled_refresh(
            db, 'owner', 'owner', allowed, pending, stop_event=stop))
        await entered.wait()
        stop.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert joined.is_set() and not db.data[demand.LEASES]
        assert not [op for name, op, _ in db.writes if name == STATES]
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_scheduler_deadline_does_not_extend_manual_budget(monkeypatch):
    monkeypatch.setattr(demand, 'REQUEST_SECONDS', .02)
    async def scenario():
        db, joined = DB(), asyncio.Event()
        async def pending():
            try:
                await asyncio.Event().wait()
            finally:
                joined.set()
        with pytest.raises(HTTPException) as failure:
            await demand.run_scheduled_refresh(db, 'owner', 'owner', allowed, pending)
        assert failure.value.status_code == 408
        assert failure.value.detail['code'] == 'operational_refresh_timeout'
        assert joined.is_set() and not db.data[demand.LEASES]
    asyncio.run(scenario())


def test_scheduler_rechecks_revocation_before_financial_commit():
    async def scenario():
        db, revoked = DB(), False
        saved = deepcopy(db.data[STATES])
        async def verify():
            if revoked:
                raise HTTPException(403, {'code': 'operational_automation_disabled'})
        async def operation():
            nonlocal revoked
            async def apply(state):
                nonlocal revoked
                state['engine']['as_of'] = '2026-11-24T00:00:00+00:00'
                revoked = True
                return state
            from operational_balance_store import mutate
            return await mutate(db, 'owner', apply)
        with pytest.raises(HTTPException) as failure:
            await demand.run_scheduled_refresh(db, 'owner', 'owner', verify, operation)
        assert failure.value.status_code == 403
        assert db.data[STATES] == saved and not db.data[demand.LEASES]
    asyncio.run(scenario())


def test_parent_stop_observes_an_already_admitted_atomic_commit():
    async def scenario():
        db, stop = DB(), asyncio.Event()
        db.delay_cas = True
        task = asyncio.create_task(demand.run_scheduled_refresh(
            db, 'owner', 'owner', allowed, lambda: update(db), stop_event=stop))
        await db.cas_entered.wait()
        stop.set()
        await asyncio.sleep(0)
        assert not task.done()
        db.cas_allowed.set()
        result = await task
        assert result['revision'] == 2 and not db.data[demand.LEASES]
        assert [op for name, op, _ in db.writes if name == STATES] == ['replace']
    asyncio.run(scenario())


def test_automation_is_opt_in_and_status_is_metadata_only():
    async def scenario():
        import operational_balance_automation as automatic
        db = DB()
        saved = deepcopy(db.data)
        value = await automatic.read_status(db, 'owner')
        assert value['schema_version'] == 1 and value['enabled'] is False
        assert value['interval_seconds'] == 7200
        assert db.data == saved and db.writes == []
    asyncio.run(scenario())


def _matches(row, query):
    for key, value in query.items():
        actual = row.get(key)
        if key == '$or':
            if not any(_matches(row, option) for option in value):
                return False
        elif type(value) is dict:
            for operator, expected in value.items():
                if operator == '$exists' and (key in row) != expected:
                    return False
                if operator == '$eq' and actual != expected:
                    return False
                if operator == '$type' and expected == 'string' and type(actual) is not str:
                    return False
                if operator == '$gt' and not (actual is not None and actual > expected):
                    return False
                if operator == '$lte' and not (actual is not None and actual <= expected):
                    return False
        elif actual != value:
            return False
    return True


class SchedulerCollection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    async def find_one(self, query, projection=None, **options):
        self.db.reads.append((self.name, deepcopy(query), deepcopy(projection)))
        rows = [row for row in self.db.data.get(self.name, {}).values() if _matches(row, query)]
        for key, direction in reversed(options.get('sort', [])):
            rows.sort(key=lambda row: row.get(key), reverse=direction == -1)
        return deepcopy(rows[0]) if rows else None

    async def update_one(self, query, update, **options):
        self.db.writes.append((self.name, 'update', deepcopy(query)))
        rows = self.db.data.setdefault(self.name, {})
        row = next((row for row in rows.values() if _matches(row, query)), None)
        matched = row is not None
        if row is None and options.get('upsert'):
            from pymongo.errors import DuplicateKeyError
            if query['_id'] in rows:
                raise DuplicateKeyError('existing')
            row = rows[query['_id']] = {'_id': query['_id']}
        if row is not None:
            row.update(deepcopy(update['$set']))
        return SimpleNamespace(matched_count=int(matched))

    async def find_one_and_update(self, query, update, **options):
        from pymongo.errors import DuplicateKeyError
        rows = self.db.data.setdefault(self.name, {})
        row = next((row for row in rows.values() if _matches(row, query)), None)
        if row is None and query['_id'] in rows:
            raise DuplicateKeyError('busy')
        if row is None:
            row = rows[query['_id']] = {'_id': query['_id']}
        self.db.writes.append((self.name, 'acquire', deepcopy(query)))
        row.update(deepcopy(update['$set']))
        return deepcopy(row)

    async def delete_one(self, query):
        self.db.writes.append((self.name, 'release', deepcopy(query)))
        rows = self.db.data.setdefault(self.name, {})
        keys = [key for key, row in rows.items() if _matches(row, query)]
        for key in keys:
            del rows[key]
        return SimpleNamespace(deleted_count=len(keys))


class SchedulerDB(DB):
    def __init__(self):
        super().__init__()
        self.data['users'] = {'owner': {'id': 'owner', 'role': 'owner', 'is_active': True}}
        self.reads = []

    def __getitem__(self, name):
        if name == STATES:
            return super().__getitem__(name)
        return SchedulerCollection(self, name)


async def enable(db):
    import operational_balance_automation as automatic
    return await automatic.set_enabled(db, 'owner', True, allowed)


def _saved_financial_refresh(monkeypatch, *, pending=None, fail_code=None):
    import operational_balance_service
    import operational_visible_evidence
    calls = []
    async def native(db, owner):
        calls.append('native')
        if pending is not None:
            pending[0].set()
            await pending[1].wait()
        if fail_code:
            raise HTTPException(409, {'code': fail_code, 'message': 'PRIVATE-PROVIDER'})
        return {'status': 'completed'}
    async def refresh(db, owner):
        calls.append('refresh')
        async def apply(state):
            state['engine']['as_of'] = datetime.now(timezone.utc).isoformat()
            return state
        from operational_balance_store import mutate
        return await mutate(db, owner, apply)
    monkeypatch.setattr(operational_visible_evidence, 'acquire_visible_evidence', native)
    monkeypatch.setattr(operational_balance_service, 'refresh', refresh)
    return calls


def test_disabled_scheduler_reads_no_sources_and_makes_no_financial_write():
    async def scenario():
        import operational_balance_automation as automatic
        db = SchedulerDB()
        before = deepcopy(db.data[STATES])
        assert await automatic.tick(db) == {'outcome': 'idle'}
        assert {name for name, _, _ in db.reads} == {automatic.SETTINGS}
        assert db.data[STATES] == before
        assert {name for name, _, _ in db.writes} == {automatic.SERIAL_LEASES}
        assert not db.data[automatic.SERIAL_LEASES]
    asyncio.run(scenario())


def test_enabled_scheduler_runs_once_then_waits_two_hours(monkeypatch):
    async def scenario():
        import operational_balance_automation as automatic
        db = SchedulerDB()
        calls = _saved_financial_refresh(monkeypatch)
        status = await enable(db)
        assert status['enabled'] is True and status['financial_coverage_verified'] is False
        outcome = await automatic.tick(db)
        assert outcome['outcome'] == 'success' and outcome['revision'] == 2
        assert calls == ['native', 'refresh']
        saved = db.data[automatic.SETTINGS]['owner']
        delta = (saved['next_due_at'] - saved['last_finished_at']).total_seconds()
        assert delta == 7200 and saved['consecutive_failures'] == 0
        assert saved['last_error_code'] is None and saved['last_success_at'] == outcome['as_of']
        assert await automatic.tick(db) == {'outcome': 'idle'}
        assert calls == ['native', 'refresh']
        assert not db.data[demand.LEASES] and not db.data[automatic.SERIAL_LEASES]
        from operational_balance_freshness import HEALTH
        assert db.data[HEALTH]['owner']['last_attempt']['outcome'] == 'success'
    asyncio.run(scenario())


@pytest.mark.parametrize('mutation', ['disabled', 'removed_owner', 'accounting_active'])
def test_current_authority_and_accounting_gate_prevent_scheduled_money(monkeypatch, mutation):
    async def scenario():
        import operational_balance_automation as automatic
        db = SchedulerDB()
        calls = _saved_financial_refresh(monkeypatch)
        await enable(db)
        if mutation == 'disabled':
            db.data[automatic.SETTINGS]['owner']['enabled'] = False
        elif mutation == 'removed_owner':
            db.data['users']['owner']['role'] = 'employee'
        else:
            db.data['mz2_atomic_owners'] = {'owner': {'_id': 'owner', 'ledger_backend_state': 'v2_active'}}
        before = deepcopy(db.data[STATES])
        await automatic.tick(db)
        assert calls == [] and db.data[STATES] == before
        assert not db.data.get(demand.LEASES)
    asyncio.run(scenario())


def test_replicas_are_serial_and_cannot_release_each_others_lease(monkeypatch):
    async def scenario():
        import operational_balance_automation as automatic
        db, pending = SchedulerDB(), (asyncio.Event(), asyncio.Event())
        calls = _saved_financial_refresh(monkeypatch, pending=pending)
        await enable(db)
        first = asyncio.create_task(automatic.tick(db))
        await pending[0].wait()
        token = db.data[automatic.SERIAL_LEASES]['serial']['token']
        assert await automatic.tick(db) == {'outcome': 'busy'}
        assert db.data[automatic.SERIAL_LEASES]['serial']['token'] == token
        pending[1].set()
        assert (await first)['outcome'] == 'success'
        assert calls == ['native', 'refresh'] and not db.data[automatic.SERIAL_LEASES]
    asyncio.run(scenario())


def test_failures_keep_saved_finance_and_back_off_with_sanitized_durable_health(monkeypatch):
    async def scenario():
        import operational_balance_automation as automatic
        from operational_balance_freshness import HEALTH
        db = SchedulerDB()
        calls = _saved_financial_refresh(monkeypatch, fail_code='operational_refresh_timeout')
        await enable(db)
        saved = deepcopy(db.data[STATES])
        for count, delay in enumerate([300, 600, 1200, 2400, 4800, 7200, 7200], 1):
            row = db.data[automatic.SETTINGS]['owner']
            row['next_due_at'] = datetime.now(timezone.utc) - timedelta(seconds=1)
            outcome = await automatic.tick(db)
            assert outcome == {'outcome': 'failed', 'error_code': 'operational_refresh_timeout'}
            row = db.data[automatic.SETTINGS]['owner']
            assert row['consecutive_failures'] == count
            assert (row['next_due_at'] - row['last_finished_at']).total_seconds() == delay
            assert row['last_outcome'] == 'failed'
        assert db.data[STATES] == saved and 'refresh' not in calls
        assert 'PRIVATE' not in repr(db.data[automatic.SETTINGS])
        assert 'PRIVATE' not in repr(db.data[HEALTH])
        assert db.data[HEALTH]['owner']['last_attempt']['outcome'] == 'failed'
    asyncio.run(scenario())


def test_disabling_generation_during_acquisition_prevents_cas_and_reenable_is_fresh(monkeypatch):
    async def scenario():
        import operational_balance_automation as automatic
        db, pending = SchedulerDB(), (asyncio.Event(), asyncio.Event())
        _saved_financial_refresh(monkeypatch, pending=pending)
        await enable(db)
        original_generation = db.data[automatic.SETTINGS]['owner']['generation']
        saved = deepcopy(db.data[STATES])
        task = asyncio.create_task(automatic.tick(db))
        await pending[0].wait()
        await automatic.set_enabled(db, 'owner', False, allowed)
        await enable(db)
        new_generation = db.data[automatic.SETTINGS]['owner']['generation']
        assert new_generation != original_generation
        pending[1].set()
        outcome = await task
        assert outcome['outcome'] == 'failed'
        assert outcome['error_code'] == 'operational_automation_disabled'
        assert db.data[STATES] == saved
        assert db.data[automatic.SETTINGS]['owner']['generation'] == new_generation
        assert db.data[automatic.SETTINGS]['owner']['last_error_code'] is None
    asyncio.run(scenario())


def test_invalid_earliest_configuration_cannot_starve_a_valid_owner(monkeypatch):
    async def scenario():
        import operational_balance_automation as automatic
        db = SchedulerDB()
        calls = _saved_financial_refresh(monkeypatch)
        await enable(db)
        broken = {**deepcopy(db.data[automatic.SETTINGS]['owner']), '_id': 'broken',
                  'owner_id': 'broken', 'actor_id': 'different-owner',
                  'next_due_at': datetime.now(timezone.utc) - timedelta(days=1)}
        db.data[automatic.SETTINGS]['broken'] = broken
        assert await automatic.tick(db) == {'outcome': 'ineligible'}
        assert db.data[automatic.SETTINGS]['broken']['enabled'] is False
        assert db.data[automatic.SETTINGS]['broken']['last_error_code'] == 'operational_automation_configuration_invalid'
        assert (await automatic.tick(db))['outcome'] == 'success'
        assert calls == ['native', 'refresh']
    asyncio.run(scenario())


@pytest.mark.parametrize('receipt', [
    {'status': 'refreshed', 'as_of': None, 'revision': 2},
    {'status': 'refreshed', 'as_of': '2999-01-01T00:00:00+00:00', 'revision': 2},
    {'status': 'refreshed', 'as_of': '2026-11-01T00:00:00+00:00', 'revision': True},
    {'status': 'refreshed', 'as_of': '2026-11-01T00:00:00+00:00', 'revision': 0},
])
def test_invalid_financial_receipt_never_becomes_scheduler_success(receipt):
    import operational_balance_automation as automatic
    assert not automatic._successful_receipt(receipt, None, datetime.now(timezone.utc))
    assert not automatic.status_value({'schema_version': True}, 'owner')['enabled']


def test_failed_prelease_metadata_cleanup_is_bounded(monkeypatch):
    async def scenario():
        import operational_balance_automation as automatic
        db, joined = SchedulerDB(), asyncio.Event()
        await enable(db)
        db.data['users']['owner']['disabled'] = True
        original = SchedulerCollection.update_one
        async def stall(self, query, update, **options):
            if self.name == automatic.SETTINGS and 'run_token' in update['$set']:
                try:
                    await asyncio.Event().wait()
                finally:
                    joined.set()
            return await original(self, query, update, **options)
        monkeypatch.setattr(SchedulerCollection, 'update_one', stall)
        monkeypatch.setattr(automatic, 'METADATA_SECONDS', .02)
        result = await asyncio.wait_for(automatic.tick(db), .5)
        assert result['error_code'] == 'operational_owner_inactive'
        assert joined.is_set() and not db.data[automatic.SERIAL_LEASES]
    asyncio.run(scenario())
