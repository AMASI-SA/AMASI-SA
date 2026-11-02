"""Compare actual runtime with the immutable #1301 experiment in two processes.

This plugin uses the frozen reference's fixture/mutation drivers, not its AST
builders for the runtime arm. No source transformations of runtime are made.
Only server startup/decorators are excluded by the existing handler loader.
"""
import asyncio
import copy
import json
import os
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import patch

ARM = os.environ.get('DASHBOARD_ADOPTION_ARM', '')
GOLDEN = Path(os.environ.get('DASHBOARD_ADOPTION_GOLDEN', 'reference-responses.json'))
records = {}
calls = Counter()
current_test = None
sequence = 0
active_authority_audit = ContextVar('dashboard_authority_audit', default=None)

# This is an explicit read-contract extension for MZ2 historical cost authority,
# not a new JSON oracle. The immutable fixtures contain no historical cost rows.
# Keep these names/shapes independent of production constants so an accidental
# collection, scope, pipeline or batching change fails the gate.
AUTHORITY_COLLECTIONS = frozenset({
    'mezan_bundle_definitions_v2', 'mezan_order_item_cost_snapshots_v2',
    'mezan_component_order_lifecycle_v1', 'mezan_component_consumption_plans_v1',
    'mezan_component_consumption_units_v1', 'mezan_order_cost_corrections_v1',
})
AUTHORITY_BATCH_SIZE = 250


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
                         'filter': query, 'projection': projection, 'limit': None,
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
    return expected


def assert_runtime_read_contract(runtime, baseline):
    """Subtract only individually audited empty authority reads from raw data."""
    assert set(runtime) == set(baseline) | {'cost_authority_audit'}
    assert runtime['json'] == baseline['json'], 'Immutable JSON response changed'
    audit = runtime['cost_authority_audit']
    expected = _expected_authority_commands(audit)
    assert audit['commands'] == expected, 'Unexpected authority query, scope, operation or batch count'
    response = json.loads(runtime['json'])
    assert len(audit['cohorts']) == (0 if 'http_error' in response else 1)
    assert not (set(baseline['collections']) & AUTHORITY_COLLECTIONS)
    for collection in AUTHORITY_COLLECTIONS:
        assert runtime['collections'].get(collection, 0) == 0, 'Legacy fixture unexpectedly read historical cost rows'
    observed_collections = set(runtime['collections']) & AUTHORITY_COLLECTIONS
    assert observed_collections == {row['collection'] for row in expected}
    normalized = {key: copy.deepcopy(value) for key, value in runtime.items()
                  if key != 'cost_authority_audit'}
    for operation, count in Counter(row['operation'] for row in expected).items():
        assert normalized['commands'].get(operation, 0) >= count
        normalized['commands'][operation] -= count
        if normalized['commands'][operation] == 0:
            del normalized['commands'][operation]
    normalized['collections'] = {key: value for key, value in normalized['collections'].items()
                                 if key not in AUTHORITY_COLLECTIONS}
    assert normalized == baseline, 'Existing Mongo commands or document counts changed'


def pytest_runtest_setup(item):
    global current_test, sequence
    current_test, sequence = item.nodeid, 0


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
                     'database': _fixture_database_name(args[0]), 'cohorts': [], 'commands': []}
            commands = args[1]
            original_started = commands.started

            def started(event):
                original_started(event)
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
            # Keep the immutable historical JSON contract. Later approved
            # production changes may add reads without changing the response.
            assert value['json'] == expected[key]['json'], key
            if ARM == 'runtime':
                assert_runtime_read_contract(value, (read_baseline or expected)[key])
        return result, metrics

    cases.invoke = recorded
    if ARM == 'reference':
        return
    import dashboard_cpu_budget as cpu
    import dashboard_v2_routes as dash
    import dashboard_b_computation_experiment as computation
    import orders_db
    from test_dashboard_b_baseline_mongo import legacy_handler

    @contextmanager
    def configured(_reference_budget, hook=None):
        # All four repetitions use the FIXED actual runtime budgets. The old
        # fixture driver also exercises its synchronous independent-writer fence.
        original_index = dash.index_current_catalog_products_cooperative
        original_ads = dash.build_salla_ads_executive_breakdown_cooperative
        original_cost = dash.build_mezan_v2_product_cost_cooperative

        class ForcedBudget(cpu.CPUWorkBudget):
            async def checkpoint(self):
                calls['forced_index_checkpoints'] += 1
                await hook()
                self.cpu = time.thread_time() - self.seconds - 1
                await super().checkpoint()

        async def index(products):
            calls['index'] += 1
            if hook is None:
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
            return await original_cost(*args, **kwargs)

        async def parser(orders):
            calls['parser'] += 1
            return await orders_db.orders_to_parsed_cooperative(orders)

        @contextmanager
        def actual(db, scope):
            handler = legacy_handler(db)
            handler.__globals__.update(
                orders_to_parsed_cooperative=parser,
                _dashboard_deepcopy=copy.deepcopy,
            )
            yield dash.make_dashboard_v2_router, handler

        with patch.object(computation, 'candidate', actual), \
             patch.object(dash, 'index_current_catalog_products_cooperative', index), \
             patch.object(dash, 'build_salla_ads_executive_breakdown_cooperative', ads), \
             patch.object(dash, 'build_mezan_v2_product_cost_cooperative', cost):
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
