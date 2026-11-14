"""Exact diagnostic behavior and a workload regression for attempt-local lookup."""
from copy import deepcopy
from time import process_time

import pytest

import operational_issue_index as index
from operational_balance_sources import issue
from operational_completed_shipping_sources import completion_issue
from operational_shipping_fee_review import courier_issue


def legacy_issue(out, code, identity=None, **details):
    # Frozen M1917 function, kept independent of the optimized append helper.
    item = {'code': code, 'source_id': str(identity or ''), 'status': 'incomplete', **details}
    if item not in out['issues']:
        out['issues'].append(item)


def enabled(rows=None):
    out = {'issues': [] if rows is None else rows}
    index.enable_issue_index(out)
    return out


def event(identity='one'):
    return {'order_id': identity, 'order_number': 'N-' + identity, 'party_type': 'courier',
            'party_id': 'carrier', 'party_name': 'Carrier', 'currency': 'SAR',
            'amount': '10.00', 'completed_at': '2026-11-01T00:00:00+00:00',
            'shipment_id': 'shipment-' + identity, 'waybill': 'waybill-' + identity,
            'schema': 'operational_completed_shipping_v1'}


def test_real_issue_completion_and_courier_sequence_matches_legacy():
    baseline, actual = {'issues': []}, enabled()
    actions = [
        lambda out: issue(out, 'simple', 'one'),
        lambda out: issue(out, 'simple', 'one'),
        lambda out: issue(out, 'simple', 'one', value=True),
        lambda out: issue(out, 'simple', 'one', value=1),
        lambda out: issue(out, 'nested', 'two', payload={'parts': [1, {'x': 2}]}),
        lambda out: out['issues'][-1]['payload']['parts'].append(3),
        lambda out: issue(out, 'nested', 'two', payload={'parts': [1, {'x': 2}]}),
        lambda out: issue(out, 'operational_delivery_timestamp_missing', 'ship'),
        lambda out: courier_issue(out, 'operational_delivery_timestamp_missing',
                                  {'id': 'ship', 'reference_id': 'N-ship'}, {}),
        lambda out: issue(out, 'operational_delivery_timestamp_missing', 'ship'),
        lambda out: courier_issue(out, 'operational_delivery_timestamp_missing',
                                  {'id': 'ship', 'reference_id': 'N-ship'}, {}),
        lambda out: completion_issue(out, 'completion', event()),
        lambda out: completion_issue(out, 'completion', event()),
        lambda out: completion_issue(out, 'completion', event(), required_action='Different'),
        lambda out: out['issues'].append({'code': 'raw', 'source_id': 'three', 'status': 'incomplete'}),
        lambda out: issue(out, 'raw', 'three'),
        lambda out: issue(out, 'override', 'unused', source_id=['not', 'hashable']),
        lambda out: issue(out, 'override', 'unused', source_id=['not', 'hashable']),
        lambda out: issue(out, 'last', 'four'),
    ]
    for action in actions:
        action(baseline)
        action(actual)
        assert actual['issues'] == baseline['issues']
    index.finish_issue_index(actual)
    assert actual == baseline
    assert type(actual['issues']) is list


def test_metadata_updates_keep_references_and_full_dictionary_equality():
    out = enabled()
    first = {'code': 'same', 'source_id': 'one', 'status': 'incomplete'}
    index.append_unique_issue(out, first)
    index.update_issue(out, first, {'review': {'reason': ['original']}})
    second = {'code': 'same', 'source_id': 'one', 'status': 'incomplete'}
    index.append_unique_issue(out, second)
    assert out['issues'] == [first, second]
    assert out['issues'][0] is first and out['issues'][1] is second
    index.update_issue(out, second, {'review': deepcopy(first['review'])})
    assert first == second  # Enrichment itself must not merge existing rows.
    index.append_unique_issue(out, deepcopy(first))
    assert len(out['issues']) == 2
    first['review']['reason'].append('changed')
    index.append_unique_issue(out, {'code': 'same', 'source_id': 'one', 'status': 'incomplete',
                                    'review': {'reason': ['original']}})
    assert len(out['issues']) == 2


@pytest.mark.parametrize('change', ['code', 'source_id'])
def test_explicit_identity_updates_invalidate_old_buckets(change):
    out = enabled()
    row = {'code': 'old', 'source_id': 'one'}
    index.append_issue(out, row)
    index.append_issue(out, {'code': 'other', 'source_id': 'two'})
    index.update_issue(out, row, {change: 'new'})
    index.append_unique_issue(out, dict(row))
    assert len(out['issues']) == 2
    assert out[index.INDEX_KEY].valid is False


def test_last_raw_identity_change_falls_back_and_external_reorder_can_invalidate():
    out = enabled()
    row = {'code': 'old', 'source_id': 'one'}
    index.append_issue(out, row)
    row['source_id'] = 'changed'
    index.append_unique_issue(out, dict(row))
    assert out['issues'] == [row]
    index.invalidate_issue_index(out)
    out['issues'].insert(0, {'code': 'other', 'source_id': 'two'})
    index.append_unique_issue(out, dict(row))
    assert out['issues'][-1] is row and len(out['issues']) == 2


