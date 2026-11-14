"""Exact legacy evidence/error parity and bounded attempt-local history work."""
import asyncio
from copy import deepcopy
from decimal import localcontext
from pathlib import Path
import sys
from threading import Event

import pytest

import operational_courier_fee_history as history
from operational_delivery_evidence import recorded_courier_fee
from operational_financial_activity_runtime import prepare_in_thread


def state(facts=None, obligations=None, movements=None):
    return {'owner_id': 'owner', 'engine': {'facts': facts if facts is not None else {},
            'obligations': obligations if obligations is not None else {}},
            'movements': movements if movements is not None else []}


def shipping(confirmed='0'):
    return {'kind': 'shipping', 'confirmed': confirmed}


def movement(*allocations):
    return {'allocations': list(allocations)}


def allocation(key='shipping:a', amount='0'):
    return {'obligation_id': key, 'amount': amount}


def outcome(function):
    with localcontext() as context:
        context.clear_flags()
        try:
            value = ('result', function())
        except Exception as error:
            value = ('error', type(error), str(error))
        return value, {flag.__name__ for flag, active in context.flags.items() if active}


def parity(value, order_id='a'):
    before = deepcopy(value)
    lookup = history.build_recorded_courier_fee_lookup(value)
    assert outcome(lambda: lookup(order_id)) == outcome(lambda: recorded_courier_fee(value, order_id))
    assert value == before


@pytest.mark.parametrize('cost', [0, False, '', [], {}, '0', 'NaN'])
def test_direct_cost_short_circuit_keeps_every_non_none_value(cost):
    value = state({'shipping:a': {'cost': cost}, 4: {}}, movements=[None])
    parity(value)
    assert history.build_recorded_courier_fee_lookup(value)('a') is True


@pytest.mark.parametrize('proof', [None, False, 0, '', [], {}, {'bad': True}])
def test_fee_key_presence_never_requires_truthy_or_well_formed_value(proof):
    parity(state({'shipping_fee:a': proof}, obligations={'shipping:a': None}, movements=[None]))


@pytest.mark.parametrize('facts', [
    {'shipping:a': None, 'shipping_fee:a': {}},
    {4: {}, 'shipping:a:base': {}},
    {'shipping:a:base': {}, 4: {}},
    {'unrelated': None},
    {'shipping:a:base': None},
    {'shipping:a': {}, 'shipping_fee:a': None},
])
def test_fallback_preserves_fact_access_and_prefix_exception_order(facts):
    parity(state(facts))


@pytest.mark.parametrize('order_id', ['', 'a', 'a:', 'a:b', 'a:b:', 'a:b:c', 'a::b', ':b', 'aa'])
def test_exact_prefix_semantics_include_colons_and_empty_segments(order_id):
    parity(state({'shipping:a:b': {'cost': None}, 'shipping:a:b:c': {}, 'shipping::b': {}},
                 {'shipping:a::b': shipping('1'), 'shipping:a:b:': shipping('0')}), order_id)


@pytest.mark.parametrize('confirmed', ['0', '1', '-1', 'Infinity', '-Infinity', 'NaN', 'sNaN',
                                       'not-a-number', None, {}, False, True, 0.5])
def test_confirmed_decimal_results_errors_and_flags_are_unchanged(confirmed):
    parity(state(obligations={'shipping:a': shipping(confirmed)}))


@pytest.mark.parametrize('obligations', [
    {'shipping:a:one': shipping('1'), 'shipping:a:two': None},
    {'shipping:a:one': None, 'shipping:a:two': shipping('1')},
    {'unrelated': None},
    {4: shipping('0')},
    {'shipping:a:one': shipping('NaN'), 'shipping:a:two': shipping('1')},
    {'shipping:a:two': shipping('1'), 'shipping:a:one': shipping('NaN')},
    {'shipping:a': {'kind': 'provider', 'confirmed': 'not-a-number'}},
    {'shipping:a': {'kind': 'shipping'}},
])
def test_obligation_set_construction_and_iteration_match_legacy(obligations):
    parity(state(obligations=obligations))


@pytest.mark.parametrize('movements', [
    [None], [{'allocations': None}], [{'allocations': [None]}],
    [{'allocations': [allocation([], '1')]}],
    [{'allocations': [allocation({}, '1')]}],
    [movement(allocation('unrelated', 'not-a-number'))],
    [movement(allocation('shipping:a', '1'), allocation([], '2'))],
    [movement(allocation('shipping:a', '0'), allocation('shipping:a', 'NaN'))],
    [movement(allocation('shipping:a', '1')), None],
    [{'allocations': (allocation('shipping:a', '1'),)}],
    [movement({'amount': 'not-a-number'})],
    [movement(allocation(b'shipping:a', 'not-a-number'))],
])
@pytest.mark.parametrize('has_obligation', [False, True])
def test_allocation_structure_and_unrelated_amounts_keep_legacy_behavior(movements, has_obligation):
    parity(state(obligations={'shipping:a': shipping()} if has_obligation else {}, movements=movements))


