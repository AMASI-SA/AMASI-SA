"""Cost previews retain their complete evidence while omitting unused DTO payloads."""
import asyncio
from copy import deepcopy

import pytest
import pymongo

import operational_open_cost_exposure as exposure
import operational_balance_sources as sources
from operational_financial_position_core import build_financial_position
from operational_read_runtime import SourceReadError, load_collections
from test_operational_balance_order_source_projection import ProjectionDB, payload_bytes
from test_operational_balance_open_customer_cod import order as cod_order
from test_operational_balance_prestart_cod_forecast import proved as courier_order
from test_operational_open_cost_exposure import NOW, native_order, operating_state, seeded_db
from test_operational_read_runtime import MotorSourceDB, ThreadedMotorCursor


def cost_cohort():
    documents = []
    for number, created, status in (
        ('OLD', '2026-10-30T10:00:00+00:00', 'in_progress'),
        ('NEW', '2026-11-01T10:00:00+00:00', 'in_progress'),
        ('CLOSED', '2026-11-01T10:00:00+00:00', 'delivered'),
        ('FUTURE', '2026-11-03T10:00:00+00:00', 'in_progress'),
    ):
        document = native_order(number, created=created, status=status)
        document.update(order_id='salla-' + number, salla_order_id='salla-' + number,
                        unrelated_display_history='synthetic-unused:' + 'x' * 131072)
        document['raw_by_source']['other_native_source'] = {'shape': [None, {'retain': True}]}
        documents.append(document)
    foreign = deepcopy(documents[0])
    foreign['user_id'] = 'another-owner'
    return [*documents, foreign]


@pytest.fixture(autouse=True)
def isolated_cost_cache():
    exposure._READ_CACHE.clear()
    yield
    exposure._READ_CACHE.clear()


@pytest.mark.asyncio
async def test_cost_read_omits_unused_payload_without_narrowing_the_owner_cohort():
    documents = cost_cohort()
    data = seeded_db().data
    data['unified_orders'] = documents
    database = ProjectionDB(data)
    state = operating_state()
    before = deepcopy((database.data, state))

    await exposure.project(database, 'owner', state, as_of=NOW)

    returned, = database.order_payloads
    assert [row['order_id'] for row in returned] == [
        'salla-OLD', 'salla-NEW', 'salla-CLOSED', 'salla-FUTURE']
    assert database.order_limits == [20001]
    assert before == (database.data, state)
    # This assertion detects the existing unnecessary source transport, without
    # substituting a machine-dependent elapsed-time assertion for the defect.
    assert payload_bytes(returned) < payload_bytes(documents[:-1]) // 10
    query, projection = database.order_queries[0]
    assert query == {'user_id': 'owner'}
    assert projection['raw_by_source'] == 1 and not any('.' in key for key in projection)
    for original, projected in zip(documents[:-1], returned):
        assert projected['raw_by_source'] == original['raw_by_source']
        assert 'unrelated_display_history' not in projected


