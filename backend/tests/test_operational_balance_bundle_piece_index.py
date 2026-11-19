"""Request-local bundle receipt lookup; all source records are synthetic."""
import asyncio
from copy import deepcopy
from decimal import Decimal
from threading import Event

from mongomock_motor import AsyncMongoMockClient
import pytest

import operational_bundle_legacy_receipts as legacy
import operational_remaining_bundle_sources as bundles
from operational_financial_activity_runtime import preparation_scope
from test_operational_balance_bundle_legacy_receipts import fixture, pieces as seed_pieces, OWNER
from test_operational_balance_remaining_products import CREATED, NOW


async def cohort(monkeypatch, count=80):
    db = AsyncMongoMockClient().bundle_piece_index
    raw, _, _ = await fixture(db, monkeypatch, rose=False, name=False)
    saved = await db.mezan_component_order_lifecycle_v1.find_one({'order_number': 'ORDER'})
    orders, pieces = [], []
    for position in range(count):
        number = 'ORDER' if position == 0 else f'ORDER-{position}'
        line = f'salla:{number}:LINE'
        order = deepcopy(raw)
        order.update(id=f'order-{position}', reference_id=number)
        orders.append(order)
        if position:
            record = deepcopy(saved)
            record.pop('_id', None)
            record['order_number'] = number
            for field in ('bundle_intake_snapshots', 'reporting_cost_snapshots'):
                record[field] = {line: value for value in record[field].values()}
            await db.mezan_component_order_lifecycle_v1.insert_one(record)
        for unit in range(1, 5):
            pieces.append({'user_id': OWNER, 'id': f'piece-{position}-{unit}',
                'order_number': number, 'order_item_id': line, 'unit_index': unit,
                'product_id': 'fixture-shawl', 'created_at': CREATED})
    return db, orders, pieces


def test_many_bundle_lines_visit_only_their_receipt_cohort(monkeypatch):
    async def exercise():
        db, orders, pieces = await cohort(monkeypatch)
        before = deepcopy((orders, pieces))
        visits = 0
        original = legacy.map_financial_receipts

        def observed(**kwargs):
            nonlocal visits
            visits += len(kwargs['pieces'])
            return original(**kwargs)

        monkeypatch.setattr(legacy, 'map_financial_receipts', observed)
        output = await bundles.load_remaining_bundles(db, OWNER, orders, NOW,
            preparation_pieces=pieces, native_matches={})
        assert len(output) == len(orders)
        assert all(row['complete'] and row['issues'] == [] for row in output.values())
        assert all(sum(Decimal(item['cost']) for item in row['items']) == 60
                   for row in output.values())
        assert sum(len(row['items']) for row in output.values()) == len(pieces)
        assert before == (orders, pieces)
        # One line's mapper must not rescan all other lines' preparation rows.
        assert visits <= 2 * len(pieces)
        assert visits == len(pieces)
        with monkeypatch.context() as legacy_path:
            legacy_path.setattr(bundles, '_legacy_piece_index', lambda rows: None)
            previous = await bundles.load_remaining_bundles(db, OWNER, orders, NOW,
                preparation_pieces=pieces, native_matches={})
        assert previous == output
        assert before == (orders, pieces)
    asyncio.run(exercise())


def _piece(number, line, unit=1, **updates):
    return {'user_id': OWNER, 'id': f'unit-{unit}', 'order_number': number,
            'order_item_id': line, 'unit_index': unit, 'product_id': 'product',
            'created_at': CREATED, **updates}


@pytest.mark.parametrize('number,line', [
    (' ORDER ', ' LINE '), ('ORDER', 'LINE'), (None, None), (0, 0),
    (True, False), (12.5, 23.5), (45, 67), ('45', '67')])
def test_raw_keys_preserve_receipt_output_and_original_row_identity(number, line):
    rows = [_piece('unrelated', 'LINE'), _piece(number, line, 2),
            _piece(number, line, 1), _piece(number, line, 3, experiment_mode=True),
            _piece(number, line, 4, experiment_run_id='experiment')]
    before = deepcopy(rows)
    index = bundles._legacy_piece_index(rows)
    subset = index[(str(number), str(line))]
    assert all(actual is expected for actual, expected in zip(subset, rows[1:]))
    expected = legacy._piece_map(rows, owner=OWNER, number=str(number), line=str(line),
        product='product', count=4, as_of=NOW)
    actual = legacy._piece_map(subset, owner=OWNER, number=str(number), line=str(line),
        product='product', count=4, as_of=NOW)
    assert list(actual) == list(expected) == [2, 1]
    assert all(actual[key] is expected[key] for key in actual)
    assert rows == before


