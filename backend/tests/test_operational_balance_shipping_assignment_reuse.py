"""Fee producers reuse one request's raw assignment view without changing proof."""
import asyncio
from copy import deepcopy
from contextlib import nullcontext
import sys
from time import perf_counter
from unittest.mock import patch

from bson.int64 import Int64
import pytest

import operational_delivery_assignment_index as indexed
from operational_completed_shipping import deferred_source_event
from operational_courier_tariffs import with_tariffs
from operational_delivery_evidence import _instant
from operational_prepaid_shipping import source_event
from test_operational_balance_delivery_cutover import START, NOW
from test_operational_balance_prepaid_shipping import fixture


def legacy_any(rows, number):
    # Frozen raw-number predicate from production c3c1cd26d93ab24d6e08003278b9910526055cc7.
    return any(str(row.get('order_number')) == number and row.get('active') is not False
               and row.get('status') in {'assigned', 'out_for_delivery', 'delivered'} for row in rows)


def assignment(**changes):
    return {'id': 'a', 'user_id': 'owner', 'order_id': '100', 'order_number': '10',
            'driver_id': 'driver', 'active': True, 'status': 'delivered', **changes}


def outcome(call):
    try:
        return 'value', call()
    except Exception as error:
        return 'error', type(error), str(error)


@pytest.mark.parametrize('rows,number', [
    ([], '10'), ([assignment()], '10'), ([assignment(order_number=' 10 ')], '10'),
    ([assignment(order_number=' 10 ')], ' 10 '), ([assignment(order_number=10)], '10'),
    ([assignment(order_number=Int64(10))], '10'), ([assignment(order_number=None)], 'None'),
    ([assignment(order_number=False)], 'False'), ([assignment(order_number=0)], '0'),
    ([assignment(active=False)], '10'), ([assignment(active=0)], '10'),
    ([assignment(active=None)], '10'), ([assignment(status='DELIVERED')], '10'),
    ([assignment(user_id='other')], '10'),
    ([assignment(), assignment(status=[])], '10'),
    ([assignment(status=[]), assignment()], '10'),
    ([assignment(order_number='other', status=[]), assignment()], '10'),
    ([assignment(), None], '10'), ([None, assignment()], '10'),
    ([assignment(order_number=['10'])], "['10']"),
    ([assignment(), assignment()], '10'),
])
def test_boolean_lookup_matches_original_short_circuit_and_raw_identity(rows, number):
    before = deepcopy(rows)
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    expected = outcome(lambda: legacy_any(rows, number))
    actual = outcome(lambda: indexed.has_active_assignment_for_number(
        rows, number, owner='owner', assignment_index=view))
    assert actual == expected
    assert rows == before


