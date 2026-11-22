"""Compare actual runtime with the immutable #1301 experiment in two processes.

This plugin uses the frozen reference's fixture/mutation drivers, not its AST
builders for the runtime arm. No source transformations of runtime are made.
Only server startup/decorators are excluded by the existing handler loader.
"""
import asyncio
import copy
import hashlib
import json
import os
import time
from collections import Counter
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from dashboard_runtime_fee_contract import extend_frozen_fee_contract

ARM = os.environ.get('DASHBOARD_ADOPTION_ARM', '')
GOLDEN = Path(os.environ.get('DASHBOARD_ADOPTION_GOLDEN', 'reference-responses.json'))
records = {}
calls = Counter()
current_test = None
sequence = 0
active_authority_audit = ContextVar('dashboard_authority_audit', default=None)
active_traffic_audit = ContextVar('dashboard_traffic_audit', default=None)
active_google_audit = ContextVar('dashboard_google_audit', default=None)
active_payment_fee_audit = ContextVar('dashboard_payment_fee_audit', default=None)
active_catalog_audit = ContextVar('dashboard_catalog_audit', default=None)
active_cost_audit = ContextVar('dashboard_cost_audit', default=None)
PAYMENT_FEE_POLICIES = 'mz2_provider_fee_policies_v2'
TRAFFIC_LEDGER = 'mezan_attribution_order_ledger_v1'
GOOGLE_NATIVE_COLLECTION = 'mezan_google_ads_performance_daily_v2'
GOOGLE_SAVED_SOURCE = ('mezan_google_ads_performance_daily_v2:selected_accounts:stored_sar:'
                       'daily_costs.google_ads_positive_legacy_days_only')
# Reviewed polymorphic source fields preserve scalar and object source evidence.
# All remaining projection leaves, owner scope, and the two-query limit stay pinned.
TRAFFIC_PROJECTION_SHA256 = '9b2f00731606f76104bafa9e195b18584f5cc8cca1a514c322da4d5b916c8171'

# Independent read specifications for the two-pass catalog change. These are
# deliberately not imported from runtime, and the frozen JSON oracle is not
# extended: only the exact selected-document read and empty-cost removals below
# may change raw Mongo counts.
CATALOG_COLLECTION = 'mezan_products_v2'
CATALOG_ALIAS_PROJECTION = dict.fromkeys((
    '_id', 'id', 'salla_product_id', 'mezan_product_id', 'name', 'sku', 'variants',
    'raw_salla_details.variants', 'raw_salla_details.skus',
    'raw_salla_details.product_variants', 'raw_salla.variants', 'raw_salla.skus',
), 1)
CATALOG_FULL_PROJECTION = dict.fromkeys((
    '_id', 'id', 'salla_product_id', 'mezan_product_id', 'name', 'sku', 'main_image',
    'cost_price_from_salla', 'cost_price', 'cost', 'variants', 'options',
    'raw_salla', 'raw_salla_details',
), 1)
CATALOG_COMPANIONS = (
    'mezan_product_cost_profiles_v2', 'mezan_product_option_cost_bindings_v2',
    'mezan_product_resource_bindings_v2', 'mezan_cost_resources_v2',
)


def _catalog_json(value):
    # Preserve Mongo physical identity types in the durable JSON evidence.
    from bson import json_util
    return json.loads(json_util.dumps(value))


def _catalog_cohort(user_id, orders):
    numbers = []
    for order in orders:
        assert order.get('user_id') in (None, user_id)
        number = str(order.get('order_number') or '')
        assert number, 'Unidentified order in frozen catalog cohort'
        numbers.append(number)
    assert len(numbers) == len(set(numbers))
    return {'user_id': str(user_id), 'order_numbers': sorted(numbers),
            'order_count': len(orders)}


def _catalog_command(operation, command, *, database_name):
    return {
        'operation': operation, 'collection': command.get(operation),
        'database': database_name, 'command_database': command.get('$db'),
        'filter': _catalog_json(command.get('filter', {})),
        'projection': copy.deepcopy(command.get('projection', {})),
        'unreviewed_fields': sorted(set(command) - {
            'find', 'filter', 'projection', '$db', 'lsid', '$clusterTime',
        }),
    }


class _CatalogReadDB:
    """Observe the actual cursor results/cap without adding another DB read."""
    def __init__(self, db, audit):
        self.db, self.audit = db, audit

    def __getitem__(self, name):
        assert name == CATALOG_COLLECTION, 'Unexpected collection in catalog loader'
        base, audit = self.db[name], self.audit

        class Collection:
            def find(self, query, projection):
                record = {'filter': _catalog_json(query),
                          'projection': copy.deepcopy(projection), 'limits': [], 'rows': []}
                audit['reads'].append(record)
                cursor = base.find(query, projection)

                class Cursor:
                    async def to_list(self, length):
                        record['limits'].append(length)
                        rows = await cursor.to_list(length=length)
                        record['rows'].extend({
                            'physical_id': _catalog_json(row['_id']),
                            'salla_product_id': str(row.get('salla_product_id') or ''),
                        } for row in rows)
                        return rows

                return Cursor()

        return Collection()


async def _catalog_capture_fence(loader, *args, catalog_module, real_enrich,
                                after_capture=None, **kwargs):
    """Preserve the frozen driver's post-full-capture/pre-companion schedule.

    The old first enrichment happened after the full cost-bearing catalog read.
    Alias indexing now precedes that read, so placing the same test fence there
    would test a different race. Only instrumentation moves: capture the selected
    full rows first, then release the unchanged independent writer before any
    companion reads. Production alias/hydration races are separately tested as
    retryable 503 followed by a successful fresh request; no race JSON exception
    or claim of arbitrary concurrent-write parity is made here.
    """
    sync_fence = catalog_module.enrich_current_salla_cost
    with patch.object(catalog_module, 'enrich_current_salla_cost', real_enrich):
        result = await loader(*args, **kwargs)
    if after_capture is not None:
        await after_capture()
    elif sync_fence is not real_enrich:
        # Invoke the frozen synchronous thread fence on an isolated captured row;
        # its enrichment return value cannot change the runtime's captured maps.
        row = next((row for index in result[1] for row in index.values()), None)
        assert row is not None, 'Frozen mutation fence requires a captured product'
        sync_fence(copy.deepcopy(row))
    return result


def _catalog_fixture_spec(fixture_case):
    assert fixture_case, 'Catalog read changes require an immutable fixture identity'
    family_and_case = fixture_case.split('::')[1]
    family, case = family_and_case[:-1].split('[', 1)
    if family == 'test_dashboard_mixed_permissions_and_tenants':
        return family, case, (['0'] if case == 'other_owner'
                              else [str(i) for i in range(40)])
    if family == 'test_existing_independent_read_mutations':
        assert case in {'status', 'payment_status', 'shipping_status', 'payment_only',
                        'shipping_only', 'product_profile_cost', 'embedded_order_cost',
                        'advertising_google', 'date_universe', 'irrelevant_note'}
        return family, case, ['0']
    assert family == 'test_mutations_during_index_preserve_captured_catalog_and_later_reads'
    assert case in {'catalog', 'profile', 'product_binding', 'option_binding', 'resource', 'policy'}
    return family, case, ['0', '1']


