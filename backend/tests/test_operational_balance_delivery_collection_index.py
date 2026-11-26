"""Collection lookup preserves driver custody evidence and bounded source work."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import sys
from threading import Event

import pytest
from bson.int64 import Int64

import operational_balance_sources as sources
import operational_delivery_collection_index as indexed
from operational_delivery_assignment_index import build_delivery_assignment_index
from operational_financial_activity_runtime import prepare_in_thread


NOW = '2026-10-10T00:00:00+00:00'
CREATED = '2026-10-06T00:00:00+00:00'


def collection(**fields):
    return {'id': 'receipt', 'user_id': 'owner', 'assignment_id': 'a',
            'driver_id': 'driver', 'order_id': 'order', 'order_number': '10',
            'amount': '200', 'cod_custody_amount': '200', 'payment_method': 'cash',
            'amount_source': 'unified_orders.remaining_amount', 'collected_at': CREATED,
            **fields}


def attachment_work(size, *, use_index=True, measure_reads=True):
    """Count real dictionary reads in the existing attachment, including build."""
    assignments = [{'id': f'a{i}', 'user_id': 'owner', 'order_id': f'o{i}',
                    'order_number': str(i), 'driver_id': 'driver', 'active': True,
                    'status': 'delivered'} for i in range(size)]
    rows = [collection(id=f'c{i}', assignment_id=f'a{i}', order_id=f'o{i}',
                       order_number=str(i)) for i in range(size)]
    data = {'store_delivery_assignments': assignments, 'store_delivery_collections': rows,
            'mz2_shipping_setup_v2': [],
            '_assignment_index': build_delivery_assignment_index(assignments, 'owner')}
    row_ids, reads = {id(row) for row in rows}, 0

    def profile(_frame, event, function):
        nonlocal reads
        if (event == 'c_call' and getattr(function, '__name__', None) == 'get'
                and id(getattr(function, '__self__', None)) in row_ids):
            reads += 1

    previous = sys.getprofile()
    try:
        if measure_reads:
            sys.setprofile(profile)
        builder = getattr(sources, 'build_delivery_collection_index', None)
        if use_index and builder is not None:
            data['_collection_index'] = builder(rows, 'owner')
        for i in range(size):
            order = {'id': f'o{i}', 'order_number': str(i), 'created_at': CREATED,
                     'status': 'delivered', 'currency': 'SAR', 'cod_amount': '0',
                     'cod': {'collected': '100', 'outstanding': '200'}}
            out = {'issues': []}
            sources._attach_delivery_sources(data, {}, 'owner', {}, order, out,
                sources.instant(NOW), Decimal('300'), True)
            assert order['cod_amount'] == '200'
            assert order['cod']['evidence_ids'] == [f'c{i}']
            assert order['cod']['collected'] == '300'
    finally:
        sys.setprofile(previous)
    return reads


def test_actual_attachment_repeated_collection_work_grows_linearly():
    small, large = attachment_work(64), attachment_work(128)
    legacy = attachment_work(128, use_index=False)
    assert large <= small * 2.4, (small, large)
    assert large < legacy / 4, (large, legacy)


def legacy_matches(rows, assignment, driver='driver', identity='order', number='10', event=False):
    # Frozen predicate from e79d595fb0fac75650b79613d40c1d1c8eb04051.
    return [c for c in rows if c.get('assignment_id') == assignment.get('id')
            and c.get('driver_id') == driver
            and (not event or (str(c.get('order_id')) == identity
                              and str(c.get('order_number')) == number))]


def outcome(function):
    try:
        return 'value', function()
    except Exception as error:
        return 'error', type(error), str(error)


def assert_parity(rows, assignment=None, *, driver='driver', identity='order', number='10', event=False):
    assignment = {'id': 'a'} if assignment is None else assignment
    before = deepcopy(rows)
    view = indexed.build_delivery_collection_index(rows, 'owner')
    expected = outcome(lambda: legacy_matches(rows, assignment, driver, identity, number, event))
    actual = outcome(lambda: legacy_matches(indexed.delivery_collection_rows(rows, assignment,
        owner='owner', collection_index=view), assignment, driver, identity, number, event))
    assert actual == expected
    if actual[0] == 'value':
        assert [id(row) for row in actual[1]] == [id(row) for row in expected[1]]
    assert rows == before


@pytest.mark.parametrize('rows', [
    [], (), None, {}, 'invalid', [None], ['invalid'], [{}],
    [collection()], (collection(),), [collection(), collection()],
    [collection(assignment_id='other'), collection()],
    [collection(), None], [collection(assignment_id=[]), collection()],
    [collection(driver_id='different')], [collection(order_id='different')],
    [collection(order_number=10)], [collection(order_number=' 10')],
])
@pytest.mark.parametrize('event', [False, True])
def test_frozen_predicate_matches_rows_errors_order_and_identity(rows, event):
    assert_parity(rows, event=event)


@pytest.mark.parametrize('identity', [
    None, '', 0, False, True, 1, '1', ' a ', 1.5, Int64(1),
    float('inf'), float('-inf'), float('nan'), [], {}, 10 ** 400,
])
def test_assignment_key_types_preserve_exact_equality_without_string_coercion(identity):
    rows = [collection(assignment_id=value) for value in [None, False, 0, True, 1, 1.0, Int64(1), '1', identity]]
    assert_parity(rows, {'id': identity})


def test_indexed_numeric_equality_none_and_duplicate_occurrences_are_unchanged():
    shared = collection(assignment_id=True)
    rows = [collection(assignment_id=1), shared, collection(assignment_id='1'),
            collection(assignment_id=Int64(1)), shared, collection(assignment_id=None)]
    view = indexed.build_delivery_collection_index(rows, 'owner')
    assert view is not None
    selected = indexed.delivery_collection_rows(rows, {'id': 1.0}, owner='owner', collection_index=view)
    assert [id(row) for row in selected] == [id(rows[i]) for i in (0, 1, 3, 4)]
    assert indexed.delivery_collection_rows(rows, {}, owner='owner', collection_index=view) == [rows[-1]]
    assert_parity(rows, {'id': 1})


def test_driver_and_delivery_event_filters_keep_their_different_identity_rules():
    rows = [collection(driver_id=1, order_id=2, order_number=3),
            collection(driver_id='1', order_id='2', order_number='3'),
            collection(driver_id=1, order_id=' 2', order_number=3)]
    assert_parity(rows, driver=1, identity='2', number='3', event=True)
    assert_parity(rows, driver='1', identity='2', number='3', event=True)
    assert_parity(rows, driver=1, identity='2', number='3', event=False)


def test_malformed_unused_fields_stay_behind_original_short_circuits():
    class Explosive:
        def __str__(self):
            raise RuntimeError('legacy conversion')

    rows = [collection(assignment_id='other', order_id=Explosive()), collection()]
    view = indexed.build_delivery_collection_index(rows, 'owner')
    assert view is not None
    for assignment, event in (({'id': 'a'}, True), ({'id': 'other'}, False), ({'id': 'other'}, True)):
        expected = outcome(lambda: legacy_matches(rows, assignment, event=event))
        actual = outcome(lambda: legacy_matches(indexed.delivery_collection_rows(rows, assignment,
            owner='owner', collection_index=view), assignment, event=event))
        assert actual == expected


def test_unsupported_cohort_keys_and_query_methods_are_not_eagerly_executed():
    calls = []

    class OddList(list):
        def __iter__(self):
            raise RuntimeError('legacy iteration')

    class OddDict(dict):
        def get(self, key, default=None):
            calls.append(key)
            return super().get(key, default)

    class OddString(str):
        def __hash__(self):
            raise RuntimeError('legacy hash must not run')

    assert indexed.build_delivery_collection_index(OddList([collection()]), 'owner') is None
    odd_row = OddDict(collection())
    assert indexed.build_delivery_collection_index([odd_row], 'owner') is None and calls == []
    odd_key = OddString('a')
    rows = [collection(assignment_id=odd_key)]
    assert indexed.build_delivery_collection_index(rows, 'owner') is None
    assert legacy_matches(indexed.delivery_collection_rows(rows, {'id': 'a'}, owner='owner'), {'id': 'a'}) == rows
    rows = [collection()]
    view = indexed.build_delivery_collection_index(rows, 'owner')
    query = OddDict(id='a')
    assert indexed.delivery_collection_rows(rows, query, owner='owner', collection_index=view) is rows
    assert calls == []
    assert indexed.delivery_collection_rows(rows, {'id': odd_key}, owner='owner', collection_index=view) is rows


def test_unusual_metaclass_equality_is_not_run_by_optional_build():
    class OddMeta(type):
        def __eq__(self, other):
            raise RuntimeError('unexpected class equality')

    class Odd(metaclass=OddMeta):
        pass

    assert indexed.build_delivery_collection_index([collection(assignment_id=Odd())], 'owner') is None


def test_wrong_owner_replaced_cohort_or_changed_length_uses_original_rows():
    rows = [collection()]
    view = indexed.build_delivery_collection_index(rows, 'owner')
    other = deepcopy(rows)
    for cohort, owner in ((other, 'owner'), (rows, 'foreign'), (rows, None)):
        assert indexed.delivery_collection_rows(cohort, {'id': 'a'}, owner=owner, collection_index=view) is cohort
    rows.append(collection(assignment_id='other'))
    assert indexed.delivery_collection_rows(rows, {'id': 'a'}, owner='owner', collection_index=view) is rows


def test_new_attempt_rebuilds_after_rows_change_without_reusing_prior_evidence():
    rows = [collection()]
    first = indexed.build_delivery_collection_index(rows, 'owner')
    rows[0]['assignment_id'] = 'next'
    second = indexed.build_delivery_collection_index(rows, 'owner')
    assert first is not second
    assert indexed.delivery_collection_rows(rows, {'id': 'next'}, owner='owner', collection_index=second) == rows
    assert indexed.delivery_collection_rows(rows, {'id': 'a'}, owner='owner', collection_index=second) == ()


@pytest.mark.asyncio
async def test_cancelled_owned_build_joins_worker_and_preserves_source_rows(monkeypatch):
    rows = [collection(assignment_id=str(i)) for i in range(1000)]
    before = deepcopy(rows)
    entered, release, exited = Event(), Event(), Event()
    checkpoint, visits = indexed._checkpoint, 0

    def controlled_checkpoint():
        nonlocal visits
        visits += 1
        if visits == 10:
            entered.set()
            assert release.wait(3)
        checkpoint()

    def build():
        try:
            return indexed.build_delivery_collection_index(rows, 'owner')
        finally:
            exited.set()

    monkeypatch.setattr(indexed, '_checkpoint', controlled_checkpoint)
    task = asyncio.create_task(prepare_in_thread(build))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert visits == 10 and exited.is_set() and rows == before
        assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['cash', 'bank_transfer', 'confirmed_bank', 'card_terminal',
                                 'duplicate', 'wrong_driver', 'wrong_order', 'odd_key',
                                 'new_contract', 'native_driver', 'courier'])
@pytest.mark.parametrize('replay', [False, True])
async def test_whole_collection_financial_state_reports_and_read_cohort_match_full_scan(monkeypatch, kind, replay):
    from operational_balance_engine import reconcile
    from operational_balance_service import report
    from operational_financial_position import build_financial_position
    from test_operational_balance_delivery_cutover import HardBoundaryDB, active, driver_data, courier_data, START, NOW
    from test_operational_balance_verified_shipping_fees import fixture

    data = courier_data() if kind == 'courier' else fixture() if kind == 'native_driver' else driver_data(
        kind if kind in {'bank_transfer', 'card_terminal'} else 'cash')
    if kind == 'confirmed_bank':
        data['store_delivery_collections'][0].update(payment_method='bank_transfer',
            cod_custody_amount='0', payment_confirmed=True)
    elif kind == 'duplicate':
        data['store_delivery_collections'].append(deepcopy(data['store_delivery_collections'][0]))
    elif kind == 'wrong_driver':
        data['store_delivery_collections'][0]['driver_id'] = 'other'
    elif kind == 'wrong_order':
        data['store_delivery_collections'][0]['order_id'] = 'other'
    elif kind == 'odd_key':
        data['store_delivery_collections'][0]['assignment_id'] = ['assignment']
    elif kind == 'new_contract':
        data['unified_orders'][0]['raw_by_source']['salla_direct']['created_at'] = CREATED
    state = active()
    monkeypatch.setattr('operational_financial_activity.now', lambda: NOW)
    if replay:
        first = await sources.collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=state)
        state = reconcile(state, first, NOW)
        if kind == 'cash':
            state['movements'] = [{'id': 'synthetic-settlement', 'direction': 'incoming', 'kind': 'collection',
                'amount': '150.00', 'currency': 'SAR', 'party_type': 'store_driver', 'party_id': 'driver',
                'bank_kind': 'bank', 'bank_id': 'bank',
                'allocations': [{'obligation_id': 'cod:100', 'amount': '150.00'}]}]
    before = deepcopy((data, state))
    builds, builder = [], sources.build_delivery_collection_index

    def build(rows, owner):
        view = builder(rows, owner)
        builds.append(view)
        return view

    monkeypatch.setattr(sources, 'build_delivery_collection_index', build)
    actual_db = HardBoundaryDB(data)
    actual = await sources.collect_sources(actual_db, 'owner', START, NOW, operational_state=state)
    assert len(builds) == 1 and (builds[0] is None) == (kind == 'odd_key')
    monkeypatch.setattr(sources, 'build_delivery_collection_index', lambda rows, owner: None)
    expected_db = HardBoundaryDB(data)
    expected = await sources.collect_sources(expected_db, 'owner', START, NOW, operational_state=state)
    assert actual == expected and actual_db.reads == expected_db.reads
    actual_state, expected_state = reconcile(state, actual, NOW), reconcile(state, expected, NOW)
    assert actual_state == expected_state
    assert actual_state['movements'] == state['movements'] and actual_state['openings'] == state['openings']
    assert report(actual_state, as_of=NOW) == report(expected_state, as_of=NOW)
    assert build_financial_position(actual_state, generated_at=NOW) == build_financial_position(expected_state, generated_at=NOW)
    assert (data, state) == before


@pytest.mark.asyncio
async def test_each_collector_invocation_builds_its_own_index(monkeypatch):
    from test_operational_balance_delivery_cutover import HardBoundaryDB, active, driver_data, START, NOW
    data, state, builds = driver_data(), active(), []

    def build(rows, owner):
        view = indexed.build_delivery_collection_index(rows, owner)
        builds.append(view)
        return view

    monkeypatch.setattr(sources, 'build_delivery_collection_index', build)
    for _ in range(2):
        await sources.collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=state)
    assert len(builds) == 2 and builds[0] is not None and builds[1] is not builds[0]
