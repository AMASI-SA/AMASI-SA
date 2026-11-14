"""Independent legacy parity and bounded work for one immutable hold history."""
import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path
import sys
from threading import Event

import pytest
from bson.int64 import Int64

import operational_courier_hold_history as history
from operational_balance_store import digest
from operational_financial_activity_runtime import preparation_scope, prepare_in_thread
from operational_historical_shipping_fee import APPROVALS
from operational_shipping_fee_review import hold_prior_courier_event


def event(**changes):
    return {'order_id': 'a', 'order_number': '101', 'party_type': 'courier',
            'party_id': 'courier-a', **changes}


def obligation(**changes):
    return {'party_type': 'courier', 'party_id': 'courier-a', 'currency': 'SAR',
            'direction': 'payable', 'confirmed': '0', **changes}


def allocation(key='pooled', value='1'):
    return {'obligation_id': key, 'amount': value}


def movement(**changes):
    return {'party_type': 'courier', 'party_id': 'courier-a', 'currency': 'SAR',
            'direction': 'outgoing', 'kind': 'payment', 'amount': '1',
            'allocations': [allocation()], **changes}


def state(*, obligations=None, openings=None, movements=None, **changes):
    return {'owner_id': 'owner', 'revision': 1,
            'engine': {'obligations': obligations if obligations is not None else {}},
            'openings': openings if openings is not None else {},
            'movements': movements if movements is not None else [], **changes}


def identities(value):
    if isinstance(value, dict):
        return (id(value), tuple((key, identities(row)) for key, row in value.items()))
    if isinstance(value, list):
        return (id(value), tuple(identities(row) for row in value))
    return id(value)


def outcome(call, precision=28):
    with localcontext() as context:
        context.prec = precision
        context.clear_flags()
        try:
            result = ('result', call())
        except Exception as error:
            result = ('error', type(error), str(error))
        return result, {flag.__name__ for flag, active in context.flags.items() if active}


def parity(value, candidate=None, carrier=None, *, precision=28):
    candidate = event() if candidate is None else candidate
    carrier = {'currency': 'SAR'} if carrier is None else carrier
    before, before_ids = deepcopy(value), identities(value)
    with localcontext() as context:
        context.clear_flags()
        lookup = history.build_courier_hold_lookup(value)
        assert not any(context.flags.values()), 'building must not parse an amount'
    expected = outcome(lambda: hold_prior_courier_event(value, candidate, carrier), precision)
    assert outcome(lambda: lookup(candidate, carrier), precision) == expected
    assert value == before and identities(value) == before_ids
    return expected


@pytest.mark.parametrize('value', [None, False, [], {}, [1], {'engine': None},
    {'engine': {'obligations': None}}, {'openings': None}, {'movements': None},
    {'engine': {'obligations': {'x': None}}}])
def test_outer_structures_keep_original_guard_or_exception(value):
    parity(value)


@pytest.mark.parametrize('candidate', [False, {}, {'party_type': 'store_driver'},
    {'party_type': None}, {'order_id': 'a'}, event(order_id=None), event(order_number=101),
    event(party_id=None), event(party_id=[]), event(party_type=['courier'])])
def test_event_shapes_fall_back_without_changing_short_circuits(candidate):
    parity(state(), candidate)


@pytest.mark.parametrize('saved', [True, {'approval': True}, 'saved', [1]])
def test_saved_approval_precedes_all_malformed_history(saved):
    value = state(**{APPROVALS: {digest('101'): saved}, 'engine': None,
                    'openings': None, 'movements': [None]})
    assert parity(value)[0] == ('result', True)


@pytest.mark.parametrize('saved', [None, False, 0, '', [], {}])
def test_false_approval_does_not_suppress_original_history_failure(saved):
    value = state(movements=[None], **{APPROVALS: {digest('101'): saved}})
    assert parity(value)[0][0] == 'error'


@pytest.mark.parametrize('confirmed', ['0', '1', '-1', '1.001', '1.000', 'NaN', 'sNaN',
    'Infinity', 'not-an-amount', None, {}, False, True, 0.5])
