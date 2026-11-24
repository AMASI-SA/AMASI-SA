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
from operational_provider_capture_batches import FIELD as BATCH_FIELD, PENDING_FIELD, PENDING_CODE, build_batch, expand_batches
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
        if self.name == 'unified_orders':
            assert not upsert and update == {'$set': {PENDING_FIELD: None}}
            self.db.writes.append((self.name, 'complete', deepcopy(query)))
            row = next((row for row in self.db.sources[self.name] if matches(row, query)), None)
            if row is not None: row.update(update['$set'])
            return SimpleNamespace(matched_count=int(row is not None))
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
        proof = expand_batches(db.sources[COLLECTION])[0]['native_transaction_evidence']
        assert proof[0]['paid_at'] == CAPTURED and proof[0]['provider_order_id'] == '1001'
        assert adapter._RETRY not in foreign
        await run(db)
        assert len(calls) == 1  # Retry admission is shared across request lifetimes.
        db.sources['unified_orders'][1].pop(adapter._RETRY)
        await run(db)
        assert len(calls) == 2 and len(db.sources[COLLECTION]) == 1
        assert expand_batches(db.sources[COLLECTION])[0]['native_transaction_evidence'] == proof
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


@pytest.mark.parametrize('publication', ['before_commit', 'after_commit', 'complete'])
@pytest.mark.parametrize('cached_positive', [False, True])
def test_negative_last_is_never_omitted_by_partial_or_uncertain_publication(monkeypatch, publication, cached_positive):
    from operational_balance_engine import reconcile
    from operational_provider_transaction_evidence import normalize_operational_payment
    from order_activity_v1 import normalize_transaction_row
    from test_operational_balance_provider_original_dates import NOW, order, claim, KEY

    async def scenario():
        db = SourceDB()
        initial = deepcopy(db.data[STATES]['owner'])
        initial['engine']['obligations'] = {KEY + ':tabby': {**claim('65.00', '0.00', '2026-11-22'), 'id': KEY + ':tabby'}}
        second = native(txn='native-2')
        second.update(payment_method={'slug': 'mada'}, total={'amount': '35.00', 'currency': 'SAR'},
            references={'order_id': '1001', 'transaction': 'ref-B'})
        negative = native(txn='native-refund', status='refunded')
        raw_rows = [native(), second, negative]
        if cached_positive:
            saved = normalize_transaction_row(native(), order_number='88001', provider_order_id='1001')
            saved.update(user_id='owner', native_transaction_evidence=[saved['native_transaction_evidence']])
            db.sources[COLLECTION].append(saved)
        async def fetch(*args, **kwargs): return page(raw_rows)
        monkeypatch.setattr(courier_delivery_sync, 'call_salla', fetch)
        original_write = SourceCollection.update_one
        publications = []
        async def interrupt(self, query, update, **options):
            if self.name == COLLECTION:
                publications.append(update)
                assert len(update['$setOnInsert'][BATCH_FIELD]['rows']) == 3
                if publication == 'before_commit': raise TimeoutError()
                result = await original_write(self, query, update, **options)
                if publication == 'after_commit': raise TimeoutError()
                return result
            return await original_write(self, query, update, **options)
        monkeypatch.setattr(SourceCollection, 'update_one', interrupt)
        result = await run(db)
        assert len(publications) == 1  # No first/second positive write exists separately.
        source = db.sources['unified_orders'][0]
        raw = source['raw_by_source']['salla_direct']
        payment, problems = normalize_operational_payment(raw, 'SAR', '100.00', CREATED, NOW,
            owner='owner', order_number='88001', provider_order_id='1001', rows=db.sources[COLLECTION], source=source)
        assert payment is None and problems
        assert problems[0]['code'] == (PENDING_CODE if publication != 'complete' else 'provider_transaction_binding_conflict')
        if publication != 'complete':
            assert result['provider']['status'] == 'deferred' and source[PENDING_FIELD]
        else:
            assert source[PENDING_FIELD] is None
        final = reconcile(initial, {'orders': [order(payment)], 'issues': problems}, NOW)
        assert final['engine']['obligations'] == initial['engine']['obligations']
        assert final['movements'] == initial['movements']
        batches = [row for row in db.sources[COLLECTION] if BATCH_FIELD in row]
        assert len(batches) == (0 if publication == 'before_commit' else 1)
    asyncio.run(scenario())


