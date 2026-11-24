"""Retained payment lookup work is linear without changing financial proof."""
import asyncio
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path
import pickle
import sys
from threading import Event

from bson.int64 import Int64
import pytest

import operational_customer_payment_basis as basis
import operational_retained_payment_index as routing
from operational_financial_activity_runtime import prepare_in_thread
from test_operational_customer_payment_basis import AS_OF, CREATED, retained_fixture


def evaluate(raw, source, state, index=None, observe=False):
    extra = {'issues': [{'code': 'provider_refund_execution_evidence_required'}]} if observe else {}
    function = basis.retained_adjustment_observation if observe else basis.retained_confirmed_paid_basis
    return function(raw, 'SAR', raw['total'], CREATED, AS_OF, owner='owner',
        order_number=raw['reference_id'], provider_order_id=raw['id'], source=source,
        state=state, fact_index=index, **extra)


def outcome(operation):
    with localcontext() as context:
        context.clear_flags()
        try:
            result = ('value', operation())
        except Exception as error:
            result = ('error', type(error), str(error))
        return result, {flag.__name__ for flag, active in context.flags.items() if active}


def renamed(value, ordinal):
    if type(value) is str:
        for old, new in [('synthetic-adjustment', 'order-' + str(ordinal)),
                         ('synthetic-order', 'number-' + str(ordinal)),
                         ('synthetic-capture', 'capture-' + str(ordinal))]:
            value = value.replace(old, new)
        return value
    if type(value) is dict:
        return {renamed(key, ordinal): renamed(row, ordinal) for key, row in value.items()}
    if type(value) is list:
        return [renamed(row, ordinal) for row in value]
    return value


def workload(size):
    engine = {'facts': {}, 'obligations': {}, 'provider_fee_inputs': {'orders': {}}, 'order_versions': {}}
    state, orders = {'owner_id': 'owner', 'revision': 7, 'engine': engine}, []
    raw0, source0, state0 = retained_fixture()
    for i in range(size):
        raw, source, local = (renamed(value, i) for value in (raw0, source0, state0))
        oid = raw['id']
        engine['facts'].update(local['engine']['facts'])
        engine['obligations'].update(local['engine']['obligations'])
        previous = local['engine']['provider_fee_inputs']['orders'][oid]
        previous.update(currency='SAR', order_number=raw['reference_id'], updated_at=CREATED)
        engine['provider_fee_inputs']['orders'][oid] = previous
        engine['order_versions'][oid] = CREATED
        if i % 2:
            engine['facts']['refund:salla:refund-' + str(i)] = {'order_id': oid, 'amount': '0.80'}
            engine['obligations']['provider:' + oid]['confirmed'] = '199.20'
        for j in range(8):
            engine['facts']['unrelated:' + str(i) + ':' + str(j)] = {'order_id': oid, 'amount': '1.00'}
        orders.append((raw, source))
    return orders, state


def measured(size, indexed):
    files = {str(Path(module.__file__).resolve()) for module in (basis, routing)}
    work = 0

    def trace(frame, event, _argument):
        nonlocal work
        if event == 'line' and frame.f_code.co_filename in files:
            work += 1
        return trace

    orders, state = workload(size)
    original = pickle.dumps(state, protocol=5)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with routing.RetainedPaymentFactScope(state) as scope:
            index = scope.build() if indexed else None
            if indexed:
                assert index is not None
            results = [evaluate(raw, source, state, index, True) for raw, source in orders]
            scope.validate()
    finally:
        sys.settrace(previous)
    assert all(value is not None for value in results)
    assert pickle.dumps(state, protocol=5) == original
    return work, results


def test_attempt_work_does_not_multiply_reviewed_orders_by_the_full_fact_ledger():
    small, _ = measured(64, True)
    large, actual = measured(128, True)
    legacy, expected = measured(128, False)
    assert actual == expected
    assert large <= small * 2.4, (small, large)
    assert large < legacy / 4, (large, legacy)


@pytest.mark.parametrize('observe', [False, True])
@pytest.mark.parametrize('change', ['valid', 'refund', 'page_only', 'capture_and_page',
    'duplicate_capture_evidence', 'foreign_provider', 'foreign_currency', 'wrong_reference',
    'changed_claim', 'foreign_owner', 'stale_source', 'future_source', 'split', 'unrelated_bad_price',
    'other_provider_refund', 'bad_amount', 'decimal_nan'])