@pytest.mark.parametrize('amount', ['0', '-1', '1', 'NaN', 'sNaN', 'Infinity', None, {}, 0.5])
def test_matching_allocation_decimal_is_lazy_and_unchanged(amount):
    parity(state(obligations={'shipping:a': shipping()}, movements=[movement(allocation(amount=amount))]))


def test_allocation_merge_retains_global_movement_order():
    for order in [('NaN', '1'), ('1', 'NaN'), ('0', '1')]:
        value = state(obligations={'shipping:a:z': shipping(), 'shipping:a:b': shipping()}, movements=[
            movement(allocation('unrelated', 'bad'), allocation('shipping:a:z', order[0])),
            movement(allocation('shipping:a:b', order[1])),
        ])
        parity(value)


@pytest.mark.parametrize('value', [None, False, 0, [], {'engine': None}, {'engine': {'facts': None}},
                                  {'engine': {'obligations': []}}, {'movements': None}])
def test_nonstandard_outer_structure_uses_unchanged_reader(value):
    parity(value)


@pytest.mark.parametrize('order_id', [None, False, 0, [], b'a'])
def test_nonstring_order_identity_preserves_original_exception(order_id):
    parity(state(), order_id)


def test_fallback_is_lazy_and_preserves_custom_mapping_access(monkeypatch):
    class Irregular(dict):
        def get(self, *args):
            raise RuntimeError('original mapping access')
    value = Irregular(engine={})
    lookup = history.build_recorded_courier_fee_lookup(value)
    assert outcome(lambda: lookup('a')) == outcome(lambda: recorded_courier_fee(value, 'a'))


def test_no_eager_amount_validation_before_a_direct_proof():
    value = state({'shipping:a': {'cost': False}}, {'shipping:a': shipping('NaN')},
                  [movement(allocation('shipping:a', 'not-a-number'))])
    parity(value)
    assert history.build_recorded_courier_fee_lookup(value)('a') is True


def cohort(size):
    return state({'capture:' + str(i): {'amount': '100'} for i in range(size)},
                 {'shipping:' + str(i): shipping() for i in range(size)},
                 [movement(allocation('provider:' + str(i), '100')) for i in range(size)])


def test_normal_history_lookups_never_scan_through_legacy_fallback(monkeypatch):
    def forbidden(*args):
        raise AssertionError('normal indexed history reached the legacy full scan')
    monkeypatch.setattr(history, 'recorded_courier_fee', forbidden)
    lookup = history.build_recorded_courier_fee_lookup(cohort(100))
    assert [lookup(str(i)) for i in range(100)] == [False] * 100


def line_work(builder, size):
    filenames = {str(Path(history.__file__).resolve()), str(Path(recorded_courier_fee.__code__.co_filename).resolve())}
    count = 0
    def trace(frame, event, _arg):
        nonlocal count
        if event == 'line' and frame.f_code.co_filename in filenames:
            count += 1
        return trace
    value = cohort(size)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        lookup = builder(value)
        assert all(lookup(str(i)) is False for i in range(size))
    finally:
        sys.settrace(previous)
    return count


def test_history_work_scales_with_input_instead_of_orders_times_history():
    small = line_work(history.build_recorded_courier_fee_lookup, 64)
    large = line_work(history.build_recorded_courier_fee_lookup, 128)
    legacy = line_work(lambda value: lambda identity: recorded_courier_fee(value, identity), 128)
    assert large <= small * 2.4, (small, large)
    assert large < legacy / 4, (large, legacy)


def test_each_attempt_builds_an_independent_lookup_after_replacing_history():
    first = cohort(2)
    old = history.build_recorded_courier_fee_lookup(first)
    assert old('0') is False
    later = deepcopy(first)
    later['engine']['facts'] = {'shipping:0:base': {}}
    later['owner_id'] = 'other-owner'
    later['revision'] = 2
    new = history.build_recorded_courier_fee_lookup(later)
    assert new is not old and new('0') is True and old('0') is False
    assert new('1') == recorded_courier_fee(later, '1')