@pytest.mark.parametrize('fault', ['digest', 'count', 'incomplete', 'owner', 'order', 'nested'])
def test_corrupt_atomic_batch_blocks_prior_cached_positive_instead_of_ignoring_it(fault):
    from operational_provider_transaction_evidence import normalize_operational_payment
    from order_activity_v1 import normalize_transaction_row
    from test_operational_balance_provider_original_dates import NOW
    doc, raw = split_source()
    value = normalize_transaction_row(native(), order_number='88001', provider_order_id='1001')
    batch = build_batch([value], owner='owner', order_number='88001', provider_order_id='1001')
    body = batch[BATCH_FIELD]
    if fault == 'digest': body['digest'] = '0' * 64
    elif fault == 'count': body['count'] = 2
    elif fault == 'incomplete': body['complete'] = False
    elif fault == 'owner': body['owner_id'] = 'foreign'
    elif fault == 'order': body['provider_order_id'] = '999'
    else: body['rows'][0][BATCH_FIELD] = {}
    saved = {**value, 'user_id': 'owner', 'native_transaction_evidence': [value['native_transaction_evidence']]}
    payment, problems = normalize_operational_payment(raw, 'SAR', '100.00', CREATED, NOW,
        owner='owner', order_number='88001', provider_order_id='1001', rows=[saved, batch], source=doc)
    assert payment is None and problems[0]['code'] == 'provider_transaction_binding_conflict'


def test_activity_display_excludes_internal_batches_without_exposing_envelope():
    from order_activity_v1 import read_order_activity
    class Cursor:
        def sort(self, *args): return self
        async def to_list(self, **kwargs): return []
    class Collection:
        def __init__(self, name): self.name = name
        def find(self, query, projection):
            if self.name == COLLECTION:
                assert query == {'user_id': 'owner', 'order_number': '88001', BATCH_FIELD: {'$exists': False}}
            return Cursor()
    class DisplayDB:
        def __getattr__(self, name): return Collection(name)
    result = asyncio.run(read_order_activity(DisplayDB(), user_id='owner', order_number='88001'))
    assert result['payments'] == [] and result['payments_count'] == 0


def test_atomic_batch_is_order_independent_and_expands_only_within_remaining_row_limit():
    from order_activity_v1 import normalize_transaction_row
    rows = [normalize_transaction_row(native(txn='native-'+str(index)), order_number='88001', provider_order_id='1001')
        for index in range(3)]
    one = build_batch(rows, owner='owner', order_number='88001', provider_order_id='1001')
    assert one == build_batch(list(reversed(rows)), owner='owner', order_number='88001', provider_order_id='1001')
    assert len(expand_batches([one], max_rows=3)) == 3
    with pytest.raises(ValueError, match='operational_source_scope_too_large'):
        expand_batches([one], max_rows=2)
    with pytest.raises(ValueError, match='operational_source_scope_too_large'):
        expand_batches([{}, one], max_rows=3)


def test_large_untrusted_batch_is_bounded_and_fails_closed():
    from order_activity_v1 import normalize_transaction_row
    from operational_provider_capture_batches import MAX_BYTES
    row = normalize_transaction_row(native(), order_number='88001', provider_order_id='1001')
    batch = build_batch([row], owner='owner', order_number='88001', provider_order_id='1001')
    batch[BATCH_FIELD]['rows'][0]['untrusted'] = 'x' * (MAX_BYTES+1)
    expanded = expand_batches([batch], max_rows=1)
    assert len(expanded) == 1 and expanded[0]['native_transaction_evidence'] == []
    with pytest.raises(ValueError, match='provider_native_batch_limit'):
        build_batch([{**row, 'untrusted': 'x'*(MAX_BYTES+1)}], owner='owner', order_number='88001', provider_order_id='1001')
