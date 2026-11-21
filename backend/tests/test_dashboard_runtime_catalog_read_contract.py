"""Independent negative controls for the frozen gate's catalog read adaptation."""
import copy
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from mongomock import MongoClient
from mongomock_motor import AsyncMongoMockClient
import pytest

import dashboard_runtime_gate_plugin as gate


PRODUCTS = 'mezan_products_v2'
COMPANIONS = ('mezan_product_cost_profiles_v2', 'mezan_product_option_cost_bindings_v2',
              'mezan_product_resource_bindings_v2', 'mezan_cost_resources_v2')
ALIAS = dict.fromkeys(('_id', 'id', 'salla_product_id', 'mezan_product_id', 'name', 'sku',
    'variants', 'raw_salla_details.variants', 'raw_salla_details.skus',
    'raw_salla_details.product_variants', 'raw_salla.variants', 'raw_salla.skus'), 1)
FULL = dict.fromkeys(('_id', 'id', 'salla_product_id', 'mezan_product_id', 'name', 'sku',
    'main_image', 'cost_price_from_salla', 'cost_price', 'cost', 'variants', 'options',
    'raw_salla', 'raw_salla_details'), 1)
MIXED = 'test_dashboard_mixed_permissions_and_tenants'
MUTATIONS = 'test_existing_independent_read_mutations'


def fixture_key(case, family=MIXED):
    return ('tests/test_dashboard_b_index_cooperative_correctness.py::'
            f'{family}[{case}]::request-0')


def evidence_pair(case='prior_month', family=MIXED):
    """Literal immutable fixture counts, without importing candidate reducers."""
    owner = 'fixture-owner'
    if family == MIXED:
        indices = {'prior_month': [0], 'empty_period': [], 'no_dates': list(range(40)),
                   'no_matching_filter': [14]}[case]
        ids = [str(i) for i in range(40)]
        counts = dict(zip((PRODUCTS, *COMPANIONS), (40, 37, 1, 2, 1)))
        policy_rows = 1
    else:
        assert family == MUTATIONS
        indices = [0] if case == 'product_profile_cost' else []
        ids = ['0']
        counts = dict(zip((PRODUCTS, *COMPANIONS), (1, 1, 0, 0, 0)))
        policy_rows = 0
    orders = [{'user_id': owner, 'order_number': str(1000000 + i),
               'products': [{'product_id': str(i)}]} for i in indices]
    collections = {'settings': 3, 'unified_orders': 2 * len(orders) + 1,
                   'order_status_policy': 2 * policy_rows, **counts}
    baseline = {'json': json.dumps({'totals': {'total_orders': len(orders)},
                                    'unchanged': {'cost': 85.0, 'precision': 11.115}}),
                'commands': {'find': 20}, 'documents': sum(collections.values()),
                'collections': collections}
    audit = {'version': 1, 'owner_id': owner, 'database': 'dashboard_b_shared_catalog_contract',
             'cohorts': [gate._cohort_evidence(owner, orders)], 'commands': [],
             'traffic_cohorts': [gate._traffic_cohort(owner, orders)] if orders else [],
             'traffic_commands': [], 'google_cohorts': [], 'google_commands': [],
             'payment_fee_cohorts': [], 'payment_fee_commands': []}
    audit['commands'] = gate._expected_authority_commands(audit)
    audit['traffic_commands'] = gate._expected_traffic_commands(audit)
    cohort = {'user_id': owner, 'order_numbers': [order['order_number'] for order in orders],
              'order_count': len(orders)}
    catalog = {'version': 1, 'cohorts': [cohort], 'loads': [copy.deepcopy(cohort)] if orders else [],
               'commands': [], 'cost_commands': [], 'reads': []}
    audit['catalog'] = catalog
    runtime = copy.deepcopy(baseline)
    runtime['cost_authority_audit'] = audit
    for operation, count in Counter(row['operation'] for row in audit['commands'] + audit['traffic_commands']).items():
        runtime['commands'][operation] = runtime['commands'].get(operation, 0) + count
    runtime['collections'].update({row['collection']: 0 for row in audit['commands']})
    if orders:
        runtime['collections'][gate.TRAFFIC_LEDGER] = 0
        runtime['collections']['unified_orders'] += len(orders)
        runtime['documents'] += len(orders)
        physical = {identity: {'$oid': f'{int(identity) + 1:024x}'} for identity in ids}

        def read(query, projection, limit, identities):
            command = {'operation': 'find', 'collection': PRODUCTS, 'database': audit['database'],
                       'command_database': audit['database'], 'filter': copy.deepcopy(query),
                       'projection': copy.deepcopy(projection), 'unreviewed_fields': []}
            catalog['commands'].append(command)
            catalog['cost_commands'].append(copy.deepcopy(command))
            catalog['reads'].append({'filter': copy.deepcopy(query), 'projection': copy.deepcopy(projection),
                'limits': [limit], 'rows': [{'physical_id': physical[identity], 'salla_product_id': identity}
                                          for identity in identities]})

        read({'user_id': owner}, ALIAS, 100000, ids)
        selected = [str(i) for i in indices if family != MIXED or i not in (12, 21)]
        if selected:
            read({'user_id': owner, '_id': {'$in': [physical[identity] for identity in selected]}},
                 FULL, len(selected), selected)
            runtime['commands']['find'] += 1
            runtime['collections'][PRODUCTS] += len(selected)
            runtime['documents'] += len(selected)
    else:
        runtime['commands']['find'] -= 6
        for collection, count in counts.items():
            runtime['documents'] -= count
            del runtime['collections'][collection]
        runtime['collections']['order_status_policy'] -= policy_rows
        runtime['documents'] -= policy_rows
    return runtime, baseline, fixture_key(case, family)