def test_cancellation_during_owned_build_joins_worker_before_return(monkeypatch):
    async def scenario():
        value = cohort(1000)
        before = deepcopy(value)
        entered, release, exited = Event(), Event(), Event()
        original = history._checkpoint
        visits = 0
        def checkpoint():
            nonlocal visits
            visits += 1
            if visits == 10:
                entered.set()
                assert release.wait(3), 'cancel test did not release the owned worker'
            original()
        def build():
            try:
                return history.build_recorded_courier_fee_lookup(value)
            finally:
                exited.set()
        monkeypatch.setattr(history, '_checkpoint', checkpoint)
        task = asyncio.create_task(prepare_in_thread(build))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not exited.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert exited.is_set() and visits == 10
            assert value == before
            assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
        finally:
            release.set()
    asyncio.run(scenario())


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['old_courier', 'old_driver', 'new_courier', 'new_native_driver', 'new_contract_driver'])
@pytest.mark.parametrize('replay', [False, True])
async def test_whole_collector_reconciliation_and_reports_match_legacy(monkeypatch, kind, replay):
    import operational_balance_sources as sources
    from operational_balance_engine import reconcile
    from operational_balance_service import report
    from operational_financial_position import build_financial_position
    from test_operational_balance_delivery_cutover import HardBoundaryDB, active, courier_data, driver_data, START, NOW
    from test_operational_balance_delivery_start_rule import undated
    data = courier_data() if kind == 'old_courier' else driver_data() if kind == 'old_driver' else undated(kind[4:])
    prior = active()
    monkeypatch.setattr('operational_financial_activity.now', lambda: NOW)
    if replay:
        first = await sources.collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=prior)
        prior = reconcile(prior, first, NOW)
    original_data, original_state = deepcopy(data), deepcopy(prior)
    builds, queries = [], []
    def indexed(value):
        builds.append(value)
        lookup = history.build_recorded_courier_fee_lookup(value)
        def query(identity):
            queries.append(identity)
            return lookup(identity)
        return query
    monkeypatch.setattr(sources, 'build_recorded_courier_fee_lookup', indexed)
    indexed_db = HardBoundaryDB(data)
    actual = await sources.collect_sources(indexed_db, 'owner', START, NOW, operational_state=prior)
    assert builds == [prior] and queries, 'collector did not use its one owned history lookup'
    monkeypatch.setattr(sources, 'build_recorded_courier_fee_lookup',
                        lambda value: lambda identity: recorded_courier_fee(value, identity))
    original_db = HardBoundaryDB(data)
    expected = await sources.collect_sources(original_db, 'owner', START, NOW, operational_state=prior)
    assert actual == expected and indexed_db.reads == original_db.reads
    actual_state, expected_state = reconcile(prior, actual, NOW), reconcile(prior, expected, NOW)
    assert actual_state == expected_state
    assert report(actual_state, as_of=NOW) == report(expected_state, as_of=NOW)
    assert build_financial_position(actual_state, generated_at=NOW) == build_financial_position(expected_state, generated_at=NOW)
    assert data == original_data and prior == original_state


@pytest.mark.asyncio
async def test_collector_builds_again_for_each_invocation_even_with_same_state(monkeypatch):
    import operational_balance_sources as sources
    from test_operational_balance_delivery_cutover import HardBoundaryDB, active, courier_data, START, NOW
    value, data, builds = active(), courier_data(), []
    def build(current):
        lookup = history.build_recorded_courier_fee_lookup(current)
        builds.append((current, lookup))
        return lookup
    monkeypatch.setattr(sources, 'build_recorded_courier_fee_lookup', build)
    await sources.collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=value)
    await sources.collect_sources(HardBoundaryDB(data), 'owner', START, NOW, operational_state=value)
    assert len(builds) == 2 and builds[0][0] is builds[1][0] is value
    assert builds[0][1] is not builds[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['disconnect', 'deadline'])
async def test_actual_visible_collector_joins_history_worker_and_releases_only_own_lease(monkeypatch, stop):
    from fastapi import HTTPException
    import operational_balance_sources as sources
    import operational_demand_refresh as runtime
    from test_operational_balance_demand_refresh import DB as LeaseDB, Request, allowed
    from test_operational_balance_delivery_cutover import HardBoundaryDB, active, courier_data, START, NOW
    value, data = active(), courier_data()
    value['engine'] = cohort(1000)['engine']
    before, source_db, lease_db, request = deepcopy(value), HardBoundaryDB(data), LeaseDB(), Request()
    class Database:
        def __getitem__(self, name):
            return lease_db[name] if name == runtime.LEASES else source_db[name]
    entered, release, exited = Event(), Event(), Event()
    checkpoint, builder, visits = history._checkpoint, sources.build_recorded_courier_fee_lookup, 0
    def controlled_checkpoint():
        nonlocal visits
        visits += 1
        if visits == 10:
            entered.set()
            assert release.wait(3), 'collector test did not release its owned worker'
        checkpoint()
    def observed_builder(current):
        try:
            return builder(current)
        finally:
            exited.set()
    monkeypatch.setattr(history, '_checkpoint', controlled_checkpoint)
    monkeypatch.setattr(sources, 'build_recorded_courier_fee_lookup', observed_builder)
    if stop == 'deadline':
        monkeypatch.setattr(runtime, 'REQUEST_SECONDS', 0.15)
    task = asyncio.create_task(runtime.run_visible_refresh(request, Database(), 'owner', 'staff', allowed,
        lambda: sources.collect_sources(source_db, 'owner', START, NOW, operational_state=value)))
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
        assert visits == 10 and exited.is_set()
        assert value == before and lease_db.data[runtime.LEASES] == {}
        assert all(name == runtime.LEASES for name, *_ in lease_db.writes)
        assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
    finally:
        release.set()
