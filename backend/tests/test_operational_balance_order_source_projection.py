"""The refresh reads every owner order while omitting unrelated DTO payloads."""
import asyncio
from copy import deepcopy
from decimal import Decimal

from bson import BSON
import pytest
import pymongo

import operational_balance_sources as sources
from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_financial_position import build_financial_position
from operational_read_runtime import SourceReadError, load_collections, source_read_budget
from test_operational_balance_sources import HardBoundaryDB, NOW, START, order
from test_operational_read_runtime import MotorSourceDB, ThreadedMotorCursor


@pytest.fixture(autouse=True)
def fixed_audit_receipt_clock(monkeypatch):
    # Compare the entire audit evidence, including its receipt clock and seal.
    monkeypatch.setattr('operational_financial_activity.now', lambda: NOW)


class OrderCursor:
    def __init__(self, database, rows, projection):
        self.database, self.rows, self.projection = database, rows, projection

    async def to_list(self, count=None, *, length=None):
        limit = length if length is not None else count
        self.database.order_limits.append(limit)
        rows = deepcopy(self.rows[:limit])
        if self.database.honor_projection and self.projection:
            assert self.projection.get('_id') == 0
            assert all('.' not in field for field in self.projection)
            rows = [{field: value for field, value in row.items()
                     if self.projection.get(field) == 1} for row in rows]
        self.database.order_payloads.append(deepcopy(rows))
        return rows


class OrderCollection:
    def __init__(self, database):
        self.database = database

    def find(self, query, projection=None):
        assert query == {'user_id': 'owner'}
        self.database.order_queries.append((deepcopy(query), deepcopy(projection)))
        self.database.reads.append('unified_orders')
        rows = [row for row in self.database.data.get('unified_orders', [])
                if row.get('user_id') == query['user_id']]
        return OrderCursor(self.database, rows, projection)

    def __getattr__(self, name):
        raise AssertionError('Unexpected order operation: ' + name)


class ProjectionDB(HardBoundaryDB):
    def __init__(self, data=None, *, honor_projection=True):
        super().__init__(deepcopy(data or {}))
        self.honor_projection = honor_projection
        self.order_queries, self.order_limits, self.order_payloads = [], [], []

    def __getitem__(self, name):
        return OrderCollection(self) if name == 'unified_orders' else super().__getitem__(name)


def payload_bytes(rows):
    return sum(len(BSON.encode(row)) for row in rows)


def cohort():
    docs = []
    for index, (created, status) in enumerate((
        ('2026-10-01T01:00:00+00:00', 'in_progress'),
        ('2026-10-06T01:00:00+00:00', 'in_progress'),
        ('2026-10-06T01:00:00+00:00', 'cancelled'),
        ('2026-10-09T01:00:00+00:00', 'delivered'),
    )):
        doc = order(id=str(100 + index), reference_id=str(10 + index),
                    created_at=created, status=status)
        doc.update(order_number=str(10 + index), order_id=str(100 + index),
                   salla_order_id=str(100 + index),
                   unrelated_display_history='synthetic-display-only:' + 'x' * 131072)
        doc['raw_by_source']['other_native_source'] = {'proof': ['retain', {'shape': None}]}
        docs.append(doc)
    foreign = deepcopy(docs[0])
    foreign['user_id'] = 'other'
    return [*docs, foreign]


@pytest.mark.asyncio
async def test_refresh_projects_heavy_order_payload_without_narrowing_owner_cohort():
    documents = cohort()
    database = ProjectionDB({'unified_orders': documents})
    before = deepcopy(database.data)
    await sources.collect_sources(database, 'owner', START, NOW)
    returned, = database.order_payloads
    assert [row['order_id'] for row in returned] == ['100', '101', '102', '103']
    assert database.order_limits == [20001]
    assert database.data == before
    # This fails against the original unprojected collect_sources reader.
    assert payload_bytes(returned) < payload_bytes(documents[:-1]) // 10
    query, projection = database.order_queries[0]
    assert query == {'user_id': 'owner'} and projection['raw_by_source'] == 1
    assert not any('.' in field for field in projection)
    for original, projected in zip(documents[:-1], returned):
        assert projected['raw_by_source'] == original['raw_by_source']
        assert 'unrelated_display_history' not in projected