@pytest.mark.parametrize('suffix', ['', ':base_shipping'])
def test_confirmed_amount_errors_flags_and_positive_short_circuit(confirmed, suffix):
    value = state(obligations={'shipping:a' + suffix: obligation(confirmed=confirmed)},
                  movements=[movement(allocations=[allocation('shipping:a', 'invalid')])])
    parity(value)


@pytest.mark.parametrize('confirmed_pair', [('1', 'NaN'), ('NaN', '1'), ('0', '1.000'),
                                          ('1.000', '0'), ('bad', 'bad-too')])
def test_direct_confirmed_set_iteration_matches_the_unmodified_reader(confirmed_pair):
    parity(state(obligations={'shipping:a': obligation(confirmed=confirmed_pair[0]),
                             'shipping:a:base_shipping': obligation(confirmed=confirmed_pair[1])}))


@pytest.mark.parametrize('value', ['0', 0, None, '', 'invalid', 'NaN', {}, False])
@pytest.mark.parametrize('suffix', ['', ':base_shipping'])
def test_any_exact_allocation_holds_without_reading_its_amount(value, suffix):
    prior = state(movements=[movement(party_id='another-party', currency='USD', direction='incoming',
        allocations=[allocation('shipping:a' + suffix, value)])])
    assert parity(prior)[0] == ('result', True)


def test_exact_allocation_without_amount_also_holds():
    assert parity(state(movements=[movement(allocations=[{'obligation_id': 'shipping:a'}])]))[0] == ('result', True)


@pytest.mark.parametrize('changes', [
    {}, {'kind': 'adjustment'}, {'allocations': []}, {'allocations': None},
    {'amount': '2'}, {'party_id': 'another-party'}, {'direction': 'incoming'},
    {'currency': 'USD'}, {'allocations': [{'obligation_id': 'pooled'}]},
    {'allocations': [{'amount': '1'}]}, {'allocations': [allocation('dangling')]},
])
def test_pooled_selection_and_full_payment_validation_match(changes):
    parity(state(obligations={'pooled': obligation()}, movements=[movement(**changes)]))


@pytest.mark.parametrize('amount', ['0', '-1', '1', '1.000', '1.001', 'NaN', 'sNaN',
    'Infinity', None, {}, True, False, 'invalid'])
@pytest.mark.parametrize('field', ['allocation', 'movement'])
def test_pooled_decimal_values_errors_and_flags_remain_lazy(amount, field):
    change = {'allocations': [allocation(value=amount)]} if field == 'allocation' else {'amount': amount}
    parity(state(obligations={'pooled': obligation()}, movements=[movement(**change)]))


@pytest.mark.parametrize('precision', [2, 4, 28])
def test_decimal_context_and_rounding_order_are_not_cached(precision):
    prior = state(obligations={'pooled': obligation()}, movements=[movement(amount='2.00',
        allocations=[allocation(value='1.000'), allocation(value='1.000')])])
    parity(prior, precision=precision)


def test_opening_payable_identity_is_included_without_reading_its_amount():
    prior = state(openings={'opening': obligation(id='pooled', direction='for_party', amount='NaN')},
                  movements=[movement()])
    assert parity(prior)[0] == ('result', False)


def test_present_none_opening_identity_can_cover_a_missing_allocation_identity():
    prior = state(openings={'opening': obligation(id=None, direction='for_party')},
                  movements=[movement(allocations=[{'amount': '1'}])])
    assert parity(prior)[0] == ('result', False)


@pytest.mark.parametrize('row', [None, obligation(direction='for_party'),
    obligation(id=[], direction='for_party'), obligation(id='pooled', currency=[]),
    obligation(id='pooled', party_id={})])
def test_malformed_opening_failure_precedes_even_an_exact_target_allocation(row):
    prior = state(openings={'opening': row},
                  movements=[movement(allocations=[allocation('shipping:a')])])
    parity(prior)