async def equal_cost_and_financial_outputs(data, *, as_of=NOW):
    state = {**operating_state(), 'openings': {}, 'movements': []}
    outcomes, reports = [], []
    for honor_projection in (False, True):
        database = ProjectionDB(data, honor_projection=honor_projection)
        before = deepcopy((database.data, state))
        try:
            result = await exposure.project(database, 'owner', state, as_of=as_of)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            outcomes.append(('error', type(error), error.args))
        else:
            outcomes.append(('result', result))
            reports.append(build_financial_position(state, cost_exposure=result, generated_at=NOW))
        assert before == (database.data, state)
        assert database.order_limits == [20001]
        assert database.order_queries[0][0] == {'user_id': 'owner'}
    assert outcomes[0] == outcomes[1]
    if reports:
        assert len(reports) == 2 and reports[0] == reports[1]
    return outcomes[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('as_of', [NOW, '2026-11-02T00:00:01+00:00'])
async def test_full_outputs_match_for_saved_snapshot_forecast_and_legacy_read(as_of):
    data = seeded_db().data
    data['unified_orders'] = cost_cohort() + [cod_order(), courier_order('COURIER', currency='QAR')]
    kind, result = await equal_cost_and_financial_outputs(data, as_of=as_of)
    assert kind == 'result' and result['status'] == 'preview'


@pytest.mark.asyncio
async def test_projection_does_not_change_shared_order_reader_or_other_cost_sources():
    data = seeded_db().data
    data['unified_orders'] = cost_cohort()
    for name in exposure.READS[1:]:
        data[name] = [{'user_id': 'owner', 'unrelated_field': {'keep': [None, False, 'whole']}}]
    database = ProjectionDB(data)
    assert await sources.rows(database, 'owner', 'unified_orders') == data['unified_orders'][:-1]
    assert database.order_queries == [({'user_id': 'owner'}, None)]
    for name in exposure.READS[1:]:
        assert await exposure._cost_source_rows(database, 'owner', name) == data[name]


# Each of these independent pieces of outer evidence excludes an otherwise
# valid customer COD estimate. Dropping one can incorrectly invent a known due.
NEGATIVE_EVIDENCE = [
    ('store_delivery_assignment_id', 'conflicting-assignment'),
    ('store_delivery_driver_id', 'conflicting-driver'),
    ('store_delivery_status', 'delivered'),
    ('store_delivery_payment_status', 'paid'),
    ('store_delivery_delivered_at', '2026-11-01T15:00:00+00:00'),
    ('store_delivery_collection_amount', '1.00'),
    ('delivered_at', '2026-11-01T15:00:00+00:00'),
    ('archived', True), ('superseded', True), ('returned', True),
    ('deleted', True), ('is_deleted', True), ('status', 'returned'), ('type', 'return'),
    ('shipment', {'id': 'old-shipment'}), ('superseded_shipment_ids', ['old-shipment']),
    ('source_kind', 'shipment'), ('tracking_url', 'synthetic-tracking'),
    ('shipping_label_url', 'synthetic-label'), ('shipment_id', 'old-shipment'),
    ('waybill', 'old-waybill'), ('awb', 'old-waybill'), ('awb_number', 'old-waybill'),
    ('tracking', {'number': 'old-waybill'}), ('label_url', 'synthetic-label'),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(('field', 'value'), NEGATIVE_EVIDENCE)
async def test_outer_negative_evidence_never_promotes_a_customer_due(field, value):
    clean = cod_order()
    clean_kind, clean_result = await equal_cost_and_financial_outputs(seeded_db(order_row=clean).data)
    assert clean_kind == 'result'
    assert clean_result['open_customer_cod']['by_currency']['SAR']['rows'][0]['amount'] == '77.00'
    clean[field] = deepcopy(value)
    kind, result = await equal_cost_and_financial_outputs(seeded_db(order_row=clean).data)
    assert kind == 'result' and result['open_customer_cod']['by_currency'] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('alias', ['order_number', 'salla_order_id'])
async def test_duplicate_outer_identity_without_raw_still_withholds_customer_forecast(alias):
    document = cod_order()
    data = seeded_db(order_row=document).data
    data['unified_orders'].append({'user_id': 'owner', alias: document[alias]})
    kind, result = await equal_cost_and_financial_outputs(data)
    assert kind == 'result' and result['open_customer_cod']['by_currency'] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['raw_by_source', 'g47_salla_snapshot', 'salla_shipping_current'])
@pytest.mark.parametrize('value', [None, False, 0, '', [], ['malformed'], {'unexpected': [None, False]}])
async def test_whole_evidence_containers_keep_falsy_and_malformed_shapes(field, value):
    document = cod_order()
    document[field] = deepcopy(value)
    data = seeded_db(order_row=document).data
    await equal_cost_and_financial_outputs(data)
    database = ProjectionDB(data)
    rows = await exposure._cost_source_rows(database, 'owner', 'unified_orders')
    assert field in rows[0] and rows[0][field] == value
    assert type(rows[0][field]) is type(value)


@pytest.mark.asyncio
@pytest.mark.parametrize('currency', ['SAR', 'QAR'])
async def test_courier_forecast_keeps_nested_shipment_payment_and_currency_evidence(currency):
    document = courier_order(currency=currency)
    kind, result = await equal_cost_and_financial_outputs(seeded_db(order_row=document).data)
    assert kind == 'result'
    assert result['prestart_open_cod']['by_currency'] == {currency: {'orders': 1, 'amount': '77.00'}}
    document['salla_shipping_current']['superseded_shipment_ids'] = [
        document['salla_shipping_current']['shipment_id']]
    kind, result = await equal_cost_and_financial_outputs(seeded_db(order_row=document).data)
    assert kind == 'result' and result['prestart_open_cod']['by_currency'] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [20000, 20001, 20002])