@pytest.mark.asyncio
async def test_shared_order_reader_and_other_refresh_sources_keep_their_existing_payloads():
    documents = cohort()
    database = ProjectionDB({'unified_orders': documents,
        'mezan_product_cost_profiles_v2': [{'user_id': 'owner', 'base_cost': '20',
                                           'unrelated_field': {'keep': True}}]})
    assert await sources.rows(database, 'owner', 'unified_orders') == documents[:-1]
    assert database.order_queries == [({'user_id': 'owner'}, None)]
    name = 'mezan_product_cost_profiles_v2'
    assert await sources.refresh_source_rows(database, 'owner', name) == database.data[name]


@pytest.mark.asyncio
async def test_ordinary_rows_only_adopts_motor_cursor_ownership_inside_bounded_read_scope():
    document = {'user_id': 'owner', 'raw_by_source': {'retain': True}, 'ordinary_payload': 'keep'}
    ordinary = ThreadedMotorCursor([], block=False)
    ordinary_calls = []

    async def ordinary_to_list(count):
        ordinary_calls.append(count)
        assert pymongo._csot.remaining() is None
        return [deepcopy(document)]

    ordinary.to_list = ordinary_to_list
    outside = MotorSourceDB(ordinary)
    assert await sources.rows(outside, 'owner', 'unified_orders') == [document]
    assert ordinary_calls == [20001] and ordinary.close_calls == 0 and not ordinary.operations
    assert outside.queries == [({'user_id': 'owner'}, None)]

    bounded = ThreadedMotorCursor([[document]], block=False)
    bounded.to_list = ordinary_to_list  # The owned path must not call this.
    inside = MotorSourceDB(bounded)
    assert await load_collections(inside, 'owner', ['unified_orders'], reader=sources.rows) == {
        'unified_orders': [document]}
    assert ordinary_calls == [20001] and bounded.close_calls == 1
    assert bounded.driver_budgets[0] is not None and inside.queries == outside.queries
    assert pymongo._csot.remaining() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [20000, 20001, 20002])
async def test_actual_motor_refresh_retains_exact_row_cap_and_one_overflow_sentinel(count):
    documents = [{'user_id': 'owner', 'raw_by_source': {'canonical': {'malformed': None}}}] * count
    cursor = ThreadedMotorCursor([documents], block=False)
    database = MotorSourceDB(cursor)
    if count == 20000:
        assert await load_collections(database, 'owner', ['unified_orders'],
            reader=sources.refresh_source_rows) == {'unified_orders': documents}
    else:
        with pytest.raises(SourceReadError) as failure:
            await load_collections(database, 'owner', ['unified_orders'], reader=sources.refresh_source_rows)
        assert failure.value.code == 'operational_source_scope_too_large'
    assert cursor.consumed == min(count, 20001)
    assert len(cursor.operations) == cursor.close_calls == len(database.queries) == 1
    assert not cursor.closed_during_read
    assert database.queries[0] == ({'user_id': 'owner'}, {'_id': 0, **{key: 1 for key in sources.REFRESH_ORDER_FIELDS}})


@pytest.mark.asyncio
async def test_actual_motor_refresh_owner_mismatch_still_fails_closed_after_cursor_cleanup():
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}, {'user_id': 'foreign'}]], block=False)
    with pytest.raises(SourceReadError) as failure:
        await load_collections(MotorSourceDB(cursor), 'owner', ['unified_orders'], reader=sources.refresh_source_rows)
    assert (failure.value.code, failure.value.collection) == ('operational_source_owner_mismatch', 'unified_orders')
    assert cursor.close_calls == 1 and not cursor.closed_during_read


@pytest.mark.asyncio
@pytest.mark.parametrize('collection', ['mezan_product_cost_profiles_v2', 'store_delivery_driver_earnings',
                                      'store_delivery_driver_settlements'])
