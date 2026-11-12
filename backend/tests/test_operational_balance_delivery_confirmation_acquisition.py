"""Saved owner proof avoids only unnecessary date history, within one request."""
import asyncio
from copy import deepcopy

from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import pytest

from operational_balance_archive import compact_financial_snapshot
from operational_balance_store import STATES, digest
from operational_delivery_confirmation import PREFIX, record_confirmation
from operational_demand_refresh import LEASES, run_visible_refresh
import operational_visible_evidence as acquisition
from salla_integration import courier_delivery_sync
from test_operational_balance_demand_refresh import DB, Request, allowed
from test_operational_balance_delivery_confirmation import data_for, confirmed, candidate, NOW, START, LATER, row


class LocalProofDB(DB):
    def __init__(self):
        super().__init__()
        self.sources = AsyncMongoMockClient()['manual_date_acquisition']
        self.local_reads = []

    def __getitem__(self, name):
        if name in {STATES, LEASES}:
            return super().__getitem__(name)
        self.local_reads.append(name)
        return self.sources[name]


async def prepared(*, packed=False):
    db = LocalProofDB()
    data = data_for()
    state, _ = confirmed(data)
    db.data[STATES]['owner'].update(state)
    db.data[STATES]['owner']['engine']['issues'] = [{'code': 'operational_delivery_timestamp_missing', 'source_id': '100'}]
    if packed:
        db.data[STATES]['owner'] = compact_financial_snapshot(db.data[STATES]['owner'], 100000)
    for name, documents in data.items():
        if documents:
            await db.sources[name].insert_many(deepcopy(documents))
    return db


@pytest.mark.parametrize('packed', [False, True])
def test_valid_saved_manual_proof_is_revalidated_locally_without_native_history(packed, monkeypatch):
    async def scenario():
        db, calls = await prepared(packed=packed), []
        before = deepcopy(db.data[STATES])
        async def native(*args, **kwargs):
            calls.append(True)
        monkeypatch.setattr(courier_delivery_sync, 'acquire_missing', native)
        result = await run_visible_refresh(Request(), db, 'owner', 'owner', allowed,
            lambda: acquisition.acquire_visible_evidence(db, 'owner'))
        assert not calls and result['status'] == 'attempted'
        assert 'order_activity_events_v2' in db.local_reads and 'store_delivery_assignments' in db.local_reads
        assert db.data[STATES] == before and db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['executor', 'negative_history', 'other_gap'])
def test_changed_proof_or_other_gap_retains_native_acquisition(change, monkeypatch):
    async def scenario():
        db, calls = await prepared(), []
        if change == 'executor':
            await db.sources.unified_orders.update_one({'user_id': 'owner'}, {'$set': {'salla_shipping_current.company_code': 'other'}})
        elif change == 'negative_history':
            await db.sources.order_activity_events_v2.insert_one(row(order_number='10', provider_order_id='100',
                source='salla_history', source_event_id='negative', native_status_transition=[{'delivered': False, 'at': NOW}]))
        else:
            db.data[STATES]['owner']['engine']['issues'].append({'code': 'operational_delivery_shipment_source_missing', 'source_id': '100'})
        async def native(db, owner, pending, **options):
            calls.append((deepcopy(pending), options))
        monkeypatch.setattr(courier_delivery_sync, 'acquire_missing', native)
        await run_visible_refresh(Request(), db, 'owner', 'owner', allowed,
            lambda: acquisition.acquire_visible_evidence(db, 'owner'))
        assert len(calls) == 1 and calls[0][1]['limit'] == 4
        assert {row['source_id'] for row in calls[0][0]} == {'100'}
        if change == 'other_gap':
            assert not db.local_reads
    asyncio.run(scenario())


def test_local_manual_rechecks_share_existing_four_candidate_bound(monkeypatch):
    import operational_delivery_confirmation_sources as sources
    async def scenario():
        db, seen, native = await prepared(), [], []
        state = db.data[STATES]['owner']
        event = candidate(data_for())
        for index in range(1, 7):
            value = {**event, 'order_id': str(index), 'order_number': str(index + 10)}
            state['engine']['facts'][PREFIX + str(index)] = record_confirmation(state, value,
                actor='owner', actor_name='Synthetic', confirmed_at=NOW, source_hash=digest(index), preview_hash=digest('preview'))
            state['engine']['issues'].append({'code': 'operational_delivery_timestamp_missing', 'source_id': str(index)})
        async def read_candidate(db, owner, state, payload, as_of, **kwargs):
            seen.append(payload['order_id'])
            return event, digest('source')
        async def acquire(db, owner, pending, **kwargs):
            native.extend(pending)
        monkeypatch.setattr(sources, 'read_candidate', read_candidate)
        monkeypatch.setattr(courier_delivery_sync, 'acquire_missing', acquire)
        await run_visible_refresh(Request(), db, 'owner', 'owner', allowed,
            lambda: acquisition.acquire_visible_evidence(db, 'owner'))
        assert len(seen) == len(set(seen)) == 4 and len(native) == 3
    asyncio.run(scenario())


@pytest.mark.parametrize('exit_kind', ['disconnect', 'budget'])
def test_local_proof_revalidation_is_cancelled_and_joined_with_no_native_work(exit_kind, monkeypatch):
    import operational_delivery_confirmation_sources as sources
    if exit_kind == 'budget':
        monkeypatch.setattr(acquisition, 'ACQUISITION_SECONDS', .02)
    async def scenario():
        db, request, entered, stopped, native = await prepared(), Request(), asyncio.Event(), asyncio.Event(), []
        async def read_candidate(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        async def acquire(*args, **kwargs):
            native.append(True)
        monkeypatch.setattr(sources, 'read_candidate', read_candidate)
        monkeypatch.setattr(courier_delivery_sync, 'acquire_missing', acquire)
        work = asyncio.create_task(run_visible_refresh(request, db, 'owner', 'owner', allowed,
            lambda: acquisition.acquire_visible_evidence(db, 'owner')))
        await entered.wait()
        if exit_kind == 'disconnect':
            request.disconnect()
            with pytest.raises(HTTPException) as failed:
                await work
            assert failed.value.status_code == 499
        else:
            assert (await work)['reason'] == 'native_evidence_budget_exhausted'
        assert stopped.is_set() and not native and db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_new_local_proof_collections_expose_no_write_capability():
    db = LocalProofDB()
    guarded = acquisition._EvidenceDatabase(db)
    for name in acquisition._LOCAL_PROOF_READS:
        for operation in ('update_one', 'find_one_and_update', 'delete_one', 'insert_one'):
            with pytest.raises(AttributeError):
                getattr(guarded[name], operation)