@pytest.mark.parametrize('case', ['prior_month', 'no_dates', 'no_matching_filter'])
def test_only_exact_physical_winners_add_one_full_read_and_leave_raw_oracle_intact(case):
    runtime, baseline, key = evidence_pair(case)
    before = copy.deepcopy((runtime, baseline))
    gate.assert_runtime_read_contract(runtime, baseline, fixture_case=key)
    assert (runtime, baseline) == before
    assert runtime['json'] == baseline['json']
    if case == 'no_dates':
        rows = runtime['cost_authority_audit']['catalog']['reads'][1]['rows']
        assert len(rows) == 38
        assert {'12', '21'}.isdisjoint(row['salla_product_id'] for row in rows)
        assert '20' in {row['salla_product_id'] for row in rows}


@pytest.mark.parametrize('ordering', ['lexical', 'reversed'])
def test_unsorted_owner_query_keeps_observed_index_order_without_inventing_an_order_oracle(ordering):
    runtime, baseline, key = evidence_pair('no_dates')
    reads = runtime['cost_authority_audit']['catalog']['reads']
    if ordering == 'lexical':
        reads[0]['rows'].sort(key=lambda row: row['salla_product_id'])
        assert [row['salla_product_id'] for row in reads[0]['rows'][:4]] == ['0', '1', '10', '11']
    else:
        reads[0]['rows'].reverse()
        assert reads[0]['rows'][0]['salla_product_id'] == '39'
    gate.assert_runtime_read_contract(runtime, baseline, fixture_case=key)


@pytest.mark.parametrize('case,family', [('empty_period', MIXED), *[
    (case, MUTATIONS) for case in ('status', 'payment_status', 'shipping_status',
                                 'payment_only', 'shipping_only', 'date_universe')]])
def test_empty_cost_removes_only_the_six_known_legacy_reads(case, family):
    runtime, baseline, key = evidence_pair(case, family)
    before = copy.deepcopy((runtime, baseline))
    assert baseline['commands']['find'] - runtime['commands']['find'] == 6
    assert runtime['cost_authority_audit']['catalog']['cost_commands'] == []
    gate.assert_runtime_read_contract(runtime, baseline, fixture_case=key)
    assert (runtime, baseline) == before


