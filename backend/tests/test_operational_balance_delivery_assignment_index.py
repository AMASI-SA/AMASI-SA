"""Frozen-reader parity, collector wiring, and owned assignment preparation."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from threading import Event

import pytest
from bson.int64 import Int64

import operational_delivery_assignment_index as indexed
import operational_delivery_evidence as evidence
from operational_financial_activity_runtime import prepare_in_thread


# Extracted unchanged from M 2db0edc9edb315026eee4b6edd43b420a569c37d.
# The complete original module SHA256 was
# 0a64e6a27dcf9e49afe23b0240c15f0ba4bd74c3ea79aafe6b6f5e056c7eabcc.
# Keep this independent of the candidate's lookup and available in shallow CI.
LEGACY_SOURCE = '''def _driver_assignment(assignments, owner, order_id, number):
    if not isinstance(assignments, (list, tuple)):
        _fail("assignment_scope_invalid")
    if any(not isinstance(row, dict) or row.get("user_id") != owner for row in assignments):
        _fail("owner_mismatch")
    relevant = [row for row in assignments if _text(row.get("order_id")) == order_id
                or _text(row.get("order_number")) == number]
    if any(_text(row.get("order_id")) != order_id or _text(row.get("order_number")) != number for row in relevant):
        _fail("assignment_order_conflict")
    active = [row for row in relevant if row.get("active") is True and not _inactive(row)
              and _status(row.get("status")) in {"assigned", "out_for_delivery", *DELIVERED}]
    if len(active) > 1:
        _fail("assignment_ambiguous")
    if not active:
        if any(_status(row.get("status")) in DELIVERED for row in relevant):
            _fail("assignment_inactive")
        return None
    assignment = active[0]
    identity = _text(assignment.get("id"))
    if not identity or not _text(assignment.get("driver_id")):
        _fail("assignment_identity_missing")
    if sum(_text(row.get("id")) == identity for row in assignments) != 1:
        _fail("assignment_ambiguous")
    return assignment
'''
LEGACY_FILENAME = 'frozen_2db0edc9_operational_delivery_evidence.py'
_namespace = {name: getattr(evidence, name) for name in ('_fail', '_text', '_inactive', '_status', 'DELIVERED')}
exec(compile(LEGACY_SOURCE, LEGACY_FILENAME, 'exec'), _namespace)
legacy_driver = _namespace['_driver_assignment']


def assignment(**fields):
    return {'id': 'a', 'user_id': 'owner', 'order_id': 'order', 'order_number': '10',
            'driver_id': 'driver', 'active': True, 'status': 'delivered', **fields}


def raw_match(rows, number):
    return [row for row in rows if str(row.get('order_number')) == number
            and row.get('active') is not False and row.get('status') in {'assigned', 'out_for_delivery', 'delivered'}]


def outcome(function):
    try:
        value = function()
        return ('value', value)
    except Exception as error:
        return ('error', type(error), str(error))


def parity(rows, owner='owner', oid='order', number='10'):
    before = deepcopy(rows)
    view = indexed.build_delivery_assignment_index(rows, owner)
    old = outcome(lambda: legacy_driver(rows, owner, oid, number))
    new = outcome(lambda: indexed.indexed_driver_assignment(rows, owner, oid, number, assignment_index=view))
    assert new == old
    if new[0] == 'value':
        assert new[1] is old[1]
    old_raw = outcome(lambda: raw_match(rows, number))
    new_raw = outcome(lambda: indexed.active_assignments_for_number(rows, number, owner=owner, assignment_index=view))
    assert old_raw == new_raw
    if old_raw[0] == 'value':
        assert [id(row) for row in old_raw[1]] == [id(row) for row in new_raw[1]]
    assert rows == before


CASES = [
    [], [assignment()], (assignment(),), None, {}, 'invalid',
    [None], ['invalid'], [{}], [assignment(user_id='foreign')],
    [assignment(), assignment(user_id='foreign', order_id='other', order_number='99')],
    [assignment(order_number='99')], [assignment(order_id='other')],
    [assignment(), assignment(id='b', order_number='99')],
    [assignment(), assignment(id='b', order_id='other')],
    [assignment(), assignment()],
    [assignment(), assignment(order_id='other', order_number='99', active=False)],
    [assignment(active=False)], [assignment(active=None)], [assignment(active=1)],
    [assignment(status='assigned')], [assignment(status='out_for_delivery')],
    [assignment(status='تم التوصيل')], [assignment(status='cancelled')],
    [assignment(status={'slug': 'delivered'})], [assignment(status=['delivered'])],
    [assignment(status=None)], [assignment(archived=True)], [assignment(type='return')],
    [assignment(id=1), assignment(id='1', order_id='other', order_number='99', active=False)],
    [assignment(order_id='other', order_number='99', status={'slug': 'delivered'}), assignment()],
]


@pytest.mark.parametrize('rows', CASES)
def test_plain_and_malformed_cohorts_preserve_value_exception_and_row_identity(rows):
    parity(rows)


@pytest.mark.parametrize('field', ['id', 'order_id', 'order_number', 'driver_id'])
@pytest.mark.parametrize('value', [None, '', 0, False, True, 1, '1', ' padded ', 'bad\x00id', 'x' * 301, [], {}, 1.5, Int64(1)])
def test_exact_identity_normalization_and_raw_number_view_remain_separate(field, value):
    parity([assignment(**{field: value})])


@pytest.mark.parametrize('owner,oid,number', [
    ('foreign', 'order', '10'), (None, 'order', '10'), ('owner', None, '10'),
    ('owner', 'order', None), ('owner', 1, '10'), ('owner', 'order', 10),
    ('owner', [], {}), ('owner', '', ''), ('owner', 'missing', 'missing'),
])
def test_unusual_queries_and_owner_mismatches_use_original_contract(owner, oid, number):
    parity([assignment()], owner, oid, number)


def test_owner_error_still_precedes_conflict_and_status_errors():
    rows = [assignment(status=['unhashable']), assignment(user_id='foreign', order_id='other', order_number='99')]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    assert view is not None
    assert outcome(lambda: indexed.indexed_driver_assignment(rows, 'owner', 'order', '10', assignment_index=view)) == (
        'error', ValueError, 'operational_delivery_owner_mismatch')


def test_or_match_keeps_conflicting_inactive_rows_and_duplicate_occurrences():
    same = assignment()
    rows = [assignment(id='unrelated', order_id='other', order_number='99'), same, same]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    assert indexed.active_assignments_for_number(rows, '10', owner='owner', assignment_index=view) == [same, same]
    parity(rows)
    parity([assignment(), assignment(id='b', order_number='99', active=False)])


def test_raw_view_does_not_normalize_whitespace_null_boolean_or_negative_number():
    rows = [assignment(id=str(i), order_number=value) for i, value in enumerate((' 10', None, True, -1, 10, '10'))]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    assert view is not None
    for number in (' 10', 'None', 'True', '-1', '10', 'missing'):
        assert [id(row) for row in indexed.active_assignments_for_number(rows, number, owner='owner', assignment_index=view)] == [
            id(row) for row in raw_match(rows, number)]


def test_native_bson_integer_ids_use_index_and_preserve_normalized_matching(monkeypatch):
    rows = [assignment(id=Int64(3), order_id=Int64(1), order_number=Int64(2), driver_id=Int64(4))]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    assert view is not None
    expected = legacy_driver(rows, 'owner', '1', '2')
    def forbidden(*args):
        raise AssertionError('ordinary BSON Int64 history reached the legacy scan')
    monkeypatch.setattr(evidence, '_driver_assignment', forbidden)
    assert indexed.indexed_driver_assignment(rows, 'owner', '1', '2', assignment_index=view) is expected
    assert indexed.active_assignments_for_number(rows, '2', owner='owner', assignment_index=view) == rows


def test_scope_mismatch_and_changed_list_length_cannot_reuse_index(monkeypatch):
    rows = [assignment()]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    calls = []
    def legacy(*args):
        calls.append(args)
        return legacy_driver(*args)
    monkeypatch.setattr(evidence, '_driver_assignment', legacy)
    copy = deepcopy(rows)
    assert indexed.indexed_driver_assignment(copy, 'owner', 'order', '10', assignment_index=view) is copy[0]
    assert outcome(lambda: indexed.indexed_driver_assignment(rows, 'foreign', 'order', '10', assignment_index=view))[0] == 'error'
    rows.append(assignment(id='b', order_id='other', order_number='99'))
    assert indexed.indexed_driver_assignment(rows, 'owner', 'order', '10', assignment_index=view) is rows[0]
    assert len(calls) == 3


def test_build_does_not_touch_unusual_container_or_identity_methods():
    class ExoticList(list):
        def __iter__(self):
            raise RuntimeError('legacy iteration')
    class ExoticString(str):
        def __str__(self):
            raise RuntimeError('legacy conversion')
    unusual = ExoticList([assignment()])
    assert indexed.build_delivery_assignment_index(unusual, 'owner') is None
    assert outcome(lambda: indexed.indexed_driver_assignment(unusual, 'owner', 'order', '10')) == (
        'error', RuntimeError, 'legacy iteration')
    rows = [assignment(id=ExoticString('a'))]
    assert indexed.build_delivery_assignment_index(rows, 'owner') is None
    assert outcome(lambda: indexed.indexed_driver_assignment(rows, 'owner', 'missing', 'missing')) == ('value', None)
    assert outcome(lambda: indexed.indexed_driver_assignment(rows, 'owner', 'order', '10')) == (
        'error', RuntimeError, 'legacy conversion')
    assert indexed.build_delivery_assignment_index([assignment(id=10 ** 5000)], 'owner') is None


@pytest.mark.parametrize('field', ['container', 'id', 'order_id', 'order_number', 'user_id'])
def test_unusual_metaclass_equality_is_never_invoked_by_build(field):
    class ExplosiveMeta(type):
        def __eq__(self, other):
            raise RuntimeError('unexpected class equality')
    class Unusual(metaclass=ExplosiveMeta):
        pass
    class UnusualRows(list, metaclass=ExplosiveMeta):
        pass
    rows = UnusualRows([assignment()]) if field == 'container' else [assignment(**{field: Unusual()})]
    assert indexed.build_delivery_assignment_index(rows, 'owner') is None


def test_each_attempt_rebuilds_after_same_cohort_rows_change():
    rows = [assignment()]
    first = indexed.build_delivery_assignment_index(rows, 'owner')
    assert indexed.indexed_driver_assignment(rows, 'owner', 'order', '10', assignment_index=first) is rows[0]
    rows[0].update(order_id='next', order_number='20')
    second = indexed.build_delivery_assignment_index(rows, 'owner')
    assert second is not first
    assert indexed.indexed_driver_assignment(rows, 'owner', 'next', '20', assignment_index=second) is rows[0]
    assert indexed.indexed_driver_assignment(rows, 'owner', 'order', '10', assignment_index=second) is None


def cohort(size):
    return [assignment(id='a' + str(i), order_id='driver-' + str(i), order_number=str(100000 + i)) for i in range(size)]


def work(size, *, use_index=True):
    filenames = {str(Path(indexed.__file__).resolve()), str(Path(evidence.__file__).resolve()), LEGACY_FILENAME}
    count = 0
    def trace(frame, event, _arg):
        nonlocal count
        if event == 'line' and frame.f_code.co_filename in filenames:
            count += 1
        return trace
    rows = cohort(size)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        view = indexed.build_delivery_assignment_index(rows, 'owner') if use_index else None
        for i in range(size):
            assert indexed.indexed_driver_assignment(rows, 'owner', 'courier-' + str(i), str(900000 + i), assignment_index=view) is None
            assert indexed.active_assignments_for_number(rows, str(900000 + i), owner='owner', assignment_index=view) == []
    finally:
        sys.settrace(previous)
    return count


def test_history_traversal_grows_with_cohort_instead_of_orders_times_cohort():
    small, large, legacy = work(64), work(128), work(128, use_index=False)
    assert large <= small * 2.4, (small, large)
    assert large < legacy / 4, (large, legacy)


@pytest.mark.asyncio
async def test_cancellation_during_owned_build_joins_worker_without_mutation(monkeypatch):
    rows = cohort(1000)
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
            return indexed.build_delivery_assignment_index(rows, 'owner')
        finally:
            exited.set()
    monkeypatch.setattr(indexed, '_checkpoint', controlled_checkpoint)
    task = asyncio.create_task(prepare_in_thread(build))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert exited.is_set() and visits == 10 and rows == before
        assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
    finally:
        release.set()


@pytest.mark.parametrize('kind', ['courier', 'driver'])
def test_actual_delivery_resolver_uses_optional_index_without_legacy_scan(monkeypatch, kind):
    from test_operational_balance_delivery_cutover import courier_data, driver_data, START, NOW
    data = courier_data() if kind == 'courier' else driver_data()
    source = data['unified_orders'][0]
    rows = data.get('store_delivery_assignments', [])
    raw = source['raw_by_source']['salla_direct']
    kwargs = dict(source=source, owner='owner', assignments=rows, setup=data['mz2_shipping_setup_v2'][0],
                  started_at=START, as_of=NOW)
    expected = evidence.resolve_delivery_event(raw, **kwargs)
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    def forbidden(*args):
        raise AssertionError('normal resolver reached full assignment scan')
    monkeypatch.setattr(evidence, '_driver_assignment', forbidden)
    assert evidence.resolve_delivery_event(raw, **kwargs, assignment_index=view) == expected


@pytest.mark.parametrize('kind', ['courier', 'native_driver', 'contract_driver'])
def test_owner_confirmation_forwards_index_without_changing_sealed_evidence(monkeypatch, kind):
    from test_operational_balance_delivery_confirmation import data_for, confirmed
    from test_operational_balance_delivery_cutover import START, NOW
    data = data_for(kind)
    state, record = confirmed(data)
    source = data['unified_orders'][0]
    rows = data.get('store_delivery_assignments', [])
    raw = source['raw_by_source']['salla_direct']
    kwargs = dict(source=source, owner='owner', assignments=rows, setup=data['mz2_shipping_setup_v2'][0],
                  started_at=START, as_of=NOW, owner_confirmation=record)
    expected = evidence.resolve_delivery_event(raw, **kwargs)
    before = deepcopy((data, state, record))
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    def forbidden(*args):
        raise AssertionError('owner confirmation reached full assignment scan')
    monkeypatch.setattr(evidence, '_driver_assignment', forbidden)
    assert evidence.resolve_delivery_event(raw, **kwargs, assignment_index=view) == expected
    assert (data, state, record) == before


def test_date_gap_enrichment_keeps_all_matching_issue_rows_and_original_references(monkeypatch):
    from operational_delivery_confirmation import date_gap_identity
    from test_operational_balance_delivery_cutover import driver_data, START
    data = driver_data()
    rows = data['store_delivery_assignments']
    raw = data['unified_orders'][0]['raw_by_source']['salla_direct']
    code = 'operational_delivery_timestamp_missing'
    original = {'issues': [{'code': code, 'source_id': '100', 'courier_fee_review': {'old': True}},
                           {'code': code, 'source_id': 'other'},
                           {'code': code, 'source_id': 100, 'driver_fee_review': {'old': True}},
                           {'code': 'other', 'source_id': '100'}]}
    expected, actual = deepcopy(original), deepcopy(original)
    date_gap_identity(expected, code, raw, rows, 'owner', started_at=START)
    row_ids = [id(row) for row in actual['issues']]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    def forbidden(*args):
        raise AssertionError('date-gap enrichment reached full assignment scan')
    monkeypatch.setattr(evidence, '_driver_assignment', forbidden)
    date_gap_identity(actual, code, raw, rows, 'owner', started_at=START, assignment_index=view)
    assert actual == expected
    assert [id(row) for row in actual['issues']] == row_ids
    assert actual['issues'][1] == original['issues'][1] and actual['issues'][3] == original['issues'][3]


def test_optional_index_does_not_move_assignment_error_before_canonical_error():
    rows = [assignment(user_id='foreign')]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    source = {'user_id': 'owner', 'raw_by_source': {'salla_direct': None}}
    kwargs = dict(source=source, owner='owner', assignments=rows, setup={},
                  started_at='2026-10-05T00:00:00Z', as_of='2026-10-10T00:00:00Z')
    assert outcome(lambda: evidence.resolve_delivery_event({}, **kwargs, assignment_index=view)) == (
        'error', ValueError, 'operational_delivery_canonical_source_missing')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['old_driver', 'old_courier', 'new_native_driver', 'new_contract_driver', 'new_courier'])
@pytest.mark.parametrize('replay', [False, True])
async def test_whole_collector_reconciliation_and_reports_match_legacy_assignment_views(monkeypatch, kind, replay):
    import operational_balance_sources as sources
    from operational_balance_engine import reconcile
    from operational_balance_service import report
    from operational_financial_position import build_financial_position
    from test_operational_balance_delivery_cutover import HardBoundaryDB, active, courier_data, driver_data, START, NOW
    from test_operational_balance_delivery_start_rule import undated
    data = courier_data() if kind == 'old_courier' else driver_data() if kind == 'old_driver' else undated(kind[4:])
    state = active()
    monkeypatch.setattr('operational_financial_activity.now', lambda: NOW)
    if replay:
        first = await sources.collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=state)
        state = reconcile(state, first, NOW)
    original_data, original_state = deepcopy(data), deepcopy(state)
    builds, calls = [], []
    builder = indexed.build_delivery_assignment_index
    resolver = indexed.DeliveryAssignmentIndex.resolve
    raw_view = indexed.active_assignments_for_number
    def build(rows, owner):
        value = builder(rows, owner)
        builds.append(value)
        return value
    def resolve(self, oid, number):
        calls.append('strict')
        return resolver(self, oid, number)
    def active_rows(*args, **kwargs):
        calls.append('raw')
        return raw_view(*args, **kwargs)
    monkeypatch.setattr(sources, 'build_delivery_assignment_index', build)
    monkeypatch.setattr(sources, 'active_assignments_for_number', active_rows)
    monkeypatch.setattr(indexed.DeliveryAssignmentIndex, 'resolve', resolve)
    actual_db = HardBoundaryDB(data)
    actual = await sources.collect_sources(actual_db, 'owner', START, NOW, operational_state=state)
    assert len(builds) == 1 and builds[0] is not None and calls
    monkeypatch.setattr(sources, 'build_delivery_assignment_index', lambda rows, owner: None)
    expected_db = HardBoundaryDB(data)
    expected = await sources.collect_sources(expected_db, 'owner', START, NOW, operational_state=state)
    assert actual == expected and actual_db.reads == expected_db.reads
    actual_state, expected_state = reconcile(state, actual, NOW), reconcile(state, expected, NOW)
    assert actual_state == expected_state
    assert report(actual_state, as_of=NOW) == report(expected_state, as_of=NOW)
    assert build_financial_position(actual_state, generated_at=NOW) == build_financial_position(expected_state, generated_at=NOW)
    assert data == original_data and state == original_state


@pytest.mark.asyncio
async def test_collector_builds_one_new_assignment_index_per_invocation(monkeypatch):
    import operational_balance_sources as sources
    from test_operational_balance_delivery_cutover import HardBoundaryDB, active, driver_data, START, NOW
    data, state, builds = driver_data(), active(), []
    def build(rows, owner):
        view = indexed.build_delivery_assignment_index(rows, owner)
        builds.append(view)
        return view
    monkeypatch.setattr(sources, 'build_delivery_assignment_index', build)
    for _ in range(2):
        await sources.collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=state)
    assert len(builds) == 2 and builds[0] is not None and builds[1] is not builds[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['disconnect', 'deadline'])
async def test_actual_visible_collector_joins_assignment_worker_and_releases_own_lease(monkeypatch, stop):
    from fastapi import HTTPException
    import operational_balance_sources as sources
    import operational_demand_refresh as runtime
    from test_operational_balance_demand_refresh import DB as LeaseDB, Request, allowed
    from test_operational_balance_delivery_cutover import HardBoundaryDB, active, courier_data, START, NOW
    data, state = courier_data(), active()
    data['store_delivery_assignments'] = cohort(1000)
    before = deepcopy((data, state))
    source_db, lease_db, request = HardBoundaryDB(data), LeaseDB(), Request()
    class Database:
        def __getitem__(self, name):
            return lease_db[name] if name == runtime.LEASES else source_db[name]
    entered, release, exited = Event(), Event(), Event()
    checkpoint, builder, visits = indexed._checkpoint, sources.build_delivery_assignment_index, 0
    def controlled_checkpoint():
        nonlocal visits
        visits += 1
        if visits == 10:
            entered.set()
            assert release.wait(3)
        checkpoint()
    def build(rows, owner):
        try:
            return builder(rows, owner)
        finally:
            exited.set()
    monkeypatch.setattr(indexed, '_checkpoint', controlled_checkpoint)
    monkeypatch.setattr(sources, 'build_delivery_assignment_index', build)
    if stop == 'deadline':
        monkeypatch.setattr(runtime, 'REQUEST_SECONDS', 0.15)
    task = asyncio.create_task(runtime.run_visible_refresh(request, Database(), 'owner', 'staff', allowed,
        lambda: sources.collect_sources(source_db, 'owner', START, NOW, operational_state=state)))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if stop == 'disconnect':
            request.disconnect()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(0.2)
        assert not task.done() and not exited.is_set()
        release.set()
        with pytest.raises(HTTPException) as failure:
            await asyncio.wait_for(task, 2)
        assert failure.value.status_code == (499 if stop == 'disconnect' else 408)
        assert visits == 10 and exited.is_set() and (data, state) == before
        assert lease_db.data[runtime.LEASES] == {}
        assert all(name == runtime.LEASES for name, *_ in lease_db.writes)
        assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
    finally:
        release.set()