def test_unknown_fee_currency_uses_all_payout_currencies_but_only_unknown_payables():
    prior = state(obligations={'pooled': obligation(currency='USD')}, movements=[movement(currency='USD')])
    assert parity(prior, carrier={'currency': 'USD'})[0] == ('result', False)
    assert parity(prior, carrier={'currency': None})[0] == ('result', True)
    lookup = history.build_courier_hold_lookup(prior)
    assert outcome(lambda: lookup(event(), None)) == outcome(lambda: hold_prior_courier_event(prior, event(), None))


@pytest.mark.parametrize('first', ['pooled', 'direct', 'same_movement'])
def test_original_movement_order_controls_decimal_flags_before_short_circuit(first):
    invalid = movement(allocations=[allocation(value='not-an-amount')])
    direct = movement(allocations=[allocation('shipping:a', 'not-an-amount')])
    rows = [invalid, direct] if first == 'pooled' else [direct, invalid]
    if first == 'same_movement':
        rows = [movement(allocations=[allocation(value='not-an-amount'), allocation('shipping:a')])]
    result, flags = parity(state(obligations={'pooled': obligation()}, movements=rows))
    assert result == ('result', True)
    assert bool(flags) == (first == 'pooled')


def test_balanced_earlier_movement_retains_flags_before_later_direct_allocation():
    prior = state(obligations={'pooled': obligation()}, movements=[
        movement(allocations=[allocation(value='1.000')]),
        movement(allocations=[allocation('shipping:a')]),
    ])
    result, flags = parity(prior)
    assert result == ('result', True) and 'Rounded' in flags


@pytest.mark.parametrize('rows', [[None], [{'allocations': [None]}],
    [movement(allocations=[allocation([])])], [movement(allocations=[allocation({})])],
    [movement(allocations=(allocation(),))], [movement(allocations=False)],
    [movement(allocations=[allocation('shipping:a')]), None],
    [movement(allocations=[allocation('shipping:a'), allocation([])])],
    [movement(allocations=[allocation([]), allocation('shipping:a')])]])
def test_unusual_movement_shapes_use_original_error_and_short_circuit_order(rows):
    parity(state(movements=rows))


def test_custom_mapping_is_not_observed_during_build():
    class Irregular(dict):
        def get(self, *args):
            raise RuntimeError('original mapping access')
    prior = Irregular(engine={})
    lookup = history.build_courier_hold_lookup(prior)
    assert outcome(lambda: lookup(event(), None)) == outcome(lambda: hold_prior_courier_event(prior, event(), None))


@pytest.mark.parametrize('value', [None, 0, False, 1, True, Int64(0), Int64(7), '7'])
def test_ordinary_bson_scalars_in_unrelated_history_do_not_disable_the_index(monkeypatch, value):
    prior = state(obligations={'other': obligation(party_type='supplier', party_id=value)},
        openings={'opening': obligation(party_type='supplier', party_id=value,
                                       id=value, direction='for_party')},
        movements=[movement(party_type='supplier', party_id=value, allocations=[allocation(value)])])
    assert parity(prior)[0] == ('result', False)
    def forbidden(*args):
        raise AssertionError('ordinary BSON scalar forced a whole-history fallback')
    monkeypatch.setattr(history, 'hold_prior_courier_event', forbidden)
    lookup = history.build_courier_hold_lookup(prior)
    assert lookup(event(), {'currency': 'SAR'}) is False


@pytest.mark.parametrize('left,right', [(True, 1), (False, 0), (Int64(1), 1),
    (Int64(0), False), (1, Int64(1)), (None, None)])
def test_scalar_grouping_keeps_native_equality_without_string_conversion(monkeypatch, left, right):
    prior = state(openings={'opening': obligation(party_id=left, currency=left,
                    id=left, direction='for_party')},
                  movements=[movement(party_id=right, currency=right,
                                      allocations=[allocation(right)])])
    candidate, carrier = event(party_id=right), {'currency': right}
    assert parity(prior, candidate, carrier)[0] == ('result', False)
    def forbidden(*args):
        raise AssertionError('native-equal scalar selectors reached fallback')
    monkeypatch.setattr(history, 'hold_prior_courier_event', forbidden)
    assert history.build_courier_hold_lookup(prior)(candidate, carrier) is False


