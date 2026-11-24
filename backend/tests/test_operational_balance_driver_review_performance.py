"""Review-effect lookup is bounded without changing saved money or proof rules."""
from copy import deepcopy
import json

import pytest

import operational_driver_review_report as reviews
from operational_balance_store import digest
from operational_financial_position import build_financial_position
from test_operational_balance_cod_projection_performance import cod_fixture, CountedMovements


def add_reviews(state, count):
    facts = state.setdefault('driver_payment_review_facts', {})
    for number in range(count):
        review, order, movement = f'review-{number}', f'review-order-{number}', f'review-movement-{number}'
        identity = digest([state['owner_id'], 'operational-driver-review', review, 1])
        fact = {'id': identity, 'financial_source': 'operational', 'review_id': review, 'review_revision': 1,
            'order_id': order, 'driver_id': 'driver', 'payment_method': 'bank_transfer', 'amount': '200.00',
            'currency': 'SAR', 'destination_id': 'bank', 'destination_type': 'bank',
            'destination_name': 'Synthetic bank', 'receipt_hash': 'a' * 64,
            'approved_at': '2026-11-08T10:00:00+03:00', 'movement_id': movement, 'obligation_id': None}
        fact['seal'] = digest(fact)
        facts[identity] = fact
        state['movements'].append({'id': movement, 'kind': 'payment', 'driver_review_event_id': identity,
            'direction': 'incoming', 'bank_id': 'bank', 'party_id': 'bank', 'party_type': 'bank',
            'bank_kind': 'bank', 'allocations': [], 'amount': '200.00', 'currency': 'SAR',
            'order_id': order, 'receipt_hash': 'a' * 64})
    return state


def model(count=10, movements=40):
    return add_reviews(cod_fixture(movements=movements, claims=5), count)


def test_effect_validation_uses_one_movement_pass_regardless_of_review_count():
    observed = []
    for count in (5, 50):
        state = model(count)
        counter = {'passes': 0}
        state['movements'] = CountedMovements(state['movements'], counter)
        valid, issues = reviews.validated_review_facts(state)
        assert len(valid) == count and issues == []
        observed.append(counter['passes'])
    assert observed == [1, 1]


def test_index_keeps_only_requested_ids_and_a_saturated_duplicate_marker():
    state = model(1, movements=400)
    matching = state['movements'][-1]
    state['movements'].extend(deepcopy(matching) for _ in range(100))
    index = reviews._index_review_movements(state, {matching['id'], 'absent'})
    assert set(index) == {matching['id']}
    assert index[matching['id']] == (2, None)
    valid, issues = reviews.validated_review_facts(state)
    assert valid == [] and [row['code'] for row in issues] == ['operational_driver_review_effect_conflict']


@pytest.mark.parametrize('conflicting', [False, True])
def test_duplicate_matching_effect_cannot_validate_even_if_one_is_correct(conflicting):
    state = model(2, movements=0)
    duplicate = deepcopy(state['movements'][0])
    if conflicting:
        duplicate['amount'] = '199.00'
    state['movements'].append(duplicate)
    valid, issues = reviews.validated_review_facts(state)
    assert [row['review_id'] for row in valid] == ['review-1']
    assert [row['source_id'] for row in issues] == [next(iter(state['driver_payment_review_facts']))]


@pytest.mark.parametrize('identity', [None, False, 0, 1, [], {}, ['malformed']])
def test_nonstring_saved_identity_keeps_the_original_equality_behavior(monkeypatch, identity):
    state = model(1, movements=0)
    fact = next(iter(state['driver_payment_review_facts'].values()))
    fact['movement_id'] = identity
    fact['seal'] = digest({key: value for key, value in fact.items() if key != 'seal'})
    state['movements'][0]['id'] = deepcopy(identity)
    before = deepcopy(state)
    with monkeypatch.context() as legacy:
        legacy.setattr(reviews, '_index_review_movements', lambda state, requested: None)
        expected = reviews.validated_review_facts(state)
    assert reviews.validated_review_facts(state) == expected
    assert len(expected[0]) == 1 and not expected[1]
    assert state == before


def test_malformed_unrelated_movement_and_cross_type_duplicates_keep_old_semantics(monkeypatch):
    state = model(2, movements=0)
    fact = next(iter(state['driver_payment_review_facts'].values()))
    fact['movement_id'] = True
    fact['seal'] = digest({key: value for key, value in fact.items() if key != 'seal'})
    state['movements'][0]['id'] = True
    duplicate = deepcopy(state['movements'][0])
    duplicate['id'] = 1  # Python equality treated these as duplicate effects before indexing.
    state['movements'].extend([duplicate, {'id': []}])
    with monkeypatch.context() as legacy:
        legacy.setattr(reviews, '_index_review_movements', lambda state, requested: None)
        expected = reviews.validated_review_facts(state)
    assert reviews.validated_review_facts(state) == expected
    assert len(expected[0]) == 1 and len(expected[1]) == 1


def test_invalid_seal_and_pos_only_review_do_not_scan_or_touch_malformed_movements():
    from test_operational_balance_driver_review_report import recorded

    state, _ = recorded('card_terminal')
    state['movements'] = [None]
    counter = {'passes': 0}
    state['movements'] = CountedMovements(state['movements'], counter)
    valid, issues = reviews.validated_review_facts(state)
    assert len(valid) == 1 and issues == [] and counter['passes'] == 0
    next(iter(state['driver_payment_review_facts'].values()))['seal'] = 'invalid'
    valid, issues = reviews.validated_review_facts(state)
    assert not valid and len(issues) == 1 and counter['passes'] == 0


def test_lookup_never_survives_the_next_report_or_masks_changed_effect():
    state = model(1, movements=0)
    assert len(reviews.validated_review_facts(state)[0]) == 1
    state['movements'][0]['amount'] = '201.00'
    valid, issues = reviews.validated_review_facts(state)
    assert not valid and len(issues) == 1


@pytest.mark.parametrize('view', ['summary', 'advertising', 'drivers', 'couriers', 'providers', 'employees',
                                  'accounts', 'suppliers', 'other', 'expected', 'forecast', 'all'])
def test_all_view_json_and_input_match_the_original_linear_lookup(monkeypatch, view):
    from test_operational_financial_position_views import rich_state

    state = add_reviews(rich_state(), 5)
    # Retain a conflicting duplicate beside valid reviews and unrelated history.
    state['movements'].append(deepcopy(state['movements'][-1]))
    state['driver_payment_review_facts']['damaged'] = {'id': 'damaged'}
    before = deepcopy(state)
    stamp = state['engine']['as_of']
    with monkeypatch.context() as legacy:
        legacy.setattr(reviews, '_index_review_movements', lambda state, requested: None)
        expected = build_financial_position(state, generated_at=stamp, view=view)
    actual = build_financial_position(state, generated_at=stamp, view=view)
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    assert encode(actual) == encode(expected)
    assert state == before