async def test_non_order_primary_reads_use_owned_motor_iteration_with_existing_projection(collection):
    from operational_shipping_fee_review import FIELDS
    document = {'user_id': 'owner', 'proof': {'retain': True}}
    cursor = ThreadedMotorCursor([[document]], block=False)
    database = MotorSourceDB(cursor, collection=collection)
    assert await load_collections(database, 'owner', [collection], reader=sources.refresh_source_rows) == {
        collection: [document]}
    projection = {'_id': 0, **{key: 1 for key in FIELDS[collection]}} if collection in FIELDS else None
    assert database.queries == [({'user_id': 'owner'}, projection)]
    assert cursor.close_calls == 1 and 0 < cursor.driver_budgets[0] <= 15


@pytest.mark.asyncio
@pytest.mark.parametrize('raw_sources', [None, [], {'salla_direct': ['malformed']},
    {'salla_direct': {'unexpected': True}, 'other_source': [None, {'proof': 'retain'}]}])
async def test_projection_keeps_entire_canonical_container_including_malformed_shapes(raw_sources):
    document = {'user_id': 'owner', 'raw_by_source': raw_sources}
    result = await sources.refresh_source_rows(
        ProjectionDB({'unified_orders': [document]}), 'owner', 'unified_orders')
    assert result == [document]


class ReturnedRowsDB:
    """An adversarial boundary that may return too many or foreign rows."""
    def __init__(self, rows):
        self.rows, self.limit = rows, None

    def __getitem__(self, name):
        assert name == 'unified_orders'
        return self

    def find(self, query, projection):
        assert query == {'user_id': 'owner'} and projection['user_id'] == 1
        return self

    async def to_list(self, count):
        self.limit = count
        return deepcopy(self.rows[:count])


@pytest.mark.asyncio
@pytest.mark.parametrize(('documents', 'code'), [
    ([{'user_id': 'owner'}] * 4, 'operational_source_scope_too_large'),
    ([{'user_id': 'owner'}, {'user_id': 'other'}], 'operational_source_owner_mismatch'),
    ([{}], 'operational_source_owner_mismatch'),
])
async def test_refresh_projection_still_fails_closed_at_cap_and_owner_boundaries(monkeypatch, documents, code):
    monkeypatch.setattr(sources, 'MAX_ROWS', 3)
    database = ReturnedRowsDB(documents)
    with pytest.raises(SourceReadError) as failure:
        await load_collections(database, 'owner', ['unified_orders'], reader=sources.refresh_source_rows)
    assert (failure.value.code, failure.value.collection) == (code, 'unified_orders')
    assert database.limit == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(('owner', 'collection'), [('', 'unified_orders'), ('owner', 'users')])
async def test_refresh_projection_rejects_absent_owner_and_forbidden_collection_before_read(owner, collection):
    with pytest.raises(ValueError, match='^operational_source_rejected$'):
        await sources.refresh_source_rows(object(), owner, collection)


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['caller_cancel', 'inherited_deadline'])
async def test_projected_order_read_is_cancelled_and_joined_with_its_existing_budget(stop):
    started, finished = asyncio.Event(), asyncio.Event()

    class WaitingDB(ReturnedRowsDB):
        async def to_list(self, count):
            assert count == 20001
            started.set()
            try:
                await asyncio.Future()
            finally:
                finished.set()

    async def read():
        with source_read_budget(0.03 if stop == 'inherited_deadline' else 1):
            return await load_collections(WaitingDB([]), 'owner', ['unified_orders'],
                                          reader=sources.refresh_source_rows)

    task = asyncio.create_task(read())
    await asyncio.wait_for(started.wait(), timeout=1)
    if stop == 'caller_cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(SourceReadError) as failure:
            await asyncio.wait_for(task, timeout=1)
        assert (failure.value.code, failure.value.collection) == (
            'operational_source_read_timeout', 'unified_orders')
    assert task.done() and finished.is_set()