@pytest.mark.parametrize('left,right', [(Int64(1), '1'), (True, 'True'), (False, 'False'),
                                       (None, 'None'), (0, '0')])
def test_scalar_values_that_only_share_a_text_representation_remain_distinct(left, right):
    prior = state(movements=[movement(party_id=left, allocations=[])])
    assert parity(prior, event(party_id=right))[0] == ('result', False)


@pytest.mark.parametrize('first', ['pooled', 'direct', 'same_movement'])
def test_native_scalar_groups_keep_exact_target_and_pooled_movement_order(first):
    invalid = movement(party_id=Int64(1), currency=Int64(1),
                       allocations=[allocation(Int64(1), 'not-an-amount')])
    direct = movement(party_id=1, currency=True, allocations=[allocation('shipping:a', 'bad')])
    rows = [invalid, direct] if first == 'pooled' else [direct, invalid]
    if first == 'same_movement':
        rows = [movement(party_id=True, currency=1,
            allocations=[allocation(Int64(1), 'not-an-amount'), allocation('shipping:a')])]
    prior = state(openings={'opening': obligation(party_id=Int64(1), currency=1,
                    id=True, direction='for_party')}, movements=rows)
    result, flags = parity(prior, event(party_id=1), {'currency': True})
    assert result == ('result', True) and bool(flags) == (first == 'pooled')


@pytest.mark.parametrize('value', [1.0, float('nan'), Decimal('1'), Decimal('NaN')])
def test_float_decimal_and_nonreflexive_selectors_keep_the_original_fallback(monkeypatch, value):
    prior = state(obligations={'other': obligation(party_type='supplier', party_id=value)})
    calls = []
    def observed(*args):
        calls.append(args)
        return hold_prior_courier_event(*args)
    monkeypatch.setattr(history, 'hold_prior_courier_event', observed)
    lookup = history.build_courier_hold_lookup(prior)
    assert not calls
    assert outcome(lambda: lookup(event(), {'currency': 'SAR'})) == outcome(
        lambda: hold_prior_courier_event(prior, event(), {'currency': 'SAR'}))
    assert len(calls) == 1


def test_large_integer_selector_never_uses_string_conversion_or_truncation(monkeypatch):
    value = 1 << 20000
    prior = state(openings={'opening': obligation(party_id=value, id=value, direction='for_party')},
                  movements=[movement(party_id=value, allocations=[allocation(value)])])
    assert parity(prior, event(party_id=value))[0] == ('result', False)
    def forbidden(*args):
        raise AssertionError('hashable integer was normalized or rejected')
    monkeypatch.setattr(history, 'hold_prior_courier_event', forbidden)
    assert history.build_courier_hold_lookup(prior)(event(party_id=value), {'currency': 'SAR'}) is False


def test_missing_event_party_key_retains_the_empty_history_short_circuit():
    candidate = event()
    del candidate['party_id']
    assert parity(state(), candidate)[0] == ('result', False)


def test_custom_record_key_does_not_raise_before_saved_approval_short_circuit():
    class ComparisonKey:
        comparisons = 0
        def __hash__(self):
            return hash('party_type')
        def __eq__(self, other):
            type(self).comparisons += 1
            raise RuntimeError('original key comparison')
    key = ComparisonKey()
    row = {key: 'unread'}
    prior = state(obligations={'pooled': row}, **{APPROVALS: {digest('101'): {'approved': True}}})
    lookup = history.build_courier_hold_lookup(prior)
    assert lookup(event(), None) is hold_prior_courier_event(prior, event(), None) is True
    assert ComparisonKey.comparisons == 0 and prior['engine']['obligations']['pooled'] is row


def cohort(size):
    return state(obligations={'pool:' + str(i): obligation(party_id='other:' + str(i)) for i in range(size)},
        openings={'opening:' + str(i): obligation(id='opening:' + str(i), direction='for_party',
                    party_id='other:' + str(i)) for i in range(size)},
        movements=[movement(party_id='other:' + str(i), allocations=[allocation('pool:' + str(i))])
                   for i in range(size)])