def test_original_validator_output_flags_dates_and_proof_hash_are_identical(change, observe):
    orders, state = workload(1)
    raw, source = orders[0]
    oid, capture = raw['id'], 'capture:capture-0'
    facts = state['engine']['facts']
    claim = state['engine']['obligations']['provider:' + oid]
    if change == 'refund':
        facts['refund:salla:known'] = {'order_id': oid, 'amount': '0.80'}
        claim['confirmed'] = '199.20'
    elif change in {'page_only', 'capture_and_page'}:
        facts['provider_order_balance:' + oid] = deepcopy(
            state['engine']['provider_fee_inputs']['orders'][oid]['payment']['order_page_balance'])
        if change == 'page_only':
            del facts[capture]
    elif change == 'duplicate_capture_evidence':
        facts['capture:second-original'] = deepcopy(facts[capture])
    elif change == 'foreign_provider':
        facts[capture]['provider'] = 'tabby'
    elif change == 'foreign_currency':
        facts[capture]['currency'] = 'USD'
    elif change == 'wrong_reference':
        raw['payment']['reference'] = 'different-reference'
    elif change == 'changed_claim':
        claim['confirmed'] = '201.00'
    elif change == 'foreign_owner':
        state['owner_id'] = 'other'
    elif change == 'stale_source':
        source['g47_salla_snapshot'] = {'source_updated_at': AS_OF}
    elif change == 'future_source':
        raw['updated_at'] = '2027-01-01T00:00:00+00:00'
    elif change == 'split':
        facts['provider_split:' + oid] = {'existing': True}
    elif change == 'unrelated_bad_price':
        facts['capture:unrelated'] = {'order_id': 'unrelated', 'amount': 'bad-price', 'provider': 'tabby'}
    elif change == 'other_provider_refund':
        facts['refund:tabby:unrelated'] = {'order_id': oid, 'amount': 'bad-price'}
    elif change == 'bad_amount':
        facts[capture]['amount'] = 'bad-price'
    elif change == 'decimal_nan':
        facts[capture]['amount'] = Decimal('sNaN')
    before = pickle.dumps((raw, source, state), protocol=5)
    expected = outcome(lambda: evaluate(raw, source, state, observe=observe))
    with routing.RetainedPaymentFactScope(state) as scope, localcontext() as context:
        context.clear_flags()
        index = scope.build()
        assert index is not None and not any(context.flags.values())
        actual = outcome(lambda: evaluate(raw, source, state, index, observe))
        scope.validate()
        assert not any(context.flags.values())
    assert actual == expected
    assert pickle.dumps((raw, source, state), protocol=5) == before
    if change in {'valid', 'refund', 'page_only', 'capture_and_page', 'unrelated_bad_price', 'other_provider_refund'}:
        value = actual[0][1]
        assert value is not None and len(value['proof_hash']) == 64
        adjustment = value['customer_adjustment'] if observe else value
        assert adjustment['paid_amount'] == '200.00'
        assert adjustment['suggested_refund_amount'] == '0.80'
        if not observe and change != 'page_only':
            assert value['payment']['captures'][0]['captured_at'] == CREATED


class Custom(dict):
    pass


@pytest.mark.parametrize('change', ['facts_subclass', 'row_subclass', 'non_string_key',
    'custom_string_key', 'nested_amount', 'nested_unused_field', 'float_identity',
    'list_identity', 'oversized_identity', 'engine_subclass'])
def test_unsupported_shape_keeps_legacy_fallback_before_interpretation(change):
    raw, source, state = retained_fixture()
    facts, capture = state['engine']['facts'], 'capture:synthetic-capture'
    if change == 'facts_subclass':
        state['engine']['facts'] = Custom(facts)
    elif change == 'row_subclass':
        facts[capture] = Custom(facts[capture])
    elif change == 'non_string_key':
        facts[7] = {}
    elif change == 'custom_string_key':
        class Key(str):
            pass
        facts[Key('unrelated')] = {}
    elif change == 'nested_amount':
        facts[capture]['amount'] = {'amount': '200.00', 'currency': 'SAR'}
    elif change == 'nested_unused_field':
        facts[capture]['unused'] = []
    elif change == 'float_identity':
        facts[capture]['order_id'] = 1.5
    elif change == 'list_identity':
        facts[capture]['order_id'] = []
    elif change == 'oversized_identity':
        facts[capture]['order_id'] = 10 ** 5000
    else:
        state['engine'] = Custom(state['engine'])
    expected = outcome(lambda: evaluate(raw, source, state))
    with routing.RetainedPaymentFactScope(state) as scope:
        assert scope.build() is None
        assert outcome(lambda: evaluate(raw, source, state, scope.index)) == expected
        scope.validate()