async def assert_full_and_projected_results_equal(data, prior=None):
    from test_operational_balance_delivery_cutover import active

    previous = deepcopy(prior if prior is not None else active())
    before = deepcopy(previous)
    full_db = ProjectionDB(data, honor_projection=False)
    projected_db = ProjectionDB(data)
    full = await sources.collect_sources(full_db, 'owner', START, NOW, operational_state=previous)
    projected = await sources.collect_sources(projected_db, 'owner', START, NOW, operational_state=previous)
    assert projected == full  # Includes derived proof, contradictions and unresolved issues.
    full_state = reconcile(previous, full, NOW)
    projected_state = reconcile(previous, projected, NOW)
    assert projected_state == full_state
    assert report(projected_state, as_of=NOW) == report(full_state, as_of=NOW)
    assert build_financial_position(projected_state, generated_at=NOW) == build_financial_position(
        full_state, generated_at=NOW)
    assert previous == before and full_db.data == projected_db.data == data
    return projected_state, projected


def parity_fixture(kind):
    from test_operational_balance_delivery_cutover import courier_data, driver_data
    from test_operational_balance_delivery_start_rule import undated
    from test_operational_balance_prepaid_shipping import fixture as prepaid
    from test_operational_balance_source_fixes import shipping_data, rich_rate
    from test_operational_balance_verified_shipping_fees import fixture as driver_fee

    if kind == 'approved_tariff':
        return shipping_data(rich_rate(), payment={'method': 'cod'}, paid_amount='100'), None
    if kind == 'conflicting_cod':
        return shipping_data(payment={'method': 'cod', 'paid_amount': '100'}, paid_amount='200'), None
    if kind == 'native_driver_fee':
        return driver_fee(), None
    if kind == 'driver_collection':
        return driver_data('cash'), None
    if kind == 'dated_courier':
        return courier_data(), None
    if kind == 'undated_delivery':
        return undated(), None
    if kind == 'prepaid_completion':
        return prepaid(old=False)
    if kind == 'completed_capture':
        from test_operational_balance_completed_capture import current_fixture, attach
        data, state = current_fixture()
        attach(data)
        return data, state
    if kind == 'split_tender':
        from test_operational_balance_split_tenders import source
        document, _ = source()
        return {'unified_orders': [document]}, None
    if kind == 'full_owner_cohort':
        return {'unified_orders': cohort()}, None
    if kind == 'shared_waybill_on_future_cancelled_order':
        data = courier_data()
        future = deepcopy(data['unified_orders'][0])
        future.update(order_id='101', salla_order_id='101', order_number='11')
        future['raw_by_source']['salla_direct'].update(
            id='101', reference_id='11', created_at='2026-10-09T01:00:00+00:00', status='cancelled')
        data['unified_orders'].append(future)
        return data, None
    raise AssertionError('Unknown synthetic fixture: ' + kind)


@pytest.mark.parametrize('kind', ['approved_tariff', 'conflicting_cod', 'native_driver_fee',
    'driver_collection', 'dated_courier', 'undated_delivery', 'prepaid_completion',
    'completed_capture', 'split_tender', 'full_owner_cohort', 'shared_waybill_on_future_cancelled_order'])
def test_projected_refresh_preserves_complete_sources_reconciliation_and_financial_reports(kind):
    data, prior = parity_fixture(kind)
    for document in data['unified_orders']:
        document['unrelated_display_history'] = ['not needed for finance'] * 200
        document.setdefault('raw_by_source', {})['another_native_source'] = {'keep_full_proof': True}
    state, collected = asyncio.run(assert_full_and_projected_results_equal(data, prior))
    if kind in {'prepaid_completion', 'completed_capture'}:
        assert [movement['amount'] for movement in state['movements']] == ['34.50']
    elif kind == 'approved_tariff':
        assert collected['orders'][0]['carrier']['cost'] == '12.00'
    elif kind == 'conflicting_cod':
        assert any(issue['code'] == 'cod_collection_contract_incomplete' for issue in collected['issues'])
    elif kind == 'shared_waybill_on_future_cancelled_order':
        assert not collected['delivery_events'] and collected['issues']
    else:
        assert collected['orders'] or collected['delivery_events'] or collected['issues']