async def test_cost_source_keeps_motor_owner_scope_and_original_overflow_sentinel(count):
    documents = [{'user_id': 'owner', 'raw_by_source': {'malformed': None}}] * count
    cursor = ThreadedMotorCursor([documents], block=False)
    database = MotorSourceDB(cursor)
    if count == 20000:
        assert await load_collections(database, 'owner', ['unified_orders'],
            reader=exposure._cost_source_rows) == {'unified_orders': documents}
    else:
        with pytest.raises(SourceReadError) as failure:
            await load_collections(database, 'owner', ['unified_orders'], reader=exposure._cost_source_rows)
        assert (failure.value.code, failure.value.collection) == (
            'operational_source_scope_too_large', 'unified_orders')
    assert cursor.consumed == min(count, 20001)
    assert len(cursor.operations) == cursor.close_calls == len(database.queries) == 1
    assert not cursor.closed_during_read
    query, projection = database.queries[0]
    assert query == {'user_id': 'owner'} and projection['user_id'] == 1
    assert projection['raw_by_source'] == 1 and projection['_id'] == 0
    assert 0 < cursor.driver_budgets[0] <= 15 and pymongo._csot.remaining() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_owner', [{'user_id': 'foreign'}, {}])
async def test_cost_source_rejects_returned_foreign_or_missing_owner_after_cursor_cleanup(invalid_owner):
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}, invalid_owner]], block=False)
    with pytest.raises(SourceReadError) as failure:
        await load_collections(MotorSourceDB(cursor), 'owner', ['unified_orders'],
                               reader=exposure._cost_source_rows)
    assert (failure.value.code, failure.value.collection) == ('operational_source_owner_mismatch', 'unified_orders')
    assert cursor.close_calls == 1 and not cursor.closed_during_read


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['caller_cancel', 'reader_deadline'])
async def test_cost_cursor_joins_active_motor_worker_before_failure_or_cancellation(stop):
    cursor = ThreadedMotorCursor([[{'user_id': 'owner'}], [{'user_id': 'owner'}]])
    database = MotorSourceDB(cursor)
    task = asyncio.create_task(load_collections(database, 'owner', ['unified_orders'],
        reader=exposure._cost_source_rows, per_read_timeout=.03 if stop == 'reader_deadline' else 1))
    try:
        await asyncio.wait_for(cursor.entered.wait(), 1)
        if stop == 'caller_cancel':
            task.cancel()
            for _ in range(12):
                await asyncio.sleep(0)
        else:
            await asyncio.sleep(.05)
        assert not cursor.worker_finished.is_set() and not task.done()
        cursor.release.set()
        if stop == 'caller_cancel':
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(SourceReadError) as failure:
                await task
            assert (failure.value.code, failure.value.collection) == (
                'operational_source_read_timeout', 'unified_orders')
        assert cursor.worker_finished.is_set() and all(op.done() for op in cursor.operations)
        assert len(cursor.operations) == len(database.queries) == cursor.close_calls == 1
        assert not cursor.closed_during_read
    finally:
        cursor.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*cursor.operations, return_exceptions=True)