def _assert_catalog_read_contract(audit, baseline, response, fixture_case):
    """Return only the independently proved raw-count normalization delta."""
    catalog = audit['catalog']
    assert set(catalog) == {'version', 'cohorts', 'loads', 'commands', 'cost_commands', 'reads'}
    assert catalog['version'] == 1
    cohorts = audit['cohorts']
    expected_cohorts = [{'user_id': row['user_id'],
                         'order_numbers': [order['order_number'] for order in row['orders']],
                         'order_count': len(row['orders'])} for row in cohorts]
    assert catalog['cohorts'] == expected_cohorts, 'Catalog cost cohort changed'
    if not cohorts:
        assert not any(catalog[key] for key in ('loads', 'commands', 'cost_commands', 'reads'))
        return 0, {}
    assert len(cohorts) == 1
    cohort = expected_cohorts[0]
    owner, numbers = cohort['user_id'], cohort['order_numbers']
    assert owner == audit['owner_id']
    # Bind the instrumentation's orders to the independently enumerated frozen
    # financial cohort, not just a candidate-supplied aggregate row count.
    frozen = _frozen_financial_cohort(response, fixture_case)
    decisions = [[owner, '', number, '', False, False, '', False] for number in numbers]
    assert hashlib.sha256(json.dumps(sorted(decisions), ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest() == frozen['eligibility_digest']
    family, case, catalog_ids = _catalog_fixture_spec(fixture_case)
    if not numbers:
        assert not any(catalog[key] for key in ('loads', 'commands', 'cost_commands', 'reads')), \
            'Empty cost cohort performed a query, write, or catalog load'
        assert audit['traffic_cohorts'] == []
        if family == 'test_dashboard_mixed_permissions_and_tenants':
            assert case == 'empty_period'
            counts = dict(zip((CATALOG_COLLECTION, *CATALOG_COMPANIONS), (40, 37, 1, 2, 1)))
            counts['order_status_policy'] = 1
            assert baseline['collections']['order_status_policy'] == 2
        else:
            assert family == 'test_existing_independent_read_mutations'
            assert case in {'status', 'payment_status', 'shipping_status',
                            'payment_only', 'shipping_only', 'date_universe'}
            counts = dict(zip((CATALOG_COLLECTION, *CATALOG_COMPANIONS), (1, 1, 0, 0, 0)))
            counts['order_status_policy'] = 0
            assert baseline['collections']['order_status_policy'] == 0
        for collection in (CATALOG_COLLECTION, *CATALOG_COMPANIONS):
            assert baseline['collections'].get(collection) == counts[collection]
        # Restore exactly the old six finds for comparison. Other query and
        # document counts, including the separate policy read, stay strict.
        return -6, {key: -value for key, value in counts.items()}

    assert catalog['loads'] == [cohort], 'Catalog must load exactly once for a nonempty cohort'
    assert len(audit['traffic_cohorts']) == 1
    assert baseline['collections'][CATALOG_COLLECTION] == len(catalog_ids)
    reads, commands = catalog['reads'], catalog['commands']
    assert reads, 'Missing alias read for nonempty cohort'
    assert all(row['operation'] in {'find', 'aggregate'} for row in catalog['cost_commands']), \
        'Unexpected operation within cost calculation'
    assert [row for row in catalog['cost_commands'] if row['collection'] == CATALOG_COLLECTION] == commands, \
        'Catalog read occurred outside the audited loader'
    alias_rows = reads[0]['rows']
    # The immutable query has no sort; its owner-prefix index may yield lexical
    # rather than insertion order. Keep the observed order in raw evidence, but
    # require only the exact finite identity set here. Duplicate-alias winner
    # order is checked by the separate full-loader parity fixtures.
    assert Counter(row['salla_product_id'] for row in alias_rows) == Counter(catalog_ids), \
        'Frozen owner catalog identities changed'
    physical = {row['salla_product_id']: row['physical_id'] for row in alias_rows}
    physical_keys = [json.dumps(value, sort_keys=True) for value in physical.values()]
    assert len(physical_keys) == len(set(physical_keys))
    assert all(set(value) == {'$oid'} and isinstance(value['$oid'], str)
               and len(value['$oid']) == 24 for value in physical.values())
    # Immutable seed_mixed has one product per number. Item 12 is empty, item 21
    # has a globally ambiguous name, and item 20 resolves uniquely by name. The
    # other-owner fixture and mutation fixtures have only direct product IDs.
    selected = [str(int(number) - 1000000) for number in numbers]
    if family == 'test_dashboard_mixed_permissions_and_tenants' and case != 'other_owner':
        selected = [identity for identity in selected if identity not in {'12', '21'}]
    assert len(selected) == len(set(selected)) and set(selected) <= set(catalog_ids)
    selected_physical = [physical[identity] for identity in selected]
    expected_queries = [({'user_id': owner}, CATALOG_ALIAS_PROJECTION, 100000)]
    if selected:
        expected_queries.append(({'user_id': owner, '_id': {'$in': selected_physical}},
                                 CATALOG_FULL_PROJECTION, len(selected)))
    assert len(reads) == len(commands) == len(expected_queries)

    def canonical_query(query):
        query = copy.deepcopy(query)
        if '_id' in query and isinstance(query['_id'], dict) and '$in' in query['_id']:
            query['_id']['$in'] = sorted(query['_id']['$in'], key=lambda value: json.dumps(value, sort_keys=True))
        return query

    for command, read, (query, projection, limit) in zip(commands, reads, expected_queries):
        expected = {'operation': 'find', 'collection': CATALOG_COLLECTION,
                    'database': audit['database'], 'command_database': audit['database'],
                    'filter': canonical_query(query), 'projection': projection, 'unreviewed_fields': []}
        observed = copy.deepcopy(command)
        observed['filter'] = canonical_query(observed['filter'])
        assert observed == expected, 'Unexpected catalog query, owner, physical winners, projection or operation'
        assert set(read) == {'filter', 'projection', 'limits', 'rows'}
        assert canonical_query(read['filter']) == expected['filter']
        assert read['projection'] == projection and read['limits'] == [limit]
    if selected:
        expected_rows = [{'physical_id': physical[identity], 'salla_product_id': identity}
                         for identity in selected]
        canonical_rows = lambda rows: Counter(json.dumps(row, sort_keys=True) for row in rows)
        assert canonical_rows(reads[1]['rows']) == canonical_rows(expected_rows), \
            'Hydration returned missing, duplicate, or unrelated physical products'
    return int(bool(selected)), ({CATALOG_COLLECTION: len(selected)} if selected else {})


def _traffic_cohort(user_id, orders):
    numbers = []
    for order in orders:
        assert order.get('user_id') in (None, user_id)
        # The immutable fixture has business order numbers and no provider IDs.
        assert not any(order.get(key) for key in ('order_id', 'id', 'reference_id'))
        number = str(order.get('order_number') or '')
        if number:
            numbers.append(number)
    assert len(numbers) == len(set(numbers))
    return {'user_id': str(user_id), 'numbers': sorted(numbers)}


def _traffic_command(operation, command, *, database_name):
    collection = command.get(operation)
    projection = copy.deepcopy(command.get('projection', {}))
    return {'operation': operation, 'collection': collection,
            'database': database_name, 'command_database': command.get('$db'),
            'filter': copy.deepcopy(command.get('filter', {})),
            'projection_sha256': hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'unreviewed_fields': sorted(set(command) - {'find', 'filter', 'projection', '$db', 'lsid', '$clusterTime'})}


def _expected_traffic_commands(audit):
    cohorts = audit['traffic_cohorts']
    assert len(cohorts) <= 1, 'Traffic must load once per request cohort'
    expected = []
    for cohort in cohorts:
        owner, numbers = cohort['user_id'], cohort['numbers']
        assert owner == audit['owner_id']
        assert numbers == sorted(set(numbers)) and all(numbers)
        assert numbers == [row['order_number'] for row in audit['cohorts'][0]['orders']]
        if not numbers:
            continue
        for collection, query, projection_hash in (
            ('unified_orders', {'user_id': owner, 'order_number': {'$in': numbers}}, TRAFFIC_PROJECTION_SHA256),
            (TRAFFIC_LEDGER, {'user_id': owner, 'order_key': {'$in': numbers}},
             hashlib.sha256(json.dumps({'_id': 0, 'user_id': 1, 'order_key': 1, 'attribution': 1}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()),
        ):
            expected.append({'operation': 'find', 'collection': collection,
                             'database': audit['database'], 'command_database': audit['database'],
                             'filter': query, 'projection_sha256': projection_hash, 'unreviewed_fields': []})
    return expected

# This is an explicit read-contract extension for MZ2 historical cost authority,
# not a new JSON oracle. The immutable fixtures contain no historical cost rows
# or supplier receipt evidence. The received-unit cost rule adds exactly two
# empty, tenant/order-scoped receipt reads per batch, before any invoice reads.
# Keep these names/shapes independent of production constants so an accidental
# collection, scope, pipeline or batching change fails the gate.
AUTHORITY_COLLECTIONS = frozenset({
    'mezan_bundle_definitions_v2', 'mezan_order_item_cost_snapshots_v2',
    'mezan_component_order_lifecycle_v1', 'mezan_component_consumption_plans_v1',
    'mezan_component_consumption_units_v1', 'mezan_order_cost_corrections_v1',
    'mezan_preparation_pieces_v1', 'mezan_supplier_receiving_events_v1',
})
AUTHORITY_BATCH_SIZE = 250
# Independently reviewed proof-only projections. Do not import the candidate's
# constants: adding a field to a production query must require gate review.
RECEIPT_PROJECTIONS = {
    'mezan_preparation_pieces_v1': {'_id': 0, **dict.fromkeys((
        'id', 'piece_id', 'order_number', 'order_item_id', 'unit_index', 'product_id',
        'variant_id', 'salla_variant_id', 'experiment_mode', 'experiment_run_id',
        'services', 'service_plan_status', 'remaining_service_count',
        'supplier_dispatch_status', 'received_at', 'supplier_receiving_history.invoice_id',
        'service_specifications_snapshot', 'options_raw', 'selected_options',
        'options', 'custom_fields',
    ), 1)},
    'mezan_supplier_receiving_events_v1': {'_id': 0, **dict.fromkeys((
        'id', 'piece_id', 'order_number', 'order_item_id', 'unit_index', 'product_id',
        'variant_id', 'salla_variant_id', 'event_type', 'session_id',
        'supplier_invoice_id', 'finalized_at', 'experiment_mode', 'experiment_run_id',
        'financial_invoice_created', 'liability_created', 'supplier_context.id',
        'service_specifications_snapshot', 'options_raw', 'selected_options',
        'options', 'custom_fields', 'services.condition',
    ), 1)},
}


def _cohort_evidence(user_id, orders):
    selected = {}
    for order in orders:
        number = str(order.get('order_number') or '')
        if not number:
            continue
        assert not order.get('user_id') or str(order['user_id']) == str(user_id)
        assert number not in selected, 'Duplicate order identity in runtime gate cohort'
        lines = []
        for item in order.get('products') or order.get('items') or []:
            if not isinstance(item, dict):
                if hasattr(item, 'model_dump'):
                    item = item.model_dump(mode='python')
                elif hasattr(item, '__dict__'):
                    item = vars(item)
                else:
                    continue
            raw = item.get('raw_item') if isinstance(item.get('raw_item'), dict) else {}
            primary = (item.get('canonical_order_item_id') or item.get('order_item_id')
                       or item.get('order_line_id') or item.get('source_item_id') or item.get('id'))
            aliases = sorted({str(value).strip() for value in (
                primary, item.get('order_item_id'), item.get('order_line_id'),
                item.get('source_item_id'), raw.get('id')) if value not in (None, '')})
            lines.append({'aliases': aliases})
        selected[number] = {'order_number': number, 'lines': lines}
    return {'user_id': str(user_id), 'orders': [selected[key] for key in sorted(selected)]}


def _compact_snapshot_projection(field):
    # Exact reviewed read-only aggregation expression, deliberately independent
    # of production code. Additional stages/operators are not permitted.
    return {'$arrayToObject': {'$map': {
        'input': {'$objectToArray': {'$ifNull': ['$' + field, {}]}},
        'as': 'entry', 'in': {'k': '$$entry.k', 'v': {'$cond': [
            {'$eq': ['$$entry.v', None]}, None,
            {'$arrayToObject': {'$filter': {
                'input': {'$objectToArray': '$$entry.v'}, 'as': 'part',
                'cond': {'$ne': ['$$part.k', 'frozen_definitions']},
            }}},
        ]}},
    }}}


def _fixture_database_name(db):
    from motor.motor_asyncio import AsyncIOMotorDatabase
    visited = set()
    while not isinstance(db, AsyncIOMotorDatabase):
        assert id(db) not in visited, 'Cyclic fixture database wrapper'
        visited.add(id(db))
        fields = vars(db)
        # The frozen ReadDB/ReadTraceDB/GatedDB wrappers expose one of these.
        # Use their actual instance fields, never dynamic collection lookup.
        if 'db' in fields:
            db = fields['db']
        else:
            assert 'base' in fields, 'Unknown fixture database wrapper'
            db = fields['base']
    assert db.name.startswith('dashboard_b_shared_'), 'Unexpected fixture database'
    return db.name


def _authority_command(operation, command, *, database_name):
    collection = command.get(operation)
    if not isinstance(collection, str) or collection not in AUTHORITY_COLLECTIONS:
        return None
    record = {'operation': operation, 'collection': collection,
              'database': database_name, 'command_database': command.get('$db')}
    if operation == 'find':
        record.update(filter=copy.deepcopy(command.get('filter', {})),
                      projection=copy.deepcopy(command.get('projection', {})),
                      limit=command.get('limit'))
        semantic_fields = {'find', 'filter', 'projection', 'limit'}
    elif operation == 'aggregate':
        record['pipeline'] = copy.deepcopy(command.get('pipeline', []))
        record['cursor'] = copy.deepcopy(command.get('cursor'))
        semantic_fields = {'aggregate', 'pipeline', 'cursor'}
    else:
        semantic_fields = {operation}
    # Only driver session metadata is outside the semantic contract; $db and
    # the event namespace are independently bound to the actual fixture DB.
    # Do not silently discard sort, skip, batchSize, hint, readConcern, explain,
    # writeConcern or future command options. Their mere presence fails closed.
    record['unreviewed_fields'] = sorted(set(command) - semantic_fields - {'$db', 'lsid', '$clusterTime'})
    # Other operations are retained and rejected by the exact expected list.
    return record


def _expected_authority_commands(audit):
    assert audit.get('version') == 1
    assert isinstance(audit.get('owner_id'), str) and audit['owner_id']
    database = audit['database']
    assert isinstance(database, str) and database.startswith('dashboard_b_shared_')
    assert len(audit['cohorts']) <= 1, 'Cost authority must load once per request cohort'
    expected = []

    def find(collection, query, projection):
        expected.append({'operation': 'find', 'collection': collection,
                         'database': database, 'command_database': database,
                         'filter': query, 'projection': copy.deepcopy(projection), 'limit': None,
                         'unreviewed_fields': []})

    def snapshots(collection, query, projection, field, limit):
        expected.append({'operation': 'aggregate', 'collection': collection,
                         'database': database, 'command_database': database, 'pipeline': [
            {'$match': query}, {'$limit': limit + 1},
            {'$project': {**projection, field: _compact_snapshot_projection(field)}},
        ], 'cursor': {}, 'unreviewed_fields': []})

    for cohort in audit['cohorts']:
        owner = cohort['user_id']
        assert owner == audit['owner_id'], 'Authority cohort has the wrong owner'
        orders = cohort['orders']
        numbers = [row['order_number'] for row in orders]
        assert numbers == sorted(set(numbers)) and all(numbers)
        if not orders:
            continue
        find('mezan_bundle_definitions_v2', {'user_id': owner, 'product_type': 'composite'},
             {'_id': 0, 'product_id': 1})
        for offset in range(0, len(orders), AUTHORITY_BATCH_SIZE):
            batch = orders[offset:offset + AUTHORITY_BATCH_SIZE]
            selected = [row['order_number'] for row in batch]
            aliases = sorted({alias for row in batch for line in row['lines'] for alias in line['aliases']})
            keys = {'user_id': owner, 'order_number': {'$in': selected}}
            find('mezan_order_item_cost_snapshots_v2',
                 {**keys, 'order_item_id': {'$in': aliases}}, {'_id': 0})
            snapshots('mezan_component_order_lifecycle_v1', keys,
                      {'_id': 0, 'order_number': 1, 'bundle_intake_snapshots': 1,
                       'reporting_cost_snapshots': 1, 'source_fingerprint': 1},
                      'bundle_intake_snapshots', len(batch))
            snapshots('mezan_component_consumption_plans_v1',
                      {'user_id': owner, 'order_id': {'$in': selected}, 'bundle_snapshots': {'$exists': True}},
                      {'_id': 1, 'order_id': 1, 'bundle_snapshots': 1, 'reconciliation_required': 1, 'lines': 1},
                      'bundle_snapshots', len(batch))
            # These immutable legacy fixtures contain no plans. Any plan or
            # other historical row is a hard failure, not ignored evidence.
            find('mezan_component_consumption_units_v1',
                 {'user_id': owner, 'plan_id': {'$in': []}, 'state': {'$ne': 'released'},
                  'order_line_id': {'$in': aliases}},
                 {'_id': 0, 'plan_id': 1, 'order_line_id': 1, 'unit_index': 1,
                  'state': 1, 'selected_context': 1, 'bundle_leaves': 1})
            find('mezan_order_cost_corrections_v1',
                 {**keys, 'state': 'applied', 'order_item_id': {'$in': aliases}}, {'_id': 0})
        # The supplier reader runs after the historical reader completes all
        # batches. With no receipt evidence it must not query invoices,
        # suppliers, products or financial journal groups.
        for offset in range(0, len(orders), AUTHORITY_BATCH_SIZE):
            selected = [row['order_number'] for row in orders[offset:offset + AUTHORITY_BATCH_SIZE]]
            keys = {'user_id': owner, 'order_number': {'$in': selected}}
            find('mezan_preparation_pieces_v1', keys,
                 RECEIPT_PROJECTIONS['mezan_preparation_pieces_v1'])
            find('mezan_supplier_receiving_events_v1',
                 {**keys, 'event_type': 'supplier_piece_service_recorded'},
                 RECEIPT_PROJECTIONS['mezan_supplier_receiving_events_v1'])
    return expected


def _google_command(operation, command, *, database_name):
    """Keep every command in the saved Google reader, including unexpected I/O."""
    record = {'operation': operation, 'collection': command.get(operation),
              'database': database_name, 'command_database': command.get('$db')}
    if operation == 'find':
        record.update(filter=copy.deepcopy(command.get('filter', {})),
                      projection=copy.deepcopy(command.get('projection', {})),
                      limit=command.get('limit'))
        fields = {'find', 'filter', 'projection', 'limit'}
    else:
        fields = {operation}
    record['unreviewed_fields'] = sorted(set(command) - fields - {'$db', 'lsid', '$clusterTime'})
    return record


def _payment_fee_command(operation, command, *, database_name):
    """Capture all I/O in fee setup, including any unexpected write/read."""
    record = _google_command(operation, command, database_name=database_name)
    if operation == 'find':
        # find_one has an explicit singleBatch flag; retain its exact value.
        record['single_batch'] = command.get('singleBatch')
        record['unreviewed_fields'] = [key for key in record['unreviewed_fields']
                                       if key != 'singleBatch']
    return record


def _expected_payment_fee_commands(audit, response):
    """Exactly two empty owner-scoped reads, independent of runtime helpers.

    The immutable fixture has no dated fee policies or source-mode document.
    It therefore resolves to legacy settings. All existing setting/document
    counts remain frozen; only these two precisely audited finds are allowed.
    """
    cohorts = audit.get('payment_fee_cohorts', [])
    if 'payment_breakdown' not in response:
        assert cohorts == [], 'Fee setup must not load outside a financial response'
        return []
    owner = audit['owner_id']
    assert cohorts == [{'user_id': owner}], 'Fee setup must load once for the exact owner'
    mode_id = hashlib.sha256(json.dumps([owner, 'fee_source_mode']).encode()).hexdigest()
    common = {'operation': 'find', 'collection': PAYMENT_FEE_POLICIES,
              'database': audit['database'], 'command_database': audit['database'],
              'unreviewed_fields': []}
    return [
        {**common, 'filter': {'user_id': owner}, 'projection': {'_id': 0},
         'limit': None, 'single_batch': None},
        {**common, 'filter': {'_id': mode_id, 'user_id': owner}, 'projection': {},
         'limit': 1, 'single_batch': True},
    ]


def _expected_google_commands(audit, response):
    """Four exact reads replace the one legacy read; net growth is three finds.

    The immutable fixtures have no Google integration/account/native facts.
    Meta/TikTok share two collection names, so their counts must remain intact.
    The existing integration read also carries the saved owner selection policy.
    Dates come from the frozen historical history, not candidate call arguments.
    """
    cohorts = audit['google_cohorts']
    if 'ads_v2' not in response:
        assert cohorts == [], 'Google must not load for a permission failure'
        return []
    history = response['ads_v2']['history']
    assert history, 'Unreviewed empty Google period'
    dates = [row['date'] for row in history]
    start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    assert dates == [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    assert 1 <= len(dates) <= 90
    owner = audit['owner_id']
    assert cohorts == [{'user_id': owner, 'date_from': dates[0], 'date_to': dates[-1]}], \
        'Google must load once for the exact request owner and historical period'
    period = {'$gte': dates[0], '$lte': dates[-1]}
    specifications = [
        ('mezan_integrations_v2', {'user_id': owner, 'provider': 'google_ads'},
         {'_id': 0, 'connection_status': 1,
          'account_selection_configured': 1, 'selected_account_ids': 1}),
        ('mezan_integration_accounts_v2',
         {'user_id': owner, 'provider': 'google_ads', 'connection_status': 'connected',
          'connection_provenance': 'api_connection'},
         {'_id': 0, 'ad_account_id': 1, 'external_account_id': 1, 'mezan_selected': 1,
          'currency': 1, 'timezone': 1}),
        (GOOGLE_NATIVE_COLLECTION, {'user_id': owner, 'provider': 'google_ads', 'date': period},
         {'_id': 0, 'ad_account_id': 1, 'date': 1, 'source_mode': 1, 'account_timezone': 1,
          'currency_native': 1, 'spend_native': 1, 'spend_sar': 1, 'fx_rate_to_sar': 1,
          'source_close_proof': 1, 'observed_at': 1, 'updated_at': 1}),
        ('daily_costs', {'user_id': owner, 'date': period}, {'_id': 0, 'date': 1, 'google_ads': 1}),
    ]
    return [{'operation': 'find', 'collection': collection,
             'database': audit['database'], 'command_database': audit['database'],
             'filter': query, 'projection': projection, 'limit': None,
             'unreviewed_fields': []} for collection, query, projection in specifications]


def _extend_google_saved_contract(response):
    """Derive Google coverage solely from immutable positive legacy evidence.

    No financial amount is recomputed or replaced. Every old monetary byte,
    including raw-sum period rounding, remains part of the historical oracle.
    Missing Google observations become null history rather than fabricated zero.
    """
    if 'ads_v2' not in response:
        return
    ads = response['ads_v2']
    old_source = 'daily_costs.google_ads:transitional_read_only:no_account_allocation'
    assert ads['source_contract']['google'] == old_source
    assert response['source_contract']['advertising']['google'] == old_source
    assert 'google' not in ads['providers'] and 'google' not in ads['spend_quality']
    history = ads['history']
    assert history and all(isinstance(row['google'], (int, float)) and row['google'] >= 0 for row in history)
    observed = sum(row['google'] > 0 for row in history)
    provisional = observed > 0
    assert (ads['breakdown']['google_transitional'] > 0) == provisional
    # All immutable fixtures have disconnected Snapchat and no Google account.
    # A future fixture with other quality states requires an explicit review.
    assert ads['spend_quality']['status'] == 'complete'
    assert ads['spend_quality']['amount_complete'] is True
    assert ads['spend_quality']['amount_available'] is True
    assert ads['spend_quality']['provisional'] is False
    quality = {
        'status': 'provisional' if provisional else 'not_required',
        'data_state': 'provisional_data' if provisional else 'not_required',
        'amount_available': True, 'amount_complete': not provisional,
        'coverage_complete': not provisional, 'provisional': provisional,
        'expected': False,
        'reason_codes': ['google_legacy_manual_fallback'] if provisional else [],
        'last_observed_at': None, 'observed_days': observed,
        'requested_days': len(history), 'account_count': 0,
    }
    for row in history:
        if row['google'] == 0:
            row['google'] = None
    providers = {}
    assert list(ads['providers']) == ['meta', 'tiktok', 'snapchat']
    for provider, metrics in ads['providers'].items():
        if provider == 'snapchat':
            providers['google'] = {'spend': ads['breakdown']['google_transitional'], **quality}
        providers[provider] = metrics
    ads['providers'] = providers
    ads['spend_quality'].update(status='provisional' if provisional else 'complete',
                                amount_complete=not provisional, provisional=provisional,
                                google=copy.deepcopy(quality))
    if not provisional:
        ads['bank_commissions']['google_account_allocation'] = 'not_required'
    ads['source_contract']['google'] = GOOGLE_SAVED_SOURCE
    response['source_contract']['advertising']['google'] = GOOGLE_SAVED_SOURCE
    executive = ads['executive_breakdown']['providers']['google']
    executive.update(data_state=quality['data_state'], coverage_complete=not provisional,
                     amount_complete=not provisional)
    response['totals']['ads_spend_data_complete'] = not provisional
    response['totals']['ads_spend_provisional'] = provisional


def _frozen_financial_cohort(response, fixture_case):
    """Independently enumerate the pinned 05b159dc synthetic source identities.

    None of these fixtures has a waiting/wating method or a native order ID.
    Date/filter mutations are intentionally retained; the digest cannot be
    copied from the candidate or inferred just from an aggregate order count.
    """
    family_and_case = fixture_case.split('::')[1]
    family, case = family_and_case[:-1].split('[', 1)
    owner = 'fixture-owner'
    if family == 'test_dashboard_mixed_permissions_and_tenants':
        indices = [i for i in range(40) if i not in (0, 3)]
        if case == 'other_owner':
            owner, indices = 'fixture-other', [0]
        elif case == 'prior_month':
            indices = [0]
        elif case == 'empty_period':
            indices = []
        elif case == 'first_day_inclusive':
            indices = [1]
        elif case == 'last_day_inclusive':
            indices = [2]
        elif case == 'overlapping_period':
            indices = list(range(4, 40))
        elif case == 'no_dates':
            indices = list(range(40))
        elif case == 'from_only':
            indices = list(range(1, 40))
        elif case == 'to_only':
            indices = [i for i in range(40) if i != 3]
        elif case == 'hide_inferred':
            indices.remove(4)
        elif case in {'payment_substring_case', 'bnpl_and_cod', 'combined_filters',
                      'no_matching_filter', 'rounding_and_known_fx'}:
            methods = ['mada', 'apple', 'bank', 'tabby', 'tamara', 'cod']
            allowed = {
                'payment_substring_case': {'mada', 'apple'},
                'bnpl_and_cod': {'tabby', 'tamara', 'cod'},
                'combined_filters': {'mada'}, 'no_matching_filter': set(),
                'rounding_and_known_fx': {'bank'},
            }[case]
            indices = [i for i in indices if i == 14 or
                       ('bank' if i in (8, 11) else methods[i % 6]) in allowed]
            if case == 'combined_filters':
                indices = [i for i in indices if i == 14 or i % 3 == 0]
        elif case == 'completed_status':
            indices = [i for i in indices if i % 6 == 0 or i in (5, 14)]
        elif case == 'arabic_status_normalization':
            indices = [i for i in indices if i % 6 == 4 or i == 14]
        elif case == 'returns_only':
            indices = [i for i in indices if (i % 6 in (1, 2) and i != 7) or i == 14]
        else:
            assert case in {'current_month_mixed', 'include_inferred', 'shipping_substring',
                            'all_statuses', 'electronic_no_exclusions'}, 'Unreviewed mixed cohort'
    elif family == 'test_existing_independent_read_mutations':
        if case in {'status', 'payment_status', 'shipping_status', 'payment_only',
                    'shipping_only', 'date_universe'}:
            indices = []
        else:
            assert case in {'product_profile_cost', 'embedded_order_cost', 'advertising_google', 'irrelevant_note'}
            indices = [0]
    else:
        assert family == 'test_mutations_during_index_preserve_captured_catalog_and_later_reads'
        assert case in {'catalog', 'profile', 'product_binding', 'option_binding', 'resource', 'policy'}
        indices = [0, 1]
    assert response['totals']['total_orders'] == len(indices), 'Frozen cohort/order count disagrees'
    decisions = [[owner, '', str(1000000 + i), '', False, False, '', False] for i in indices]
    return {'reader_version': 'financial-cohort-deferred-dashboard-v2',
            'total_orders': len(indices), 'financial_eligible_orders': len(indices),
            'excluded_unpaid_orders': 0, 'review_orders': 0, 'complete': True,
            'review_reason_counts': {}, 'eligibility_digest': hashlib.sha256(
                json.dumps(sorted(decisions), ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}


def _extend_financial_cohort_contract(response, fixture_case):
    if 'payment_breakdown' not in response:
        return
    assert fixture_case, 'Financial cohort extension requires an immutable fixture identity'
    cohort = _frozen_financial_cohort(response, fixture_case)
    # Insert only the reviewed fields at their documented response positions.
    expanded = {}
    for key, value in response.items():
        expanded[key] = value
        if key == 'range':
            expanded['financial_cohort'] = cohort
    response.clear()
    response.update(expanded)
    totals = {}
    for key, value in response['totals'].items():
        totals[key] = value
        if key == 'total_orders':
            totals.update(financial_eligible_orders=cohort['financial_eligible_orders'],
                          excluded_unpaid_orders=0, review_orders=0, financial_cohort_complete=True)
    response['totals'] = totals
    # The month cohort uses its own pinned period/filter count. It need not
    # equal the selected range count (prior/empty/boundary fixtures), and has
    # no waiting/wating methods either. Preserve every original month value.
    month = {}
    for key, value in response['month_kpis'].items():
        month[key] = value
        if key == 'total_orders':
            assert isinstance(value, int) and value >= 0
            month.update(financial_eligible_orders=value,
                         excluded_unpaid_orders=0, review_orders=0)
    response['month_kpis'] = month


def expected_cost_completeness_json(historical_json, *, fixture_case=None):
    """Owner-approved response extension; all other historical bytes stay exact.

    Derive the new contract solely from the frozen response's cost coverage,
    never from the candidate response or production resolver.
    """
    response = json.loads(historical_json)
    if 'product_cost_v2' not in response or 'totals' not in response:
        return historical_json
    costs = response['product_cost_v2']
    # Independent oracle for immutable 05b159dc fixtures: each product belongs
    # to one seeded order/source, never a ledger account. The mixed seed cycles
    # these sources, with explicit overrides at 9/10 and historical alias 21.
    # Return-policy quantity is taken from the unchanged historical row.
    # populate() gives every order Snapchat; only seed_mixed() applies the
    # source cycle. In particular, index-mutation product 1 is not Meta.
    fixture_family = (fixture_case.split('::')[1].split('[', 1)[0]
                      if fixture_case is not None else 'test_dashboard_mixed_permissions_and_tenants')
    assert fixture_family in {
        'test_dashboard_mixed_permissions_and_tenants',
        'test_existing_independent_read_mutations',
        'test_mutations_during_index_preserve_captured_catalog_and_later_reads',
    }, 'Unreviewed immutable fixture family'
    mixed = fixture_family == 'test_dashboard_mixed_permissions_and_tenants'
    labels = {'snapchat': 'سناب شات', 'meta': 'ميتا', 'tiktok': 'تيك توك',
              'google': 'جوجل', 'direct': 'مباشر'}
    for index, row in enumerate(costs.get('product_rows', [])):
        pid = str(row['salla_product_id'])
        fixture_index = 21 if pid == 'historical' else int(pid)
        assert 0 <= fixture_index < 40, 'Unreviewed immutable fixture product'
        if not mixed:
            maximum = 0 if fixture_family == 'test_existing_independent_read_mutations' else 1
            assert 0 <= fixture_index <= maximum, 'Unreviewed populate fixture product'
        platform = ('snapchat', 'meta', 'tiktok', 'google', 'direct')[fixture_index % 5] if mixed else 'snapchat'
        if mixed and fixture_index == 9:
            platform = 'snapchat'
        elif mixed and fixture_index == 10:
            platform = 'meta'
        source = {'platform': platform, 'platform_label': labels[platform],
                  'account_id': None,
                  'account_status': 'not_applicable' if platform == 'direct' else 'unknown',
                  'attribution_status': 'confirmed' if platform == 'direct' else 'platform_only',
                  'units_sold': float(row['units_sold']),
                  # The frozen fixtures have no ledger campaigns. Their full
                  # source quantity must remain unknown (or non-campaign).
                  'campaigns': [{'campaign_id': None, 'campaign_name': None,
                      'attribution_status': 'not_applicable' if platform == 'direct' else 'unknown',
                      'units_sold': float(row['units_sold'])}]}
        expanded = {}
        for key, value in row.items():
            expanded[key] = value
            if key == 'units_sold':
                expanded['traffic_sources'] = [source]
        costs['product_rows'][index] = expanded
    complete = (costs['missing_all_cost_products_count'] == 0
                and costs['no_products_orders_count'] == 0)
    response['totals']['product_cost_complete'] = complete
    if not complete:
        response['totals']['net_profit'] = None
        if response['net_sales_config'].get('deduct_product_costs', True):
            response['totals']['net_sales'] = None
    _extend_google_saved_contract(response)
    extend_frozen_fee_contract(response, fixture_case=fixture_case)
    _extend_financial_cohort_contract(response, fixture_case)
    return json.dumps(response, ensure_ascii=False, default=str)


def assert_runtime_read_contract(runtime, baseline, *, fixture_case=None):
    """Normalize only independently audited read changes; preserve raw evidence."""
    assert set(runtime) == set(baseline) | {'cost_authority_audit'}
    assert runtime['json'] == expected_cost_completeness_json(baseline['json'], fixture_case=fixture_case), 'Unapproved JSON response change'
    audit = runtime['cost_authority_audit']
    expected = _expected_authority_commands(audit)
    assert audit['commands'] == expected, 'Unexpected authority query, scope, operation or batch count'
    traffic_expected = _expected_traffic_commands(audit)
    assert audit['traffic_commands'] == traffic_expected, 'Unexpected traffic query, scope, projection or count'
    google_expected = _expected_google_commands(audit, json.loads(baseline['json']))
    # The four independent reads run concurrently; preserve exact multiplicity
    # and semantics without imposing an unsupported wire arrival order.
    canonical = lambda rows: Counter(json.dumps(row, sort_keys=True) for row in rows)
    assert canonical(audit['google_commands']) == canonical(google_expected), \
        'Unexpected Google query, owner, period, projection, operation or count'
    fee_expected = _expected_payment_fee_commands(audit, json.loads(baseline['json']))
    assert canonical(audit.get('payment_fee_commands', [])) == canonical(fee_expected), \
        'Unexpected payment fee query, owner, projection, operation or count'
    response = json.loads(runtime['json'])
    assert len(audit['cohorts']) == (0 if 'http_error' in response else 1)
    if fixture_case is not None and ('payment_breakdown' in response or 'http_error' in response):
        assert 'catalog' in audit, 'Complete frozen runtime evidence requires the catalog audit'
    catalog_commands, catalog_documents = (0, {})
    if 'catalog' in audit:
        catalog_commands, catalog_documents = _assert_catalog_read_contract(
            audit, baseline, response, fixture_case)
    else:
        assert len(audit['traffic_cohorts']) == len(audit['cohorts'])
    assert not (set(baseline['collections']) & AUTHORITY_COLLECTIONS)
    for collection in AUTHORITY_COLLECTIONS:
        assert runtime['collections'].get(collection, 0) == 0, 'Legacy fixture unexpectedly read historical cost rows'
    observed_collections = set(runtime['collections']) & AUTHORITY_COLLECTIONS
    assert observed_collections == {row['collection'] for row in expected}
    assert TRAFFIC_LEDGER not in baseline['collections']
    if traffic_expected:
        assert runtime['collections'].get(TRAFFIC_LEDGER) == 0, 'Immutable fixture has no account attribution ledger rows'
    else:
        assert TRAFFIC_LEDGER not in runtime['collections']
    assert GOOGLE_NATIVE_COLLECTION not in baseline['collections']
    if google_expected:
        assert runtime['collections'].get(GOOGLE_NATIVE_COLLECTION) == 0, \
            'Immutable fixture unexpectedly read Google native facts'
    else:
        assert GOOGLE_NATIVE_COLLECTION not in runtime['collections']
    assert PAYMENT_FEE_POLICIES not in baseline['collections']
    if fee_expected:
        assert runtime['collections'].get(PAYMENT_FEE_POLICIES) == 0, \
            'Immutable fixture unexpectedly read dated payment fee policies'
    else:
        assert PAYMENT_FEE_POLICIES not in runtime['collections']
    normalized = {key: copy.deepcopy(value) for key, value in runtime.items()
                  if key != 'cost_authority_audit'}
    normalized['json'] = baseline['json']  # Validated exact approved extension above.
    for operation, count in Counter(row['operation'] for row in expected + traffic_expected + fee_expected).items():
        assert normalized['commands'].get(operation, 0) >= count
        normalized['commands'][operation] -= count
        if normalized['commands'][operation] == 0:
            del normalized['commands'][operation]
    if google_expected:
        # daily_costs is a replacement read, already present in the baseline.
        assert normalized['commands'].get('find', 0) >= 3
        normalized['commands']['find'] -= 3
        if normalized['commands']['find'] == 0:
            del normalized['commands']['find']
    normalized['collections'] = {key: value for key, value in normalized['collections'].items()
                                 if key not in AUTHORITY_COLLECTIONS
                                 and key not in {TRAFFIC_LEDGER, GOOGLE_NATIVE_COLLECTION, PAYMENT_FEE_POLICIES}}
    extra_documents = sum(len(cohort['numbers']) for cohort in audit['traffic_cohorts'])
    if extra_documents:
        normalized['documents'] -= extra_documents
        normalized['collections']['unified_orders'] -= extra_documents
    if catalog_commands:
        assert normalized['commands'].get('find', 0) >= max(catalog_commands, 0)
        normalized['commands']['find'] = normalized['commands'].get('find', 0) - catalog_commands
        if normalized['commands']['find'] == 0:
            del normalized['commands']['find']
    for collection, count in catalog_documents.items():
        if catalog_commands < 0:
            if collection == 'order_status_policy':
                assert collection in normalized['collections'], 'Separate policy read disappeared'
            else:
                assert collection not in normalized['collections'], 'Empty cohort retained an old cost read'
        normalized['documents'] -= count
        normalized['collections'][collection] = normalized['collections'].get(collection, 0) - count
    assert normalized == baseline, 'Existing Mongo commands or document counts changed'


def pytest_runtest_setup(item):
    global current_test, sequence
    current_test, sequence = item.nodeid, 0


@contextmanager
def _runtime_legacy_adapter(handler, parser):
    """Bind the extracted HTTP adapter to its actual canonical dependency.

    The immutable fixture excludes server imports to avoid application startup.
    Its current adapter now delegates to a separate, bootstrap-free module;
    bind that real reader and observe its parser without changing either body.
    Historical reference and Production-baseline arms never enter this scope.
    """
    import legacy_dashboard_reader as canonical
    assert Path(canonical.__file__).resolve().parent == Path(__file__).resolve().parents[1]
    with patch.dict(handler.__globals__, {'_canonical_dashboard': canonical.dashboard}), \
         patch.object(canonical, 'orders_to_parsed_cooperative', parser):
        yield handler


def pytest_collection_modifyitems(session, config, items):
    assert ARM in {'reference', 'baseline', 'runtime'}, 'Explicit dashboard adoption arm required'
    modules = {item.module for item in items}
    assert len(modules) == 1
    cases = modules.pop()
    assert Path(cases.__file__).name == 'test_dashboard_b_index_cooperative_correctness.py'
    import dashboard_v2_routes
    import orders_db
    import order_currency
    import product_catalog_cost_resolution
    import dashboard_v2_ads_executive
    for module in (dashboard_v2_routes, orders_db, order_currency,
                   product_catalog_cost_resolution, dashboard_v2_ads_executive):
        assert Path(module.__file__).resolve().parent == Path.cwd().resolve(), module.__file__
    original_invoke = cases.invoke
    expected = json.loads(GOLDEN.read_text()) if ARM != 'reference' else None
    read_baseline_path = os.environ.get('DASHBOARD_ADOPTION_READ_BASELINE')
    read_baseline = (json.loads(Path(read_baseline_path).read_text())
                     if ARM == 'runtime' and read_baseline_path else None)

    async def recorded(*args, **kwargs):
        global sequence
        audit = None
        if ARM == 'runtime':
            user = kwargs.get('user', args[4] if len(args) > 4 else cases.OWNER)
            audit = {'version': 1, 'owner_id': str(user['id']),
                     'database': _fixture_database_name(args[0]), 'cohorts': [], 'commands': [],
                     'traffic_cohorts': [], 'traffic_commands': [],
                     'google_cohorts': [], 'google_commands': [],
                     'payment_fee_cohorts': [], 'payment_fee_commands': [],
                     'catalog': {'version': 1, 'cohorts': [], 'loads': [],
                                 'commands': [], 'cost_commands': [], 'reads': []}}
            commands = args[1]
            original_started = commands.started

            def started(event):
                original_started(event)
                if active_cost_audit.get() is audit:
                    command = _catalog_command(event.command_name, event.command,
                                               database_name=event.database_name)
                    audit['catalog']['cost_commands'].append(command)
                    if active_catalog_audit.get() is audit:
                        audit['catalog']['commands'].append(command)
                if active_payment_fee_audit.get() is audit:
                    audit['payment_fee_commands'].append(_payment_fee_command(
                        event.command_name, event.command, database_name=event.database_name))
                    return
                if active_google_audit.get() is audit:
                    audit['google_commands'].append(_google_command(event.command_name, event.command,
                                                                   database_name=event.database_name))
                    return
                if active_traffic_audit.get() is audit:
                    audit['traffic_commands'].append(_traffic_command(event.command_name, event.command,
                                                                     database_name=event.database_name))
                    return
                command = _authority_command(event.command_name, event.command,
                                             database_name=event.database_name)
                if command is not None:
                    audit['commands'].append(command)

            token = active_authority_audit.set(audit)
            try:
                with patch.object(commands, 'started', started):
                    result, metrics = await original_invoke(*args, **kwargs)
            finally:
                active_authority_audit.reset(token)
        else:
            result, metrics = await original_invoke(*args, **kwargs)
        key = current_test + '::request-' + str(sequence)
        sequence += 1
        value = {
            # Do not sort keys: preserve both array order and JSON member order.
            'json': json.dumps(result, ensure_ascii=False, default=str),
            'commands': metrics['mongo_commands'],
            'documents': metrics['documents_read'],
            'collections': metrics['docs_by_collection'],
        }
        if audit is not None:
            value['cost_authority_audit'] = audit
        records[key] = value
        if expected is not None:
            expected_json = expected[key]['json']
            if ARM == 'runtime':
                expected_json = expected_cost_completeness_json(expected_json, fixture_case=key)
            assert value['json'] == expected_json, key
            if ARM == 'runtime':
                assert_runtime_read_contract(value, (read_baseline or expected)[key], fixture_case=key)
        return result, metrics

    cases.invoke = recorded
    if ARM == 'reference':
        return
    import dashboard_cpu_budget as cpu
    import dashboard_v2_routes as dash
    import dashboard_b_computation_experiment as computation
    import orders_db
    if ARM == 'runtime':
        import payment_method_fee_status
    else:
        payment_method_fee_status = None
    from order_currency import summarize_orders_sar
    from test_dashboard_b_baseline_mongo import legacy_handler

    @contextmanager
    def configured(_reference_budget, hook=None):
        # All four repetitions use the FIXED actual runtime budgets. The old
        # fixture driver also exercises its synchronous independent-writer fence.
        original_index = dash.index_current_catalog_products_cooperative
        original_ads = dash.build_salla_ads_executive_breakdown_cooperative
        original_cost = dash.build_mezan_v2_product_cost_cooperative
        original_catalog_loader = (getattr(dash, '_load_cohort_catalog_products', None)
                                   if ARM == 'runtime' else None)
        if ARM == 'runtime':
            assert original_catalog_loader is not None, 'Runtime catalog instrumentation is mandatory'
        original_traffic = getattr(dash, 'load_product_traffic_sources', None)
        original_google = getattr(dash, 'load_google_dashboard_spend', None)
        original_fee_setup = (payment_method_fee_status.load_fee_setup_context
                              if payment_method_fee_status is not None else None)

        class ForcedBudget(cpu.CPUWorkBudget):
            async def checkpoint(self):
                calls['forced_index_checkpoints'] += 1
                await hook()
                self.cpu = time.thread_time() - self.seconds - 1
                await super().checkpoint()

        async def index(products):
            calls['index'] += 1
            if hook is None or original_catalog_loader is not None:
                return await original_index(products)
            with patch.object(cpu, 'CPUWorkBudget', ForcedBudget):
                return await original_index(products)

        async def ads(*args, **kwargs):
            calls['advertising'] += 1
            return await original_ads(*args, **kwargs)

        async def cost(*args, **kwargs):
            calls['cost'] += 1
            audit = active_authority_audit.get()
            if audit is not None:
                user_id = args[1] if len(args) > 1 else kwargs['user_id']
                orders = args[2] if len(args) > 2 else kwargs['orders']
                audit['cohorts'].append(_cohort_evidence(user_id, orders))
                audit['catalog']['cohorts'].append(_catalog_cohort(user_id, orders))
            token = active_cost_audit.set(audit)
            try:
                return await original_cost(*args, **kwargs)
            finally:
                active_cost_audit.reset(token)

        async def catalog_loader(db, user_id, orders):
            audit = active_authority_audit.get()
            assert audit is not None, 'Missing catalog audit in runtime fixture'
            audit['catalog']['loads'].append(_catalog_cohort(user_id, orders))

            async def load():
                token = active_catalog_audit.set(audit)
                try:
                    return await original_catalog_loader(_CatalogReadDB(db, audit['catalog']), user_id, orders)
                finally:
                    active_catalog_audit.reset(token)

            async def after_capture():
                await ForcedBudget(cpu.PRODUCT_INDEX_BUDGET_MS).checkpoint()

            return await _catalog_capture_fence(
                load, catalog_module=product_catalog_cost_resolution,
                real_enrich=dash.enrich_current_salla_cost,
                after_capture=after_capture if hook is not None else None,
            )

        async def parser(orders):
            calls['parser'] += 1
            return await orders_db.orders_to_parsed_cooperative(orders)

        async def traffic(db, user_id, orders, **kwargs):
            audit = active_authority_audit.get()
            if audit is None:
                return await original_traffic(db, user_id, orders, **kwargs)
            audit['traffic_cohorts'].append(_traffic_cohort(user_id, orders))
            token = active_traffic_audit.set(audit)
            try:
                return await original_traffic(db, user_id, orders, **kwargs)
            finally:
                active_traffic_audit.reset(token)

        async def google(db, user_id, *, date_from, date_to, **kwargs):
            audit = active_authority_audit.get()
            if audit is None:
                return await original_google(db, user_id, date_from=date_from, date_to=date_to, **kwargs)
            audit['google_cohorts'].append({'user_id': str(user_id),
                                            'date_from': str(date_from), 'date_to': str(date_to)})
            token = active_google_audit.set(audit)
            try:
                return await original_google(db, user_id, date_from=date_from, date_to=date_to, **kwargs)
            finally:
                active_google_audit.reset(token)

        async def fee_setup(db, owner):
            audit = active_authority_audit.get()
            if audit is None:
                return await original_fee_setup(db, owner)
            audit['payment_fee_cohorts'].append({'user_id': str(owner)})
            token = active_payment_fee_audit.set(audit)
            try:
                return await original_fee_setup(db, owner)
            finally:
                active_payment_fee_audit.reset(token)

        @contextmanager
        def actual(db, scope):
            handler = legacy_handler(db)
            handler.__globals__.update(
                summarize_orders_sar=summarize_orders_sar,
                orders_to_parsed_cooperative=parser,
                _dashboard_deepcopy=copy.deepcopy,
            )
            with (_runtime_legacy_adapter(handler, parser) if ARM == 'runtime' else nullcontext()):
                yield dash.make_dashboard_v2_router, handler

        with patch.object(computation, 'candidate', actual), \
             patch.object(dash, 'index_current_catalog_products_cooperative', index), \
             patch.object(dash, 'build_salla_ads_executive_breakdown_cooperative', ads), \
             patch.object(dash, 'build_mezan_v2_product_cost_cooperative', cost), \
             (patch.object(dash, '_load_cohort_catalog_products', catalog_loader)
              if original_catalog_loader is not None else nullcontext()), \
             patch.object(dash, 'load_product_traffic_sources', traffic, create=True), \
             patch.object(dash, 'load_google_dashboard_spend', google, create=True), \
             (patch.object(payment_method_fee_status, 'load_fee_setup_context', fee_setup)
              if payment_method_fee_status is not None else nullcontext()):
            yield

    cases.configured = configured


def pytest_sessionfinish(session, exitstatus):
    output = Path(os.environ['DASHBOARD_ADOPTION_OUTPUT'])
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    if exitstatus == 0:
        assert len(records) == 164, len(records)
        if ARM != 'reference':
            assert set(records) == set(json.loads(GOLDEN.read_text()))
            assert all(calls[name] > 0 for name in ('parser', 'index', 'cost', 'advertising', 'forced_index_checkpoints'))
            output.with_suffix('.calls.json').write_text(json.dumps(calls, indent=2))