def test_custom_access_is_not_pulled_ahead_of_the_original_currency_guard():
    accessed = []

    class Exploding(dict):
        def get(self, key, *args):
            accessed.append(key)
            raise RuntimeError('custom fact access')

    raw, source, state = retained_fixture()
    state['engine']['facts']['capture:synthetic-capture'] = Exploding()
    with routing.RetainedPaymentFactScope(state) as scope:
        assert scope.build() is None
        result = basis.retained_confirmed_paid_basis(raw, 'USD', raw['total'], CREATED, AS_OF,
            owner='owner', order_number=raw['reference_id'], provider_order_id=raw['id'],
            source=source, state=state, fact_index=scope.index)
        scope.validate()
    assert result is None and accessed == []


@pytest.mark.parametrize('query', ['None', '', '0', 'False', 'True', '9', 'unmatched'])
def test_routing_keeps_string_identity_order_and_all_matching_evidence(query):
    identities = [None, '', 0, '0', False, 'False', True, Int64(0), Int64(9), '9']
    facts = {'capture:' + str(i): {'order_id': identity, 'amount': '1', 'provider': 'salla'}
             for i, identity in enumerate(identities)}
    state = {'engine': {'facts': facts}}
    expected = [(key, row) for key, row in facts.items() if str(row.get('order_id')) == query]
    with routing.RetainedPaymentFactScope(state) as scope:
        index = scope.build()
        actual = routing.retained_fact_rows(index, state, facts, query, 'capture:')
        assert actual == expected
        assert all(row is facts[key] for key, row in actual)
        scope.validate()


def test_index_never_crosses_state_identity_or_survives_its_attempt():
    raw, source, state = retained_fixture()
    with routing.RetainedPaymentFactScope(state) as scope:
        index = scope.build()
        different = deepcopy(state)
        different['engine']['facts']['capture:synthetic-capture']['amount'] = '201.00'
        different['engine']['obligations']['provider:' + raw['id']]['confirmed'] = '201.00'
        assert evaluate(raw, source, different, index) == evaluate(raw, source, different)
        scope.validate()
    state['engine']['facts']['capture:synthetic-capture']['amount'] = '201.00'
    state['engine']['obligations']['provider:' + raw['id']]['confirmed'] = '201.00'
    assert evaluate(raw, source, state, index) == evaluate(raw, source, state)
    assert index.closed and not scope.active


@pytest.mark.parametrize('mutation', ['same_size_key_replacement', 'order_id_out', 'order_id_in',
    'replacement_row', 'changed_kind', 'key_order', 'amount', 'provider', 'currency',
    'facts_replaced', 'engine_replaced', 'revision', 'owner', 'as_of'])
def test_mutated_attempt_cannot_pass_the_final_boundary(mutation):
    raw, source, state = retained_fixture()
    facts, capture = state['engine']['facts'], 'capture:synthetic-capture'
    facts[capture]['kind'] = 'capture'
    facts['capture:other'] = {'order_id': 'other', 'provider': 'salla', 'amount': '20.00'}
    with pytest.raises(ValueError, match='operational_retained_fact_snapshot_changed'):
        with routing.RetainedPaymentFactScope(state) as scope:
            index = scope.build()
            assert evaluate(raw, source, state, index) is not None
            if mutation == 'same_size_key_replacement':
                facts['capture:new'] = facts.pop(capture)
            elif mutation == 'order_id_out':
                facts[capture]['order_id'] = 'other'
            elif mutation == 'order_id_in':
                facts['capture:other']['order_id'] = raw['id']
            elif mutation == 'replacement_row':
                facts[capture] = dict(facts[capture])
            elif mutation == 'changed_kind':
                facts[capture]['kind'] = 'refund'
            elif mutation == 'key_order':
                facts[capture] = facts.pop(capture)
            elif mutation in {'amount', 'provider', 'currency'}:
                facts[capture][mutation] = {'amount': '201.00', 'provider': 'tabby', 'currency': 'USD'}[mutation]
            elif mutation == 'facts_replaced':
                state['engine']['facts'] = dict(facts)
            elif mutation == 'engine_replaced':
                state['engine'] = dict(state['engine'])
            elif mutation == 'revision':
                state['revision'] = 2
            elif mutation == 'owner':
                state['owner_id'] = 'other'
            else:
                state['engine']['as_of'] = AS_OF
            # Per-order fallback may occur, but cannot waive the final fence.
            evaluate(raw, source, state, index)
            scope.validate()
    assert index.closed and scope.closed and not scope.active


def test_normal_exit_requires_final_validation():
    _, _, state = retained_fixture()
    with pytest.raises(ValueError, match='operational_retained_fact_snapshot_changed'):
        with routing.RetainedPaymentFactScope(state) as scope:
            index = scope.build()
    assert index.closed and scope.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['build', 'validate'])
