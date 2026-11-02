"""The frozen dashboard gate permits only audited, empty, scoped cost reads."""
import copy
import json
from collections import Counter

import pytest

from dashboard_runtime_gate_plugin import (
    AUTHORITY_COLLECTIONS,
    _authority_command,
    _cohort_evidence,
    _expected_authority_commands,
    assert_runtime_read_contract,
)


def evidence_pair(orders=None):
    if orders is None:
        orders = [{'user_id': 'owner', 'order_number': 'A', 'products': [
            {'canonical_order_item_id': 'salla:A:line', 'source_item_id': 'line', 'quantity': 4},
        ]}]
    baseline = {'json': json.dumps({'total': 220, 'profit': 180}),
                'commands': {'find': 43}, 'documents': 260,
                'collections': {'unified_orders': 152, 'settings': 4, 'products': 104}}
    audit = {'version': 1, 'owner_id': 'owner', 'database': 'dashboard_b_shared_contract_test',
             'cohorts': [_cohort_evidence('owner', orders)], 'commands': []}
    audit['commands'] = _expected_authority_commands(audit)
    runtime = copy.deepcopy(baseline)
    runtime['cost_authority_audit'] = audit
    for operation, count in Counter(row['operation'] for row in audit['commands']).items():
        runtime['commands'][operation] = runtime['commands'].get(operation, 0) + count
    runtime['collections'].update({row['collection']: 0 for row in audit['commands']})
    return runtime, baseline


def test_exact_six_empty_reads_preserve_the_raw_evidence_and_original_oracle():
    runtime, baseline = evidence_pair()
    original = copy.deepcopy((runtime, baseline))
    assert runtime['commands'] == {'find': 47, 'aggregate': 2}
    assert len(runtime['cost_authority_audit']['commands']) == 6
    assert set(runtime['collections']) - set(baseline['collections']) == AUTHORITY_COLLECTIONS
    assert_runtime_read_contract(runtime, baseline)
    assert (runtime, baseline) == original


def test_empty_cohort_and_permission_failure_permit_no_authority_queries():
    runtime, baseline = evidence_pair([])
    assert runtime['cost_authority_audit']['commands'] == []
    assert_runtime_read_contract(runtime, baseline)
    runtime['json'] = baseline['json'] = json.dumps({'http_error': 403, 'detail': 'Forbidden'})
    runtime['cost_authority_audit']['cohorts'] = []
    runtime['commands'] = baseline['commands'] = {}
    runtime['documents'] = baseline['documents'] = 0
    runtime['collections'] = baseline['collections'] = {}
    assert_runtime_read_contract(runtime, baseline)


def test_two_bounded_batches_have_eleven_queries_and_exact_disjoint_order_scopes():
    orders = [{'user_id': 'owner', 'order_number': str(i).zfill(3),
               'products': [{'order_item_id': 'line-' + str(i)}]} for i in range(251)]
    runtime, baseline = evidence_pair(orders)
    commands = runtime['cost_authority_audit']['commands']
    assert len(commands) == 11
    first, second = commands[1]['filter'], commands[6]['filter']
    assert len(first['order_number']['$in']) == 250
    assert second['order_number']['$in'] == ['250']
    assert second['order_item_id']['$in'] == ['line-250']
    assert set(first['order_number']['$in']).isdisjoint(second['order_number']['$in'])
    assert_runtime_read_contract(runtime, baseline)