@pytest.mark.parametrize('defect', [
    'owner', 'missing_owner', 'wrong_database', 'projection_raw_alias', 'projection_cost_missing',
    'unbounded_hydration', 'wrong_physical_id', 'duplicate_physical_id', 'extra_physical_id',
    'missing_full_read', 'second_full_read', 'cursor_cap', 'cursor_consumed_twice',
    'missing_hydrated_row', 'duplicate_hydrated_row', 'unrelated_hydrated_row',
    'alias_duplicate_identity', 'alias_missing_row', 'unreviewed_sort', 'write', 'outside_loader',
    'wrong_cost_cohort', 'wrong_loader_cohort', 'second_loader', 'missing_profile_read',
    'missing_unrelated_read', 'extra_getmore', 'financial_value', 'json_member_order',
])
def test_nonempty_catalog_extension_fails_closed_on_scope_payload_and_other_read_changes(defect):
    runtime, baseline, key = evidence_pair()
    audit = runtime['cost_authority_audit']
    catalog = audit['catalog']
    full = catalog['commands'][1]
    if defect == 'owner':
        full['filter']['user_id'] = 'other-owner'
    elif defect == 'missing_owner':
        del full['filter']['user_id']
    elif defect == 'wrong_database':
        full['database'] = 'foreign_database'
    elif defect == 'projection_raw_alias':
        catalog['commands'][0]['projection']['raw_salla_details'] = 1
    elif defect == 'projection_cost_missing':
        del full['projection']['cost_price']
    elif defect == 'unbounded_hydration':
        del full['filter']['_id']
    elif defect == 'wrong_physical_id':
        full['filter']['_id']['$in'] = [{'$oid': 'f' * 24}]
    elif defect == 'duplicate_physical_id':
        full['filter']['_id']['$in'] *= 2
    elif defect == 'extra_physical_id':
        full['filter']['_id']['$in'].append({'$oid': f'{2:024x}'})
    elif defect == 'missing_full_read':
        catalog['commands'].pop(); catalog['reads'].pop(); catalog['cost_commands'].pop()
        runtime['commands']['find'] -= 1
        runtime['documents'] -= 1; runtime['collections'][PRODUCTS] -= 1
    elif defect == 'second_full_read':
        catalog['commands'].append(copy.deepcopy(full))
        runtime['commands']['find'] += 1
    elif defect == 'cursor_cap':
        catalog['reads'][1]['limits'] = [100000]
    elif defect == 'cursor_consumed_twice':
        catalog['reads'][1]['limits'] *= 2
    elif defect == 'missing_hydrated_row':
        catalog['reads'][1]['rows'] = []
    elif defect == 'duplicate_hydrated_row':
        catalog['reads'][1]['rows'] *= 2
    elif defect == 'unrelated_hydrated_row':
        catalog['reads'][1]['rows'] = [catalog['reads'][0]['rows'][1]]
    elif defect == 'alias_duplicate_identity':
        catalog['reads'][0]['rows'][1] = copy.deepcopy(catalog['reads'][0]['rows'][0])
    elif defect == 'alias_missing_row':
        catalog['reads'][0]['rows'].pop()
    elif defect == 'unreviewed_sort':
        full['unreviewed_fields'] = ['sort']
    elif defect == 'write':
        full['operation'] = 'update'
    elif defect == 'outside_loader':
        catalog['cost_commands'].append(copy.deepcopy(full))
    elif defect == 'wrong_cost_cohort':
        catalog['cohorts'][0]['order_numbers'] = []
    elif defect == 'wrong_loader_cohort':
        catalog['loads'][0]['user_id'] = 'foreign'
    elif defect == 'second_loader':
        catalog['loads'] *= 2
    elif defect == 'missing_profile_read':
        runtime['commands']['find'] -= 1
        runtime['documents'] -= runtime['collections'].pop(COMPANIONS[0])
    elif defect == 'missing_unrelated_read':
        runtime['commands']['find'] -= 1
        runtime['documents'] -= runtime['collections'].pop('settings')
    elif defect == 'extra_getmore':
        runtime['commands']['getMore'] = 1
    elif defect == 'financial_value':
        runtime['json'] = runtime['json'].replace('85.0', '84.0')
    elif defect == 'json_member_order':
        value = json.loads(runtime['json'])
        runtime['json'] = json.dumps(dict(reversed(list(value.items()))))
    with pytest.raises(AssertionError):
        gate.assert_runtime_read_contract(runtime, baseline, fixture_case=key)