def test_normal_history_uses_no_legacy_rescan_and_keeps_original_objects(monkeypatch):
    prior = cohort(100)
    before = identities(prior)
    def forbidden(*args):
        raise AssertionError('ordinary history reached full legacy scan')
    monkeypatch.setattr(history, 'hold_prior_courier_event', forbidden)
    lookup = history.build_courier_hold_lookup(prior)
    assert lookup.state is prior and lookup.obligations is prior['engine']['obligations']
    assert all(lookup(event(order_id=str(i)), {'currency': 'SAR'}) is False for i in range(100))
    assert identities(prior) == before


def line_work(builder, size):
    filenames = {str(Path(history.__file__).resolve()), str(Path(hold_prior_courier_event.__code__.co_filename).resolve())}
    visits = 0
    def trace(frame, event_name, _arg):
        nonlocal visits
        if event_name == 'line' and frame.f_code.co_filename in filenames:
            visits += 1
        return trace
    prior, previous = cohort(size), sys.gettrace()
    try:
        sys.settrace(trace)
        lookup = builder(prior)
        assert all(lookup(event(order_id=str(i)), {'currency': 'SAR'}) is False for i in range(size))
    finally:
        sys.settrace(previous)
    return visits


def test_work_growth_removes_unrelated_full_history_per_order_with_negative_control():
    small = line_work(history.build_courier_hold_lookup, 64)
    large = line_work(history.build_courier_hold_lookup, 128)
    legacy_small = line_work(lambda prior: lambda event, carrier: hold_prior_courier_event(prior, event, carrier), 64)
    legacy_large = line_work(lambda prior: lambda event, carrier: hold_prior_courier_event(prior, event, carrier), 128)
    assert large <= small * 2.4, (small, large)
    assert legacy_large > legacy_small * 3.5, (legacy_small, legacy_large)
    assert large < legacy_large / 4, (large, legacy_large)


def test_each_attempt_has_its_own_lookup_for_owner_revision_and_replaced_history():
    first = cohort(2)
    old = history.build_courier_hold_lookup(first)
    later = deepcopy(first)
    later['owner_id'], later['revision'] = 'other-owner', 2
    later['movements'] = [movement(allocations=[allocation('shipping:a', '0')])]
    new = history.build_courier_hold_lookup(later)
    same_state_new_attempt = history.build_courier_hold_lookup(first)
    assert new is not old and same_state_new_attempt is not old
    assert old(event(), None) is False and new(event(), None) is True
    assert new.state is later and old.state is first


def test_cancellation_during_owned_build_joins_the_worker(monkeypatch):
    async def scenario():
        prior, entered, release, exited = cohort(1000), Event(), Event(), Event()
        before, checkpoint, visits = deepcopy(prior), history._checkpoint, 0
        def controlled_checkpoint():
            nonlocal visits
            visits += 1
            if visits == 10:
                entered.set()
                assert release.wait(3), 'test did not release the owned worker'
            checkpoint()
        def build():
            try:
                return history.build_courier_hold_lookup(prior)
            finally:
                exited.set()
        monkeypatch.setattr(history, '_checkpoint', controlled_checkpoint)
        task = asyncio.create_task(prepare_in_thread(build))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not exited.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert exited.is_set() and visits == 10 and prior == before
            assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
        finally:
            release.set()
    asyncio.run(scenario())


def test_cancellation_while_reading_a_relevant_payout_does_not_escape_as_a_hold(monkeypatch):
    prior = state(obligations={'pooled': obligation()}, movements=[movement() for _ in range(100)])
    before, stop = deepcopy(prior), Event()
    lookup = history.build_courier_hold_lookup(prior)
    checkpoint, visits = history._checkpoint, 0
    def controlled_checkpoint():
        nonlocal visits
        visits += 1
        if visits == 5:
            stop.set()
        checkpoint()
    monkeypatch.setattr(history, '_checkpoint', controlled_checkpoint)
    with preparation_scope(stop), pytest.raises(asyncio.CancelledError):
        lookup(event(), {'currency': 'SAR'})
    assert visits == 5 and prior == before