def producer(kind, rows):
    data, state = fixture(old=False)
    if kind == 'deferred':
        state['courier_tariffs'][0]['payment_mode'] = 'deferred'
        state['courier_tariffs'][0].pop('cash_account')
    data['store_delivery_assignments'] = rows
    setup, conflicts = with_tariffs({}, 'owner', state)
    assert conflicts == []
    source = data['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    args = (data, setup, source, raw, state, 'owner', _instant(START), _instant(NOW), (), set())
    return source_event if kind == 'prepaid' else deferred_source_event, args


@pytest.mark.parametrize('kind', ['prepaid', 'deferred'])
@pytest.mark.parametrize('rows', [[], [assignment()], [assignment(active=False)],
    [assignment(order_number='other', status=[])], [assignment(), assignment(status=[])],
    [assignment(status=[]), assignment()], [assignment(user_id='other')], [None]])
def test_actual_fee_producer_preserves_results_errors_and_inputs(kind, rows):
    call, args = producer(kind, rows)
    before = deepcopy(args)
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    expected = outcome(lambda: call(*args, review_only=True))
    actual = outcome(lambda: call(*args, review_only=True, assignment_index=view))
    assert actual == expected
    assert args == before
    if not rows:
        event = actual[1][1] if kind == 'prepaid' else actual[1]
        assert actual[0] == 'value' and event['amount'] == '34.50'


@pytest.mark.parametrize('mismatch', ['copied_rows', 'owner', 'length'])
def test_incompatible_index_uses_original_assignment_cohort(mismatch):
    rows = [assignment()]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    owner = 'owner'
    if mismatch == 'copied_rows':
        rows = [assignment(active=False)]
    elif mismatch == 'owner':
        owner = 'other'
    else:
        rows.append(assignment(order_number='other', status=[]))
    assert indexed.has_active_assignment_for_number(rows, '10', owner=owner,
        assignment_index=view) == legacy_any(rows, '10')


def test_boolean_lookup_keeps_cancellation_checkpoint(monkeypatch):
    rows = [assignment(order_number='other')]
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    def stop():
        raise asyncio.CancelledError()
    monkeypatch.setattr(indexed, '_checkpoint', stop)
    with pytest.raises(asyncio.CancelledError):
        indexed.has_active_assignment_for_number(rows, '10', owner='owner', assignment_index=view)


@pytest.mark.parametrize('empty', [False, True])
def test_prepaid_unusual_reference_keeps_lazy_conversion_and_original_error_order(empty):
    class BadText:
        def __str__(self):
            raise ValueError('unusual_reference_conversion')
    rows = [] if empty else [assignment(order_number='other')]
    call, args = producer('prepaid', rows)
    raw = args[3]
    raw.update(reference_id=BadText(), status='pending')
    view = indexed.build_delivery_assignment_index(rows, 'owner')
    expected = outcome(lambda: call(*args, review_only=True))
    actual = outcome(lambda: call(*args, review_only=True, assignment_index=view))
    assert actual == expected
    assert actual == (('value', (True, None)) if empty else
                      ('error', ValueError, 'unusual_reference_conversion'))


@pytest.mark.parametrize('incompatible', ['other_cohort', 'other_owner', 'wrong_type'])
def test_prepaid_incompatible_index_does_not_eagerly_read_reference(incompatible):
    def check(use_index):
        rows = []
        call, args = producer('prepaid', rows)
        raw = args[3]
        raw.update(reference_id='unrelated-before-row-read', status='pending')
        view = indexed.build_delivery_assignment_index(
            rows if incompatible == 'other_owner' else [],
            'other' if incompatible == 'other_owner' else 'owner')
        if incompatible == 'wrong_type':
            view = object()
        class LazyRow(dict):
            def get(self, key, default=None):
                if key == 'order_number':
                    raw['reference_id'] = '10'
                return super().get(key, default)
        rows.append(LazyRow(assignment()))
        return call(*args, review_only=True, assignment_index=view if use_index else None)
    assert check(False) == check(True) == (False, None)


def producer_work(kind, calls, assignments, *, use_index, instrument=False):
    """Plain timing and separate C-level dict.get counts; no DB or network."""
    rows = [assignment(id=str(i), order_id='unrelated-' + str(i),
                       order_number='unrelated-' + str(i)) for i in range(assignments)]
    call, args = producer(kind, rows)
    row_ids = {id(row) for row in rows}
    visits = 0
    def profile(_frame, event, function):
        nonlocal visits
        if (event == 'c_call' and getattr(function, '__name__', '') == 'get'
                and id(getattr(function, '__self__', None)) in row_ids):
            visits += 1
    previous = sys.getprofile()
    if instrument:
        sys.setprofile(profile)
    started = perf_counter()
    try:
        # Timing the candidate's cooperative fallback would inflate the old
        # baseline. Use the production predicate with no added checkpoints.
        baseline = (patch.object(indexed, 'has_active_assignment_for_number',
            lambda rows, number, **kwargs: legacy_any(rows, number))
            if kind == 'deferred' and not use_index else nullcontext())
        with baseline:
            view = indexed.build_delivery_assignment_index(rows, 'owner') if use_index else None
            result = None
            for _ in range(calls):
                result = call(*args, review_only=True, assignment_index=view)
            elapsed = perf_counter() - started
    finally:
        if instrument:
            sys.setprofile(previous)
    event = result[1] if kind == 'prepaid' else result
    assert event['amount'] == '34.50'
    return {'row_get_calls': visits, 'elapsed_seconds': elapsed, 'event': event}


@pytest.mark.parametrize('kind', ['prepaid', 'deferred'])
def test_producer_work_is_linear_and_old_scan_fails_same_budget(kind):
    small = producer_work(kind, 64, 64, use_index=True, instrument=True)
    large = producer_work(kind, 128, 128, use_index=True, instrument=True)
    legacy = producer_work(kind, 128, 128, use_index=False, instrument=True)
    assert small['event'] == large['event'] == legacy['event']
    assert large['row_get_calls'] <= small['row_get_calls'] * 2.1
    # Negative control: the unindexed actual producer violates this same work budget.
    budget = 128 * 12
    assert large['row_get_calls'] <= budget < legacy['row_get_calls']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['prepaid', 'deferred'])
async def test_collector_passes_one_attempt_index_and_preserves_full_report(monkeypatch, kind):
    import operational_balance_sources as sources
    from operational_balance_engine import reconcile
    from operational_balance_service import report
    from operational_financial_position import build_financial_position
    from test_operational_balance_sources import HardBoundaryDB
    call, args = producer(kind, [assignment(order_number='other')])
    data, _, _, _, state = args[:5]
    before_data, before_state = deepcopy(data), deepcopy(state)
    original_helper = indexed.has_active_assignment_for_number
    views = []
    def observe(rows, number, *, owner, assignment_index=None):
        views.append(assignment_index)
        return original_helper(rows, number, owner=owner, assignment_index=assignment_index)
    monkeypatch.setattr(indexed, 'has_active_assignment_for_number', observe)
    monkeypatch.setattr('operational_financial_activity.now', lambda: NOW)
    actual_db = HardBoundaryDB(data)
    actual = await sources.collect_sources(actual_db, 'owner', START, NOW, operational_state=state)
    assert views and all(type(view) is indexed.DeliveryAssignmentIndex for view in views)
    assert len({id(view) for view in views}) == 1
    monkeypatch.setattr(sources, 'build_delivery_assignment_index', lambda rows, owner: None)
    expected_db = HardBoundaryDB(data)
    expected = await sources.collect_sources(expected_db, 'owner', START, NOW, operational_state=state)
    assert actual == expected and actual_db.reads == expected_db.reads
    after, legacy = reconcile(state, actual, NOW), reconcile(state, expected, NOW)
    assert after == legacy
    assert report(after, as_of=NOW) == report(legacy, as_of=NOW)
    assert build_financial_position(after, generated_at=NOW) == build_financial_position(legacy, generated_at=NOW)
    assert data == before_data and state == before_state