@pytest.mark.parametrize('defect', [
    'hidden_find', 'hidden_write', 'catalog_load', 'traffic_call', 'cost_cohort_nonempty',
    'forged_empty_cohort', 'missing_unrelated_find', 'extra_removed_policy_read',
    'retained_catalog_read', 'unrecognized_empty_case',
])
def test_empty_cost_fastpath_does_not_waive_other_queries_or_accept_nonempty_orders(defect):
    runtime, baseline, key = evidence_pair('empty_period')
    audit = runtime['cost_authority_audit']
    catalog = audit['catalog']
    if defect in {'hidden_find', 'hidden_write'}:
        catalog['cost_commands'] = [{'operation': 'find' if defect == 'hidden_find' else 'update'}]
    elif defect == 'catalog_load':
        catalog['loads'] = [catalog['cohorts'][0]]
    elif defect == 'traffic_call':
        audit['traffic_cohorts'] = [gate._traffic_cohort('fixture-owner', [])]
    elif defect == 'cost_cohort_nonempty':
        catalog['cohorts'][0]['order_count'] = 1
    elif defect == 'forged_empty_cohort':
        # Candidate and instrumentation agreeing on [] cannot override the
        # immutable fixture's one real order (the blank-method order 14).
        key = fixture_key('no_matching_filter')
        baseline['json'] = runtime['json'] = json.dumps({'totals': {'total_orders': 1}})
    elif defect == 'missing_unrelated_find':
        runtime['commands']['find'] -= 1
        runtime['documents'] -= runtime['collections'].pop('settings')
    elif defect == 'extra_removed_policy_read':
        runtime['commands']['find'] -= 1
        runtime['documents'] -= 1
        runtime['collections']['order_status_policy'] -= 1
    elif defect == 'retained_catalog_read':
        runtime['commands']['find'] += 1
        runtime['collections'][PRODUCTS] = 0
    elif defect == 'unrecognized_empty_case':
        key = fixture_key('unreviewed')
    with pytest.raises(AssertionError):
        gate.assert_runtime_read_contract(runtime, baseline, fixture_case=key)


def test_empty_cost_still_requires_the_other_zero_row_policy_read():
    runtime, baseline, key = evidence_pair('status', MUTATIONS)
    assert runtime['collections']['order_status_policy'] == 0
    del runtime['collections']['order_status_policy']
    with pytest.raises(AssertionError, match='Separate policy read disappeared'):
        gate.assert_runtime_read_contract(runtime, baseline, fixture_case=key)


def test_permission_denial_requires_present_but_empty_catalog_instrumentation():
    runtime, baseline, _ = evidence_pair('empty_period')
    baseline.update(json=json.dumps({'http_error': 403, 'detail': 'Forbidden'}),
                    commands={}, documents=0, collections={})
    audit = runtime['cost_authority_audit']
    for field in ('cohorts', 'commands', 'traffic_cohorts', 'traffic_commands'):
        audit[field] = []
    audit['catalog']['cohorts'] = []
    runtime = {**copy.deepcopy(baseline), 'cost_authority_audit': audit}
    key = fixture_key('permission_denied')
    gate.assert_runtime_read_contract(runtime, baseline, fixture_case=key)
    del audit['catalog']
    with pytest.raises(AssertionError, match='requires the catalog audit'):
        gate.assert_runtime_read_contract(runtime, baseline, fixture_case=key)


def test_catalog_wire_audit_retains_physical_type_and_rejectable_command_options():
    identity = ObjectId('1234567890abcdef12345678')
    query = {'user_id': 'owner', '_id': {'$in': [identity]}}
    command = {'find': PRODUCTS, 'filter': query, 'projection': FULL,
               '$db': 'dashboard_b_shared_catalog_contract', 'sort': {'name': 1},
               'lsid': {'id': 'opaque'}, '$clusterTime': {'clusterTime': 'opaque'}}
    value = gate._catalog_command('find', command, database_name=command['$db'])
    query['user_id'] = 'changed-after-command'
    assert value['filter'] == {'user_id': 'owner', '_id': {'$in': [{'$oid': str(identity)}]}}
    assert value['unreviewed_fields'] == ['sort']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['synchronous', 'cooperative'])