@pytest.mark.parametrize('value', [None, 0, False, 1, True, ['one'], {'one': 1}])
def test_unusual_identity_overrides_use_ordinary_list_equality(value):
    expected, actual = {'issues': []}, enabled()
    for supplied in (value, deepcopy(value), 'one'):
        legacy_issue(expected, 'override', 'ignored', source_id=supplied)
        issue(actual, 'override', 'ignored', source_id=supplied)
    assert actual['issues'] == expected['issues']


class ComparisonFailure:
    def __eq__(self, other):
        raise RuntimeError('original comparison order')


def test_nonstandard_dictionary_order_preserves_first_comparison_error():
    old = {'payload': ComparisonFailure(), 'code': 'different', 'source_id': 'one'}
    new = {'payload': ComparisonFailure(), 'code': 'new', 'source_id': 'two'}
    with pytest.raises(RuntimeError, match='original comparison order'):
        new in [old]
    out = enabled([old])
    with pytest.raises(RuntimeError, match='original comparison order'):
        index.append_unique_issue(out, new)
    assert out['issues'] == [old]


def test_dict_subclass_equality_is_not_bypassed():
    class Unusual(dict):
        def __eq__(self, other):
            raise ValueError('custom row equality')
    old = Unusual(code='a', source_id='a')
    out = enabled([old])
    with pytest.raises(ValueError, match='custom row equality'):
        index.append_unique_issue(out, {'code': 'b', 'source_id': 'b'})


def test_custom_dictionary_keys_are_not_compared_while_selecting_fallback():
    class Key:
        def __hash__(self):
            return 991
        def __eq__(self, other):
            raise ValueError('key comparison must remain lazy')
    row = {Key(): 'payload', 'code': 'a', 'source_id': 'one'}
    out = enabled()
    index.append_unique_issue(out, row)  # Empty-list containment performs no equality.
    assert out['issues'][0] is row


def test_list_subclass_containment_and_outer_mapping_contract_are_preserved():
    class AlwaysContains(list):
        def __contains__(self, item):
            return True
        def __len__(self):
            raise ValueError('length was not requested by legacy containment')
    rows = AlwaysContains()
    out = enabled(rows)
    index.append_unique_issue(out, {'code': 'a', 'source_id': 'one'})
    assert rows == []
    class Outer(dict):
        def get(self, *args):
            raise ValueError('legacy outer mapping only used subscription')
    outer = Outer(issues=[])
    issue(outer, 'a', 'one')
    assert outer['issues'] == [{'code': 'a', 'source_id': 'one', 'status': 'incomplete'}]


def test_raw_appends_and_identity_lookup_preserve_order_and_row_identity():
    out = enabled()
    rows = [{'code': 'target', 'source_id': 'one', 'value': value} for value in range(3)]
    for row in rows:
        out['issues'].append(row)
        index.append_issue(out, {'code': 'unrelated', 'source_id': str(row['value'])})
    result = index.issues_for_identity(out, 'target', 'one')
    assert result == rows
    assert all(actual is expected for actual, expected in zip(result, rows))
    out['issues'].append({'code': 'target', 'source_id': 1})
    result = [row for row in index.issues_for_identity(out, 'target', '1')
              if row.get('code') == 'target' and str(row.get('source_id')) == '1']
    assert result == [out['issues'][-1]]


def test_replacing_or_shortening_the_list_falls_back():
    out = enabled()
    issue(out, 'a', 'one')
    out['issues'] = [{'code': 'b', 'source_id': 'two', 'status': 'incomplete'}]
    issue(out, 'b', 'two')
    assert len(out['issues']) == 1
    out['issues'].clear()
    issue(out, 'b', 'two')
    assert len(out['issues']) == 1


def test_attempts_are_isolated_and_output_has_no_index():
    first, second = enabled(), enabled()
    issue(first, 'a', 'one')
    issue(second, 'a', 'one')
    index.update_issue(first, first['issues'][0], {'metadata': 'first only'})
    issue(first, 'a', 'one')
    issue(second, 'a', 'one')
    assert len(first['issues']) == 2 and len(second['issues']) == 1
    index.finish_issue_index(first)
    index.finish_issue_index(second)
    assert set(first) == set(second) == {'issues'}


def test_sync_and_candidate_walk_cooperate_with_cancellation(monkeypatch):
    out = enabled([{'code': 'a', 'source_id': str(n)} for n in range(1000)])
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError('visible request cancelled')
    monkeypatch.setattr(index, '_checkpoint', cancelled)
    with pytest.raises(RuntimeError, match='visible request cancelled'):
        index.append_unique_issue(out, {'code': 'new', 'source_id': 'new'})
    assert calls == 5 and len(out['issues']) == 1000


def test_four_thousand_distinct_diagnostics_avoid_repeated_whole_list_work():
    # Process CPU excludes scheduling delays in other CI jobs. The legacy
    # workload makes almost eight million comparisons; allow a broad margin.
    identities = [str(n) for n in range(4000)]
    baseline = {'issues': []}
    started = process_time()
    for identity in identities:
        legacy_issue(baseline, 'source_incomplete', identity, component='shipping_fee')
    old_cpu = process_time() - started
    actual = enabled()
    started = process_time()
    for identity in identities:
        issue(actual, 'source_incomplete', identity, component='shipping_fee')
    new_cpu = process_time() - started
    assert actual['issues'] == baseline['issues']
    assert new_cpu < old_cpu * 0.65, (old_cpu, new_cpu)
