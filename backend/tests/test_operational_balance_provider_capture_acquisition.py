"""Authenticated, bounded provider evidence acquisition never writes the ledger."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from operational_balance_store import STATES
from operational_demand_refresh import LEASES, run_visible_refresh
from operational_provider_dates import UNPROVEN
from operational_provider_transaction_evidence import COLLECTION
import operational_visible_evidence as visible
from salla_integration import courier_delivery_sync, provider_capture_sync as adapter
from test_operational_balance_demand_refresh import DB, Request, allowed
from test_operational_balance_provider_original_dates import CAPTURED, CREATED, split_source


def get(row, dotted):
    for part in dotted.split('.'):
        row = row.get(part) if isinstance(row, dict) else None
    return row


def matches(row, query):
    for key, value in query.items():
        if key == '$or':
            if not any(matches(row, sub) for sub in value): return False
        elif isinstance(value, dict) and any(k.startswith('$') for k in value):
            actual = get(row, key)
            if '$in' in value and actual not in value['$in']: return False
            if '$exists' in value and (actual is not None) != value['$exists']: return False
            if '$lte' in value and (actual is None or actual > value['$lte']): return False
        elif get(row, key) != value:
            return False
    return True


class SourceCollection:
    def __init__(self, db, name): self.db, self.name = db, name

    async def find_one(self, query, projection=None):
        return deepcopy(next((row for row in self.db.sources[self.name] if matches(row, query)), None))

    async def find_one_and_update(self, query, update, **options):
        self.db.writes.append((self.name, 'retry', deepcopy(query)))
        row = next((row for row in self.db.sources[self.name] if matches(row, query)), None)
        if row is None: return None
        previous = deepcopy(row)
        row.update(update['$set'])
        return previous

    async def update_one(self, query, update, *, upsert=False):
        assert self.name == COLLECTION and upsert
        self.db.writes.append((self.name, 'proof', deepcopy(query)))
        row = next((row for row in self.db.sources[self.name] if matches(row, query)), None)
        if row is None:
            row = deepcopy(update['$setOnInsert'])
            self.db.sources[self.name].append(row)
        for key, value in update.get('$addToSet', {}).items():
            if value not in row.setdefault(key, []): row[key].append(deepcopy(value))
        row.update(deepcopy(update['$set']))
        return SimpleNamespace(matched_count=1)


class SourceDB(DB):
    def __init__(self, count=1):
        super().__init__()
        self.sources = {'unified_orders': [], COLLECTION: []}
        for n in range(count):
            doc, raw = split_source()
            identity, number = str(1001+n), str(88001+n)
            doc.update(salla_order_id=identity, order_number=number)
            raw.update(id=identity, reference_id=number)
            self.sources['unified_orders'].append(doc)
        self.data[STATES]['owner']['engine'] = {'issues': [
            {'code': UNPROVEN, 'source_id': 'provider:'+str(1001+n)+':tabby', 'party_type': 'provider', 'party_id': 'tabby'}
            for n in range(count)]}

    def __getitem__(self, name):
        return SourceCollection(self, name) if name in self.sources else super().__getitem__(name)

    def __getattr__(self, name):
        if name in self.sources: return self[name]
        raise AttributeError(name)


def native(identity='1001', *, paid_at=CAPTURED, bound=True, status='captured', txn='native-1'):
    return {'id': txn, 'references': {'transaction': 'ref-A', **({'order_id': identity} if bound else {})},
        'payment_method': {'slug': 'tabby_installment'}, 'status': {'slug': status},
        'total': {'amount': '65.00', 'currency': 'SAR'}, 'paid_at': paid_at, 'created_at': CREATED}


def page(rows):
    return {'success': True, 'data': rows, 'pagination': {'total': len(rows), 'totalPages': 1, 'currentPage': 1}}


async def run(db, verify=allowed):
    return await run_visible_refresh(Request(), db, 'owner', 'owner', verify,
        lambda: visible.acquire_visible_evidence(db, 'owner'))


def test_owner_order_bound_source_only_and_idempotent_native_append(monkeypatch):
    async def scenario():
        db, calls = SourceDB(), []
        original = deepcopy(db.data[STATES])
        foreign = deepcopy(db.sources['unified_orders'][0]); foreign['user_id'] = 'foreign'
        db.sources['unified_orders'].insert(0, foreign)
        async def fetch(guarded, owner, method, path, *, params):
            assert owner == 'owner' and method == 'GET' and path == '/transactions'
            assert params == {'order_id': '1001', 'page': 1, 'per_page': 100}
            calls.append(path)
            return page([native()])
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        first = await run(db)
        assert first['provider']['status'] == 'completed' and first['provider']['selected'] == 1
        assert db.data[STATES] == original and db.data[LEASES] == {}
        assert len(db.sources[COLLECTION]) == 1
        proof = db.sources[COLLECTION][0]['native_transaction_evidence']
        assert proof[0]['paid_at'] == CAPTURED and proof[0]['provider_order_id'] == '1001'
        assert adapter._RETRY not in foreign
        await run(db)
        assert len(calls) == 1  # Retry admission is shared across request lifetimes.
        db.sources['unified_orders'][1].pop(adapter._RETRY)
        await run(db)
        assert len(calls) == 2 and len(db.sources[COLLECTION]) == 1
        assert db.sources[COLLECTION][0]['native_transaction_evidence'] == proof
        assert not any(name == STATES for name, *_ in db.writes)
    asyncio.run(scenario())


def test_limit_four_and_only_saved_original_date_holds_are_eligible(monkeypatch):
    async def scenario():
        db, calls = SourceDB(6), []
        async def fetch(guarded, owner, method, path, *, params):
            calls.append(params['order_id']); return page([native(params['order_id'])])
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        result = await run(db)
        assert result['provider']['selected'] == 4 and len(calls) == 4
        await run(db)
        assert len(calls) == 6 and len(set(calls)) == 6
        db.data[STATES]['owner']['engine']['issues'] = [{'code': 'unrelated', 'source_id': 'provider:9999'},
            {'code': UNPROVEN, 'source_id': 'provider:../../secrets'}]
        await run(db)
        assert len(calls) == 6
    asyncio.run(scenario())


def test_acquired_bound_native_proof_unlocks_only_original_day_provider_claims(monkeypatch):
    from operational_balance_engine import reconcile
    from operational_provider_transaction_evidence import normalize_operational_payment
    from test_operational_balance_provider_original_dates import NOW, order

    async def scenario():
        db = SourceDB()
        async def fetch(*args, **kwargs):
            second = native(txn='native-2')
            second.update(payment_method={'slug': 'mada'}, total={'amount': '35.00', 'currency': 'SAR'},
                          references={'order_id': '1001', 'transaction': 'ref-B'})
            return page([native(), second])
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        before = deepcopy(db.data[STATES]['owner'])
        await run(db)
        source = db.sources['unified_orders'][0]
        raw = source['raw_by_source']['salla_direct']
        payment, problems = normalize_operational_payment(raw, 'SAR', '100.00', CREATED, NOW,
            owner='owner', order_number='88001', provider_order_id='1001', rows=db.sources[COLLECTION], source=source)
        assert not problems
        result = reconcile(before, {'orders': [order(payment)]}, NOW)
        claims = result['engine']['obligations']
        assert {row['party_id']: (row['confirmed'], row['business_date']) for row in claims.values()} == {
            'tabby': ('65.00', '2026-11-22'), 'salla': ('35.00', '2026-11-22')}
        assert db.data[STATES]['owner'] == before  # Acquisition itself never posted these claims.
        assert reconcile(result, {'orders': [order(payment)]}, NOW)['engine']['obligations'] == claims
    asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['missing_pagination', 'too_many_pages', 'wrong_total', 'repeated_id', 'empty'])
def test_incomplete_native_history_never_saves_a_selected_subset(monkeypatch, fault):
    async def scenario():
        db = SourceDB()
        async def fetch(*args, **kwargs):
            response = page([native()])
            if fault == 'missing_pagination': response.pop('pagination')
            elif fault == 'too_many_pages': response['pagination']['totalPages'] = 4
            elif fault == 'wrong_total': response['pagination']['total'] = 2
            elif fault == 'repeated_id': response['data'] *= 2; response['pagination']['total'] = 2
            else: response = page([])
            return response
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        result = await run(db)
        assert result['provider']['status'] == 'partial'
        assert db.sources[COLLECTION] == []
        assert db.data[STATES]['owner']['engine']['issues'][0]['code'] == UNPROVEN
    asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['created_only', 'unbound', 'refund'])
def test_transaction_creation_unbound_or_negative_observation_never_claims_a_capture_date(monkeypatch, fault):
    async def scenario():
        db = SourceDB()
        async def fetch(*args, **kwargs):
            return page([native(paid_at=None if fault == 'created_only' else CAPTURED,
                bound=fault != 'unbound', status='refunded' if fault == 'refund' else 'paid')])
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        result = await run(db)
        assert result['provider']['status'] == 'partial'
        assert result['provider']['outcomes'][0]['reason'] == 'provider_native_capture_time_missing'
        assert len(db.sources[COLLECTION]) == 1  # Negative provenance is retained, never filtered away.
        assert db.data[STATES]['owner']['engine']['issues'][0]['code'] == UNPROVEN
    asyncio.run(scenario())


def test_source_version_change_during_native_read_withholds_all_new_observations(monkeypatch):
    async def scenario():
        db = SourceDB()
        async def fetch(*args, **kwargs):
            db.sources['unified_orders'][0]['raw_by_source']['salla_direct']['updated_at'] = CAPTURED
            return page([native()])
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        result = await run(db)
        assert result['provider']['outcomes'][0]['status'] == 'source_changed'
        assert db.sources[COLLECTION] == []
    asyncio.run(scenario())


def test_authority_revocation_after_native_read_prevents_source_and_financial_writes(monkeypatch):
    async def scenario():
        db, revoked = SourceDB(), False
        before = deepcopy(db.data[STATES])
        async def verify():
            if revoked: raise HTTPException(403, detail={'code': 'operational_actor_inactive'})
        async def fetch(*args, **kwargs):
            nonlocal revoked
            revoked = True
            return page([native()])
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        with pytest.raises(HTTPException) as failure:
            await run(db, verify)
        assert failure.value.status_code == 403
        assert db.sources[COLLECTION] == [] and db.data[STATES] == before and db.data[LEASES] == {}
    asyncio.run(scenario())


def test_shared_budget_cancels_native_provider_read_and_keeps_unknown_financial_result(monkeypatch):
    async def scenario():
        db, stopped = SourceDB(), asyncio.Event()
        before = deepcopy(db.data[STATES])
        async def fetch(*args, **kwargs):
            try: await asyncio.Event().wait()
            finally: stopped.set()
        monkeypatch.setattr(visible, 'ACQUISITION_SECONDS', .02)
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        result = await run(db)
        assert result['status'] == 'deferred' and result['provider']['status'] == 'deferred'
        assert stopped.is_set() and db.sources[COLLECTION] == [] and db.data[STATES] == before
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())