@pytest.mark.parametrize('kind,expected_total', [
    ('catalog', 85), ('profile', 146.5), ('product_binding', 94),
    ('option_binding', 93), ('resource', 95), ('policy', 0),
])
async def test_mutation_fence_runs_after_full_capture_and_preserves_frozen_numerical_oracle(mode, kind, expected_total):
    import dashboard_v2_routes as dash
    import product_catalog_cost_resolution as catalog_module
    sync_client = MongoClient()
    db = AsyncMongoMockClient(mock_mongo_client=sync_client).catalog_gate_fence
    owner = 'fixture-owner'
    orders = [{'user_id': owner, 'order_number': str(1000000 + i), 'order_status': 'completed',
               'order_date': '2026-10-05', 'currency': 'SAR', 'total_amount': 100,
               'products': [{'product_id': str(i), 'quantity': 1, 'price': 100,
                             'options': [{'name': 'التغليف', 'value': 'فاخر'}] if i == 0 else []}]}
              for i in range(2)]
    await db[PRODUCTS].insert_many([{'user_id': owner, 'salla_product_id': str(i),
        'id': 'product-' + str(i), 'name': 'Fixture product ' + str(i), 'cost_price': 37.5,
        'variants': []} for i in range(2)])
    await db[COMPANIONS[0]].insert_many([{'user_id': owner, 'salla_product_id': str(i),
        'base_cost': 37.5} for i in range(2)])
    await db[COMPANIONS[1]].insert_one({'user_id': owner, 'salla_product_id': '0', 'id': 'option',
        'option_name': 'التغليف', 'value_name': 'فاخر', 'mode': 'direct', 'direct_amount': 4})
    await db[COMPANIONS[2]].insert_one({'user_id': owner, 'salla_product_id': '0', 'id': 'binding',
        'resource_id': 'resource', 'quantity': 2})
    await db[COMPANIONS[3]].insert_one({'user_id': owner, 'id': 'resource', 'unit_cost': 3})
    targets = {
        'catalog': (PRODUCTS, {'salla_product_id': '0'}, {'name': 'Changed catalog name'}),
        'profile': (COMPANIONS[0], {'salla_product_id': '0'}, {'base_cost': 99}),
        'product_binding': (COMPANIONS[2], {'id': 'binding'}, {'quantity': 5}),
        'option_binding': (COMPANIONS[1], {'id': 'option'}, {'direct_amount': 12}),
        'resource': (COMPANIONS[3], {'id': 'resource'}, {'unit_cost': 8}),
        'policy': ('order_status_policy', {'status': 'completed'}, {'category': 'cancelled'}),
    }
    evidence, read_trace, mutations = {'reads': []}, [], []

    class TracedDB:
        def __getitem__(self, name):
            base = db[name]

            class Collection:
                def find(self, *args, **kwargs):
                    read_trace.append((name, bool(mutations)))
                    return base.find(*args, **kwargs)

                def __getattr__(self, name):
                    return getattr(base, name)

            return Collection()

        def __getattr__(self, name):
            return self[name]

    def mutate():
        assert not mutations
        assert [row['limits'] for row in evidence['reads']] == [[100000], [2]]
        assert [len(row['rows']) for row in evidence['reads']] == [2, 2]
        assert read_trace == [(PRODUCTS, False), (PRODUCTS, False)]
        collection, query, fields = targets[kind]
        sync_client[db.name][collection].update_one({'user_id': owner, **query},
                                                  {'$set': fields}, upsert=kind == 'policy')
        mutations.append(kind)

    original_loader = dash._load_cohort_catalog_products
    real_enrich = catalog_module.enrich_current_salla_cost

    def synchronous_fence(row):
        mutate()
        return real_enrich(row)

    async def cooperative_fence():
        mutate()

    async def loader(database, user_id, selected_orders):
        return await gate._catalog_capture_fence(
            original_loader, gate._CatalogReadDB(database, evidence), user_id, selected_orders,
            catalog_module=catalog_module, real_enrich=real_enrich,
            after_capture=cooperative_fence if mode == 'cooperative' else None)

    with patch.object(dash, '_load_cohort_catalog_products', loader), \
         patch.object(catalog_module, 'enrich_current_salla_cost',
                      synchronous_fence if mode == 'synchronous' else real_enrich):
        response = await dash.build_mezan_v2_product_cost_cooperative(TracedDB(), owner, orders)
    assert mutations == [kind]
    assert response['total'] == expected_total
    for collection in (*COMPANIONS, 'order_status_policy'):
        assert (collection, True) in read_trace
        assert (collection, False) not in read_trace
    if kind == 'catalog':
        assert all(row['name'] != 'Changed catalog name' for row in response['product_rows'])
        fresh = await dash.build_mezan_v2_product_cost_cooperative(db, owner, orders)
        assert fresh['total'] == 85
        assert any(row['name'] == 'Changed catalog name' for row in fresh['product_rows'])


@pytest.mark.asyncio
async def test_failed_capture_does_not_fire_fence_or_convert_retryable_error_to_an_oracle_exception():
    fired = []
    real_enrich = lambda row: row
    module = SimpleNamespace(enrich_current_salla_cost=lambda row: fired.append('sync'))

    async def failed():
        raise HTTPException(503, detail={'code': 'catalog_changed_during_cost_read'})

    async def after_capture():
        fired.append('async')

    with pytest.raises(HTTPException) as caught:
        await gate._catalog_capture_fence(failed, catalog_module=module,
                                         real_enrich=real_enrich, after_capture=after_capture)
    assert caught.value.status_code == 503
    assert fired == []