async def test_cancelled_preparation_is_joined_and_the_attempt_is_closed(monkeypatch, stage):
    _, state = workload(32)
    before = pickle.dumps(state, protocol=5)
    entered, release, exited = Event(), Event(), Event()
    original = routing._checkpoint
    armed = stage == 'build'

    def checkpoint():
        if armed and not entered.is_set():
            entered.set()
            try:
                assert release.wait(2)
                original()
            finally:
                exited.set()
        original()

    monkeypatch.setattr(routing, '_checkpoint', checkpoint)
    scope = routing.RetainedPaymentFactScope(state)

    async def operation():
        nonlocal armed
        with scope:
            await prepare_in_thread(scope.build)
            armed = True
            await prepare_in_thread(scope.validate)

    task = asyncio.create_task(operation())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert exited.is_set() and scope.closed and not scope.active
    assert scope.index is None or scope.index.closed
    assert pickle.dumps(state, protocol=5) == before


@pytest.mark.parametrize('status', ['refunded', 'cancelled'])
def test_complete_source_collection_reconciliation_and_replay_match_legacy(monkeypatch, status):
    import operational_balance_sources as sources
    from operational_balance_engine import reconcile
    from test_operational_balance_sources import run
    from test_operational_customer_payment_basis import cached_adjustment_fixture

    raw, _, state, db = cached_adjustment_fixture()
    raw['status'] = raw['payment']['status'] = status
    before = deepcopy((state, db.data))
    built, build = [], routing.RetainedPaymentFactScope.build

    def tracked(scope):
        index = build(scope)
        built.append(index)
        return index

    monkeypatch.setattr(routing.RetainedPaymentFactScope, 'build', tracked)
    actual = run(sources.collect_sources(db, 'owner', state['started_at'], AS_OF, operational_state=state))
    assert len(built) == 1 and built[0] is not None and built[0].closed
    assert actual['orders'][0].get('payment') is None
    assert actual['orders'][0]['retained_customer_adjustment']['customer_adjustment']['paid_amount'] == '200.00'
    monkeypatch.setattr(routing.RetainedPaymentFactScope, 'build', lambda _scope: None)
    expected = run(sources.collect_sources(db, 'owner', state['started_at'], AS_OF, operational_state=state))
    assert actual == expected and (state, db.data) == before
    refreshed = reconcile(state, actual, AS_OF)
    assert refreshed == reconcile(state, expected, AS_OF)
    assert refreshed['engine']['facts'] == state['engine']['facts']
    assert refreshed['engine']['obligations'] == state['engine']['obligations']
    monkeypatch.setattr(routing.RetainedPaymentFactScope, 'build', tracked)
    repeated = run(sources.collect_sources(db, 'owner', refreshed['started_at'], AS_OF, operational_state=refreshed))
    assert reconcile(refreshed, repeated, AS_OF) == refreshed
    assert built[-1] is not built[0] and built[-1].closed


@pytest.mark.parametrize('mutation', ['same_size_key_replacement', 'order_id_in', 'row_replacement', 'cancel'])
def test_source_loop_does_not_return_observations_from_a_changed_or_cancelled_attempt(monkeypatch, mutation):
    import operational_balance_sources as sources
    from test_operational_balance_sources import run
    from test_operational_customer_payment_basis import cached_adjustment_fixture

    raw, _, state, db = cached_adjustment_fixture()
    facts = state['engine']['facts']
    facts['capture:other'] = {'order_id': 'other', 'provider': 'salla', 'amount': '20.00'}
    built, build = [], routing.RetainedPaymentFactScope.build
    observed = basis.retained_adjustment_observation

    def tracked(scope):
        index = build(scope)
        built.append(index)
        return index

    def interrupted(*args, **kwargs):
        result = observed(*args, **kwargs)
        assert result is not None
        if mutation == 'cancel':
            raise asyncio.CancelledError
        if mutation == 'same_size_key_replacement':
            facts['capture:new'] = facts.pop('capture:synthetic-capture')
        elif mutation == 'order_id_in':
            facts['capture:other']['order_id'] = raw['id']
        else:
            facts['capture:synthetic-capture'] = dict(facts['capture:synthetic-capture'])
        return result

    monkeypatch.setattr(routing.RetainedPaymentFactScope, 'build', tracked)
    monkeypatch.setattr(basis, 'retained_adjustment_observation', interrupted)
    error = asyncio.CancelledError if mutation == 'cancel' else ValueError
    with pytest.raises(error):
        run(sources.collect_sources(db, 'owner', state['started_at'], AS_OF, operational_state=state))
    assert len(built) == 1 and built[0] is not None and built[0].closed