@pytest.mark.parametrize('damage', ['duplicate', 'unit', 'product', 'owner', 'future'])
def test_receipt_validation_rejects_the_same_conflicts(damage):
    first = _piece('ORDER', 'LINE')
    rows = [_piece('different', 'LINE'), first, _piece('ORDER', 'LINE', 2)]
    if damage == 'duplicate':
        rows.append(first)
    elif damage == 'unit':
        rows[2]['unit_index'] = 1
    elif damage == 'product':
        rows[2]['product_id'] = 'different'
    elif damage == 'owner':
        rows[2]['user_id'] = 'different'
    elif damage == 'future':
        rows[2]['updated_at'] = '2027-01-01T00:00:00Z'
    subset = bundles._legacy_piece_index(rows)[('ORDER', 'LINE')]
    failures = []
    for selected in (rows, subset):
        with pytest.raises(ValueError) as error:
            legacy._piece_map(selected, owner=OWNER, number='ORDER', line='LINE',
                product='product', count=4, as_of=NOW)
        failures.append(str(error.value))
    assert failures[0] == failures[1]
    if damage == 'duplicate':
        assert subset[0] is subset[2] is first


@pytest.mark.parametrize('shape', ['mapping', 'list', 'value', 'key', 'huge-int'])
def test_unsupported_shapes_do_not_eagerly_convert_or_visit_custom_values(shape):
    class StringValue(str):
        def __str__(self):
            raise AssertionError('unrelated line identity was evaluated')

    class Mapping(dict):
        def get(self, *args):
            raise AssertionError('optional index touched a custom mapping')

    class Rows(list):
        def __iter__(self):
            raise AssertionError('optional index iterated a custom cohort')

    rows = [_piece('unrelated', StringValue('LINE'))]
    if shape == 'mapping':
        rows = [Mapping(rows[0])]
    elif shape == 'list':
        rows = Rows(rows)
    elif shape == 'key':
        rows[0][1] = 'extra metadata'
    elif shape == 'huge-int':
        rows[0]['order_item_id'] = 1 << 15000
    assert bundles._legacy_piece_index(rows) is None
    if shape in {'value', 'key', 'huge-int'}:
        assert legacy._piece_map(rows, owner=OWNER, number='ORDER', line='LINE',
            product='product', count=4, as_of=NOW) == {}


def test_cancelled_preparation_stops_before_building_the_index():
    stop = Event()
    stop.set()
    with preparation_scope(stop), pytest.raises(asyncio.CancelledError):
        bundles._legacy_piece_index([_piece('ORDER', 'LINE')])


@pytest.mark.parametrize('damage', ['duplicate', 'future', 'owner', 'experiment', 'missing'])
def test_whole_adapter_result_matches_full_scan_for_receipt_variants(monkeypatch, damage):
    async def exercise():
        db, orders, pieces = await cohort(monkeypatch, count=3)
        if damage == 'duplicate':
            pieces.append(pieces[0])
        elif damage == 'future':
            pieces[0]['updated_at'] = '2027-01-01T00:00:00Z'
        elif damage == 'owner':
            pieces[0]['user_id'] = 'different'
        elif damage == 'experiment':
            pieces[0]['experiment_run_id'] = 'experiment'
        elif damage == 'missing':
            pieces.pop(0)
        before = deepcopy((orders, pieces))
        db_before = {name: await db[name].find({}).to_list(None)
                     for name in await db.list_collection_names()}

        async def result():
            try:
                return await bundles.load_remaining_bundles(db, OWNER, orders, NOW,
                    preparation_pieces=pieces, native_matches={})
            except ValueError as error:
                return type(error), str(error)

        indexed = await result()
        with monkeypatch.context() as legacy_path:
            legacy_path.setattr(bundles, '_legacy_piece_index', lambda rows: None)
            original = await result()
        assert original == indexed
        assert before == (orders, pieces)
        assert db_before == {name: await db[name].find({}).to_list(None)
                             for name in await db.list_collection_names()}
        if damage == 'owner':
            assert indexed == (ValueError, 'bundle_legacy_piece_read_scope_invalid')
        elif damage in {'duplicate', 'future'}:
            assert indexed[('ORDER', 'salla:ORDER:LINE')]['complete'] is False
        else:
            assert all(row['complete'] for row in indexed.values())
    asyncio.run(exercise())


def test_received_product_and_service_costs_keep_exact_supplier_evidence(monkeypatch):
    async def exercise():
        db = AsyncMongoMockClient().bundle_receipt_index
        raw, _, _ = await fixture(db, monkeypatch, rose=True, name=True)
        await seed_pieces(db, received=4, completed=2)
        pieces = await db.mezan_preparation_pieces_v1.find({}).to_list(None)
        pieces.append(_piece('unrelated', 'different'))
        before = deepcopy((raw, pieces))
        indexed = await bundles.load_remaining_bundles(db, OWNER, [raw], NOW,
            preparation_pieces=pieces, native_matches={})
        with monkeypatch.context() as legacy_path:
            legacy_path.setattr(bundles, '_legacy_piece_index', lambda rows: None)
            original = await bundles.load_remaining_bundles(db, OWNER, [raw], NOW,
                preparation_pieces=pieces, native_matches={})
        assert indexed == original
        result = indexed[('ORDER', 'salla:ORDER:LINE')]
        assert result['complete'] and result['issues'] == []
        received = [item for item in result['items'] if item['supplier_receipt_proven']]
        unreceived = [item for item in result['items'] if not item['supplier_receipt_proven']]
        assert sum(Decimal(item['cost']) for item in received) == 80
        assert sum(Decimal(item['cost']) for item in unreceived) == 180
        assert {item['supplier_id'] for item in received} == {'external-supplier'}
        assert before == (raw, pieces)
    asyncio.run(exercise())