@pytest.mark.parametrize('defect', [
    'wrong_query_owner', 'wrong_caller_owner', 'missing_owner', 'wider_order_scope',
    'wrong_line_alias', 'unexpected_plan', 'extra_authority_query', 'missing_query',
    'write', 'out', 'merge', 'nonempty_authority', 'nonempty_old_collection',
    'extra_old_read', 'extra_get_more', 'unknown_collection', 'second_cohort',
    'json_number', 'json_member_order', 'missing_empty_result_evidence',
    'wrong_command_database', 'wrong_event_database',
])
def test_gate_rejects_changes_outside_the_exact_empty_read_extension(defect):
    runtime, baseline = evidence_pair()
    audit = runtime['cost_authority_audit']
    commands = audit['commands']
    if defect == 'wrong_query_owner':
        commands[1]['filter']['user_id'] = 'foreign'
    elif defect == 'wrong_caller_owner':
        # The request owner remains the oracle even if the caller and every
        # query agree with each other on a different tenant.
        audit['cohorts'][0]['user_id'] = 'foreign'
        for row in commands:
            query = row['filter'] if row['operation'] == 'find' else row['pipeline'][0]['$match']
            query['user_id'] = 'foreign'
    elif defect == 'missing_owner':
        del commands[1]['filter']['user_id']
    elif defect == 'wider_order_scope':
        commands[1]['filter']['order_number']['$in'].append('outside-selected-orders')
    elif defect == 'wrong_line_alias':
        commands[1]['filter']['order_item_id']['$in'].append('outside-selected-lines')
    elif defect == 'unexpected_plan':
        commands[4]['filter']['plan_id']['$in'].append('unread-plan')
    elif defect == 'extra_authority_query':
        commands.append(copy.deepcopy(commands[1]))
        runtime['commands']['find'] += 1
    elif defect == 'missing_query':
        commands.pop()
        runtime['commands']['find'] -= 1
    elif defect == 'write':
        commands[1] = _authority_command('update', {'update': 'mezan_order_item_cost_snapshots_v2'},
                                         database_name=audit['database'])
    elif defect in {'out', 'merge'}:
        commands[2]['pipeline'].append({'$' + defect: 'unapproved-write'})
    elif defect == 'nonempty_authority':
        runtime['collections']['mezan_order_item_cost_snapshots_v2'] = 1
        runtime['documents'] += 1
    elif defect == 'nonempty_old_collection':
        runtime['collections']['unified_orders'] += 1
        runtime['documents'] += 1
    elif defect == 'extra_old_read':
        runtime['commands']['find'] += 1
    elif defect == 'extra_get_more':
        runtime['commands']['getMore'] = 1
    elif defect == 'unknown_collection':
        runtime['collections']['unreviewed_collection'] = 0
        runtime['commands']['find'] += 1
    elif defect == 'second_cohort':
        audit['cohorts'].append(copy.deepcopy(audit['cohorts'][0]))
    elif defect == 'json_number':
        runtime['json'] = json.dumps({'total': 219, 'profit': 181})
    elif defect == 'json_member_order':
        runtime['json'] = json.dumps({'profit': 180, 'total': 220})
    elif defect == 'missing_empty_result_evidence':
        del runtime['collections']['mezan_order_item_cost_snapshots_v2']
    elif defect == 'wrong_command_database':
        commands[1]['command_database'] = 'different_database'
    elif defect == 'wrong_event_database':
        commands[1]['database'] = 'different_database'
    with pytest.raises(AssertionError):
        assert_runtime_read_contract(runtime, baseline)


def test_listener_keeps_read_semantics_and_does_not_record_session_metadata():
    collection = 'mezan_order_cost_corrections_v1'
    event = {'find': collection, 'filter': {'user_id': 'owner'}, 'projection': {'_id': 0},
             'lsid': {'id': 'opaque-session'}, '$clusterTime': {'clusterTime': 'opaque-time'}}
    event['$db'] = 'dashboard_b_shared_contract_test'
    recorded = _authority_command('find', event, database_name=event['$db'])
    event['filter']['user_id'] = 'changed-after-event'
    assert recorded == {'operation': 'find', 'collection': collection,
                        'database': 'dashboard_b_shared_contract_test',
                        'command_database': 'dashboard_b_shared_contract_test',
                        'filter': {'user_id': 'owner'}, 'projection': {'_id': 0}, 'limit': None,
                        'unreviewed_fields': []}
    assert _authority_command('find', {'find': 'unified_orders'},
                              database_name=event['$db']) is None


@pytest.mark.parametrize('operation,field,value', [
    ('find', 'sort', {'user_id': 1}), ('find', 'skip', 1),
    ('find', 'batchSize', 1), ('find', 'hint', 'unreviewed-index'),
    ('find', 'readConcern', {'level': 'local'}),
    ('aggregate', 'explain', True), ('aggregate', 'writeConcern', {'w': 'majority'}),
    ('aggregate', 'cursor', {'batchSize': 1}),
])
def test_listener_rejects_unreviewed_top_level_command_options(operation, field, value):
    runtime, baseline = evidence_pair()
    commands = runtime['cost_authority_audit']['commands']
    index = 1 if operation == 'find' else 2
    old = commands[index]
    event = {operation: old['collection'], **{
        key: copy.deepcopy(old[key]) for key in
        (('filter', 'projection') if operation == 'find' else ('pipeline', 'cursor'))}}
    event[field] = value
    event['$db'] = runtime['cost_authority_audit']['database']
    commands[index] = _authority_command(operation, event, database_name=event['$db'])
    with pytest.raises(AssertionError):
        assert_runtime_read_contract(runtime, baseline)


def test_cohort_identity_rejects_foreign_orders_and_duplicates():
    with pytest.raises(AssertionError):
        _cohort_evidence('owner', [{'user_id': 'foreign', 'order_number': 'A'}])
    with pytest.raises(AssertionError):
        _cohort_evidence('owner', [{'order_number': 'A'}, {'order_number': 'A'}])