@pytest.mark.parametrize('field', ['salla_shipment_id', 'tracking_number', 'shipping_number',
    'shipping_company_code', 'shipping_company', 'shipping_status', 'shipment_status'])
@pytest.mark.parametrize('value', [None, {'unrecognized': 'shape'}])
def test_present_null_or_malformed_shipping_alias_still_blocks_conflicting_delivery(field, value):
    from test_operational_balance_delivery_cutover import courier_data

    data = courier_data()
    data['unified_orders'][0][field] = value
    _, collected = asyncio.run(assert_full_and_projected_results_equal(data))
    assert not collected['delivery_events']
    assert any(issue['code'] == 'operational_delivery_current_shipment_conflict'
               for issue in collected['issues'])


@pytest.mark.parametrize('field', ['merchant', 'merchant_id', 'store_id'])
@pytest.mark.parametrize('value', ['foreign-merchant', {'unrecognized': 'shape'}])
def test_completed_capture_keeps_foreign_and_malformed_merchant_contradictions(field, value):
    from test_operational_balance_completed_capture import current_fixture, attach

    data, prior = current_fixture()
    attach(data)
    data['unified_orders'][0][field] = value
    state, collected = asyncio.run(assert_full_and_projected_results_equal(data, prior))
    assert not state['movements']
    assert any(issue['code'] == 'operational_prepaid_shipping_capture_identity_conflict'
               for issue in collected['issues'])


@pytest.mark.asyncio
@pytest.mark.parametrize('newer_status', [False, True])
async def test_component_pending_keeps_native_payment_version_and_newer_status_veto(newer_status):
    from operational_provider_transaction_evidence import normalize_operational_payment
    from test_operational_balance_payment_source_current import recognize

    changes = {'last_salla_direct_status_source_updated_at': '2026-11-02T08:00:01Z'} if newer_status else {}
    expected, document, raw = recognize(document=changes)
    document['raw_by_source'] = {'salla_direct': raw}
    projected, = await sources.refresh_source_rows(
        ProjectionDB({'unified_orders': [document]}), 'owner', 'unified_orders')
    actual = normalize_operational_payment(raw, 'SAR', '80.00', '2026-11-01T01:00:00Z',
        '2026-11-03T09:00:00Z', owner='owner', order_number='825', provider_order_id='71',
        rows=[], source=projected)
    assert actual == expected
    assert (actual[0] is None) is newer_status
    if not newer_status:
        assert actual[0]['order_page_balance']['paid'] == '80.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('proof_state', ['valid', 'conflicting', 'absent'])
async def test_sar_receipt_is_kept_and_conflicting_proof_cannot_turn_into_fx_fallback(proof_state):
    from operational_customer_payment_sar import value_customer_payment
    from salla_paid_sar_evidence import list_payment_evidence
    from test_operational_balance_paid_sar_acquisition import pair

    native, raw = pair()
    raw['exchange_rate'] = {'base_currency': 'SAR', 'exchange_currency': 'KWD', 'rate': '12'}
    document = {'user_id': 'owner', 'order_id': raw['id'], 'order_number': '1',
                'raw_by_source': {'salla_direct': raw}}
    if proof_state != 'absent':
        document['salla_paid_sar_evidence'] = (
            list_payment_evidence('owner', native) if proof_state == 'valid' else {})
    projected, = await sources.refresh_source_rows(
        ProjectionDB({'unified_orders': [document]}), 'owner', 'unified_orders')
    amounts = {'salla': Decimal('10.00')}
    full = value_customer_payment(raw, document, 'owner', 'KWD', amounts)
    actual = value_customer_payment(raw, projected, 'owner', 'KWD', amounts)
    assert actual == full
    if proof_state == 'conflicting':
        assert actual is None
    else:
        assert (actual['paid'], actual['basis']) == (
            ('121.14', 'salla_list_paid') if proof_state == 'valid' else ('120.00', 'salla_order_fx'))

