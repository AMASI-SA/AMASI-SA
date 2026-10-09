"""Synthetic ASGI + real Mongo benchmark; no provider networking or runtime edits.

The measured HTTP transport is in-process ASGI, not public-network latency.
Seed/restore/index creation are excluded. All raw timings include transaction retries.
"""
import argparse
import asyncio
import contextvars
import copy
import hashlib
import json
import math
import os
import random
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'backend/tests')]

from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring
import preparation_piece_operations as ops
import order_review_routes as review
import test_g47_component_lifecycle_integration as fixtures
import stock_component_consumption_service as components
from benchmarks.shipping_delivered.safety import design_adapter
from benchmarks.shipping_delivered.isolation import localhost_only

ACTOR = contextvars.ContextVar('benchmark_actor', default='owner')
REQUEST = contextvars.ContextVar('benchmark_request', default=None)


def percentiles(values):
    values = sorted(values)
    return {f'p{p}': values[max(0, math.ceil(len(values) * p / 100) - 1)]
            if values else None for p in (50, 95, 99)}


class Commands(monitoring.CommandListener):
    """Store metadata only; command bodies, provider records and credentials excluded."""
    def __init__(self):
        self.enabled = False
        self.rows = []
        self.pending = {}
        self.lock = threading.Lock()

    def started(self, event):
        if self.enabled:
            with self.lock:
                command = event.command
                collection = command.get(event.command_name)
                if not isinstance(collection, str):
                    collection = command.get('collection')
                transaction_key = None
                if command.get('autocommit') is False and 'txnNumber' in command:
                    session = hashlib.sha256(str(command.get('lsid')).encode()).hexdigest()[:16]
                    transaction_key = f'{session}:{command["txnNumber"]}'
                self.pending[event.request_id] = {
                    'request': REQUEST.get(), 'command': event.command_name,
                    'collection': collection, 'transaction_key': transaction_key,
                    'start_transaction': bool(command.get('startTransaction')),
                    'started_monotonic': time.monotonic(),
                }

    def done(self, event, failed):
        with self.lock:
            row = self.pending.pop(event.request_id, None)
            if row is not None:
                failure = event.failure if failed else event.reply
                code = failure.get('code') if isinstance(failure, dict) else None
                if code is None and isinstance(failure, dict) and failure.get('writeErrors'):
                    code = failure['writeErrors'][0].get('code')
                row.update(duration_ms=event.duration_micros / 1000, failed=failed,
                           code=code, ended_monotonic=time.monotonic())
                self.rows.append(row)

    def succeeded(self, event):
        self.done(event, False)

    def failed(self, event):
        self.done(event, True)


def command_metrics(commands):
    """Measured wire-command attempts, including failures; no estimated roundtrips."""
    by_transaction = {}
    for row in commands:
        if row['transaction_key']:
            by_transaction.setdefault(row['transaction_key'], []).append(row)
    attempts = []
    for key, rows in by_transaction.items():
        rows.sort(key=lambda r: r['started_monotonic'])
        terminals = [r for r in rows if r['command'] in {'commitTransaction', 'abortTransaction'}]
        last = terminals[-1] if terminals else None
        attempts.append({
            'transaction_key': key, 'request': rows[0]['request'],
            'observed_duration_ms': (max(r['ended_monotonic'] for r in rows) - rows[0]['started_monotonic']) * 1000,
            'mongo_roundtrips': len(rows), 'start_observed': any(r['start_transaction'] for r in rows),
            'terminal': last['command'] if last else 'not_observed',
            'terminal_success': bool(last and not last['failed'] and last['code'] is None),
            'write_conflicts': sum(r['code'] == 112 for r in rows),
        })
    def category(collection):
        if collection == 'mz2_atomic_owners':
            return 'owner'
        if collection == 'unified_orders':
            return 'canonical'
        if collection in {fixtures.LOCATIONS, components.PLANS, components.UNITS,
                          'warehouse_location_events', 'mezan_inventory_receipts_v2'}:
            return 'stock'
        return 'other_or_unknown'
    conflicts = Counter(category(r['collection']) for r in commands if r['code'] == 112)
    return attempts, {key: conflicts[key] for key in ('owner', 'canonical', 'stock', 'other_or_unknown')}


async def dump(db):
    return {name: await db[name].find({}).to_list(None)
            for name in await db.list_collection_names()}


async def restore(db, rows):
    # Diagnose a malformed fixture before changing any disposable database data.
    for name, docs in rows.items():
        identities = [str(doc['_id']) for doc in docs if '_id' in doc]
        duplicates = [identity for identity, count in Counter(identities).items() if count > 1]
        if duplicates:
            raise AssertionError({'fixture_collection': name, 'duplicate_ids': duplicates[:5]})
    for name in await db.list_collection_names():
        await db[name].delete_many({})
    for name, docs in rows.items():
        if docs:
            try:
                await db[name].insert_many(copy.deepcopy(docs))
            except Exception as exc:
                raise AssertionError({'fixture_collection': name, 'documents': len(docs),
                                      'error': str(exc)}) from exc


def clone(template, index, merchant):
    """Clone all fixture identities and their references, keeping business values.

    Each order owns synthetic component stock. This avoids introducing a second
    shared-stock hotspot; merchant transaction ownership is still shared.
    """
    identifiers = {'owner': merchant, 'order-1': f'order-{index}',
                   'piece-1': f'piece-{index}-1', 'piece-2': f'piece-{index}-2'}

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key == 'id' or key.endswith('_id')) and isinstance(item, str) and item:
                    identifiers.setdefault(item, f'{item}-case{index}')
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(template)
    # These identities are regenerated by runtime, not arbitrary fixture keys.
    for plan in template.get(components.PLANS, []):
        identifiers[plan['_id']] = components._id('component_plan', merchant, f'order-{index}')
    for unit in template.get(components.UNITS, []):
        identifiers[unit['_id']] = components._id(
            'component_unit', merchant, f'order-{index}',
            identifiers.get(unit['order_line_id'], unit['order_line_id']), unit['unit_index'])

    def translate(value):
        if isinstance(value, ObjectId):
            return ObjectId()
        if isinstance(value, str):
            return identifiers.get(value, value)
        if isinstance(value, dict):
            return {key: translate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [translate(item) for item in value]
        return value
    result = translate(template)
    for plan in result.get(components.PLANS, []):
        plan['input_hash'] = components._digest({
            'lines': plan['lines'], 'warehouse_ids': plan['warehouse_ids']})
    # Preserve authentic consumption proof identities, including embedded refs.
    proof_ids = {unit['consumption_id']: components._id(
        'component_consumption', unit['_id'], unit['resource_demands'])
        for unit in result.get(components.UNITS, [])}
    def proofs(value):
        if isinstance(value, str):
            return proof_ids.get(value, value)
        if isinstance(value, list):
            return [proofs(item) for item in value]
        if isinstance(value, dict):
            return {key: proofs(item) for key, item in value.items()}
        return value
    result = proofs(result)
    for doc in result.get('mz2_atomic_owners', []):
        doc['_id'] = merchant
    return result


async def fixture_template(case, virtual):
    await restore(case.db, case.empty)
    ACTOR.set('owner')
    response, _ = await case.accept()
    assert response.status_code == 200, response.text
    payload = case.source_payload(number='order-1', status='in_progress')
    await case.db.unified_orders.update_one({'order_number': 'order-1'}, {'$set': {
        'order_status': 'in_progress', 'order_date': fixtures.WHEN,
        'raw_by_source.salla_direct': payload}})
    await case.seed_physical()
    if virtual:
        await case.db[ops.PIECES].delete_many({})
        # Two operational pieces preserve an incomplete order: no label hook.
        await case.db[ops.WORKFLOWS].update_one({'order_number': 'order-1'}, {'$set': {
            'operational_items': [
                {'operational_item_id': 'piece-1', 'name': 'Synthetic note',
                 'assembly_status': 'pending', 'blocks_order_completion': True},
                {'operational_item_id': 'piece-2', 'name': 'Synthetic second note',
                 'assembly_status': 'pending', 'blocks_order_completion': True}]}})
    return await dump(case.db)


def merged_fixture(template, count, topology):
    result = {}
    # These belong to the merchant, not to any individual cloned order.
    merchant_wide = {'mz2_atomic_owners', 'order_review_acceptance_config_versions', 'settings'}
    for index in range(count):
        merchant = f'merchant-{index}' if topology == 'different' else 'merchant-0'
        for name, docs in clone(template, index, merchant).items():
            if name in merchant_wide and topology == 'same' and index:
                continue
            result.setdefault(name, []).extend(docs)
    return result


async def run_batch(case, meter, design, topology, kind, concurrency, order_count,
                    trial, template, raw_file):
    # A cancelled Motor await may leave a worker-thread command completing.
    # Never reuse or delete its database for a later cell.
    observer = case.mongo
    index_specs = case.index_specs
    appname = 'shipping-benchmark-' + uuid4().hex
    mongo = AsyncIOMotorClient(os.environ['MZ2_TEST_MONGO_URI'],
                              event_listeners=[meter], maxPoolSize=300, appname=appname)
    db = mongo['shipping_benchmark_cell_' + uuid4().hex]
    app = FastAPI()
    async def actor():
        return {'id': ACTOR.get(), 'role': 'owner', 'name': 'Synthetic operator'}
    app.include_router(ops.make_preparation_piece_operations_router(db, actor))
    client = AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                         base_url='http://synthetic')
    case = SimpleNamespace(db=db, client=client)
    await restore(db, merged_fixture(template, order_count, topology))
    for name, indexes in index_specs.items():
        for index_name, specification in indexes.items():
            if index_name != '_id_':
                options = {key: value for key, value in specification.items()
                           if key not in {'v', 'ns', 'key', 'name'}}
                await db[name].create_index(specification['key'], name=index_name, **options)
    await ops.ensure_piece_operation_indexes(db)
    gate = asyncio.Event()
    released = {}
    records = []
    meter.rows.clear()
    batch = f'{design}:{topology}:{kind}:c{concurrency}:o{order_count}:t{trial}'

    async def timed(operation, index, writer):
        created = time.monotonic()
        await gate.wait()
        started = time.monotonic()
        order_index = index % order_count
        merchant = f'merchant-{order_index}' if topology == 'different' else 'merchant-0'
        token = ACTOR.set(merchant)
        req_token = REQUEST.set(f'{batch}:{operation}:{index}')
        row = {'batch': batch, 'operation': operation, 'index': index,
               'order_index': order_index, 'created_monotonic': created,
               'started_monotonic': started,
               'idempotent': False, 'timed_out': False, 'response_valid': True,
               'eventloop_admission_ms': (started-released['at'])*1000,
               'barrier_wait_ms': (released['at']-created)*1000}
        try:
            if operation == 'interactive':
                response = await asyncio.wait_for(case.client.post(
                    f'/preparation-work-v1/assembly/pieces/piece-{order_index}-1/ready',
                    json={'client_request_id': f'bench-{batch}-{index}'}), timeout=90)
                row['status'] = response.status_code
                try:
                    body = response.json()
                except ValueError:
                    body = {'non_json_body': response.text[:2000]}
                row['idempotent'] = body.get('idempotent', False)
                row['response_valid'] = response.status_code != 200 or body.get('ok') is True
                if response.status_code != 200:
                    row['error'] = body
            else:
                # Persistence segment of status polling/reconciliation/enrichment.
                # Real provider HTTP and worker scheduling are deliberately absent.
                async def apply(db):
                    field = {'polling': 'order_status',
                             'reconciliation': 'raw_by_source.salla_direct.status',
                             'enrichment': 'benchmark_enrichment'}[operation]
                    value = {'polling': 'in_progress',
                             'reconciliation': {'slug': 'in_progress', 'name': 'in_progress'},
                             'enrichment': index}[operation]
                    return await db.unified_orders.update_one(
                        {'user_id': merchant, 'order_number': f'order-{order_index}'},
                        {'$set': {field: value}, '$inc': {'benchmark_provider_write': 1}})
                result = await asyncio.wait_for(writer(case.db, merchant, apply), timeout=90)
                assert result.matched_count == 1
                row['status'] = 200
        except Exception as exc:
            row.update(status=row.get('status', 0), error=type(exc).__name__ + ': ' + str(exc),
                       timed_out=isinstance(exc, TimeoutError))
        finally:
            row['ended_monotonic'] = time.monotonic()
            row['latency_ms'] = (row['ended_monotonic'] - started)*1000
            records.append(row)
            REQUEST.reset(req_token)
            ACTOR.reset(token)

    async with design_adapter(design) as writer:
        tasks = [asyncio.create_task(timed('interactive', i, writer)) for i in range(concurrency)]
        tasks += [asyncio.create_task(timed(('polling', 'reconciliation', 'enrichment')[i % 3], i, writer))
                  for i in range(concurrency)]
        # All tasks reach barrier before measured burst begins.
        await asyncio.sleep(0)
        started = time.monotonic()
        cpu_started = time.process_time()
        meter.enabled = True
        released['at'] = time.monotonic()
        gate.set()
        await asyncio.gather(*tasks)
        meter.enabled = False
        ended = time.monotonic()
        cpu_seconds = time.process_time() - cpu_started
    await client.aclose()
    mongo.close()
    # Observe from an independent client after closing all cell sessions/sockets.
    # Require two empty samples, including idle transactions, before continuing.
    quiet_samples, observations = 0, []
    quiet_started = time.monotonic()
    while time.monotonic() - quiet_started < 10:
        active = await observer.admin.aggregate([
            {'$currentOp': {'allUsers': True, 'idleSessions': True}},
            {'$match': {'clientMetadata.application.name': appname}},
            {'$match': {'$or': [
                {'transaction': {'$exists': True}},
                {'command.hello': {'$exists': False}, 'command.ismaster': {'$exists': False}}
            ]}},
        ]).to_list(None)
        observations.append({'at': time.monotonic(), 'operations': len(active)})
        quiet_samples = quiet_samples + 1 if not active else 0
        if quiet_samples >= 2:
            break
        await asyncio.sleep(0.1)
    quiescent = quiet_samples >= 2
    commands = [r for r in meter.rows if (r['request'] or '').startswith(batch + ':')]
    # Include missing request context rather than silently discarding it.
    commands += [r for r in meter.rows if r['request'] is None]
    outstanding_telemetry = [r for r in meter.pending.values()
                             if (r['request'] or '').startswith(batch + ':')]
    # Read committed state through the observer; never mutate the retained cell DB.
    state_db = observer[db.name]
    transactions = Counter(row['request'] for row in commands if row['start_transaction'])
    transaction_rows, conflict_categories = command_metrics(commands)
    for row in records:
        request_id = f'{batch}:{row["operation"]}:{row["index"]}'
        request_commands = [r for r in commands if r['request'] == request_id]
        request_transactions = [r for r in transaction_rows if r['request'] == request_id]
        row['mongo_roundtrips'] = len(request_commands)
        row['mongo_command_duration_sum_ms'] = sum(r['duration_ms'] for r in request_commands)
        row['transaction_attempts_observed'] = request_transactions
    command_rows = [{'batch': batch, 'record_type': 'mongo_command', **row} for row in commands]
    interactive = [r for r in records if r['operation'] == 'interactive']
    sync = [r for r in records if r['operation'] != 'interactive']
    errors = [r for r in records if r['status'] != 200 or not r['response_valid']]
    # Every distinct order must actually transition once; duplicates are separate.
    successful_transitions = sum(r['status'] == 200 and r['response_valid'] and not r['idempotent']
                                 for r in interactive)
    violations, persisted = [], []
    if successful_transitions != order_count:
        violations.append({'expected_transitions': order_count, 'acknowledged': successful_transitions})
    if not quiescent:
        violations.append({'quiescence_not_proven': observations})
    if outstanding_telemetry:
        violations.append({'mongo_telemetry_incomplete': len(outstanding_telemetry)})
    if any(r['request'] is None for r in commands):
        violations.append({'request_context_missing': sum(r['request'] is None for r in commands)})
    for index in range(order_count):
        merchant = f'merchant-{index}' if topology == 'different' else 'merchant-0'
        selector = {'user_id': merchant, 'order_number': f'order-{index}'}
        if kind == 'physical':
            piece = await state_db[ops.PIECES].find_one({**selector, 'piece_id': f'piece-{index}-1'})
            transitioned = bool(piece and piece.get('assembly_status') == 'ready')
            consumed = await state_db[fixtures.UNITS].count_documents({
                'user_id': merchant, 'order_id': f'order-{index}', 'state': 'consumed'})
            location = await state_db[fixtures.LOCATIONS].find_one({
                'user_id': merchant, 'id': f'materials-case{index}'})
            stock = location['occupancy']['items'][0]['quantity'] if location else None
            if consumed != int(transitioned) or stock != 20 - 2 * int(transitioned):
                violations.append({'order': index, 'partial_side_effects': True,
                                   'ready': transitioned, 'consumed': consumed, 'stock': stock})
        else:
            workflow = await state_db[ops.WORKFLOWS].find_one(selector)
            piece = next((r for r in (workflow or {}).get('operational_items', [])
                          if r.get('operational_item_id') == f'piece-{index}-1'), None)
            transitioned = bool(piece and piece.get('assembly_status') == 'ready')
            consumed = await state_db[fixtures.UNITS].count_documents({
                'user_id': merchant, 'order_id': f'order-{index}', 'state': 'consumed'})
            stock = None
            if consumed:
                violations.append({'order': index, 'virtual_consumption': consumed})
        observed = {'order_index': index, 'ready': transitioned, 'consumed': consumed, 'stock': stock}
        persisted.append(observed)
        successes = [r for r in interactive if r['order_index'] == index and r['status'] == 200]
        if successes and not transitioned:
            violations.append({'order': index, 'acknowledged_but_not_persisted': True})
        for row in (r for r in interactive if r['order_index'] == index and r['status'] != 200):
            row['post_quiescence_order_state'] = observed
            row['commit_uncertainty'] = (
                'order_committed_but_request_attribution_unknown' if transitioned
                else 'no_committed_order_transition_observed')
    for row in records + command_rows + [
            {'batch': batch, 'record_type': 'transaction_attempt', **r} for r in transaction_rows]:
        raw_file.write(json.dumps(row, default=str) + '\n')
    raw_file.write(json.dumps({'batch': batch, 'record_type': 'persisted_state',
                               'orders': persisted, 'quiescence': observations}) + '\n')
    raw_file.flush()
    success_count = sum(r['status'] == 200 and r['response_valid'] for r in records)
    persisted_counters = {
        'piece_events': await state_db[ops.PIECE_EVENTS].count_documents({}),
        'shipping_batches': await state_db[ops.SHIPPING_BATCHES].count_documents({}),
        'consumed_units': await state_db[fixtures.UNITS].count_documents({'state': 'consumed'}),
        'ready_physical_pieces': await state_db[ops.PIECES].count_documents({'assembly_status': 'ready'}),
    }
    if persisted_counters['shipping_batches']:
        violations.append({'unexpected_shipping_batches': persisted_counters['shipping_batches']})
    return {
        'batch': batch, 'design': design, 'topology': topology, 'kind': kind,
        'concurrency': concurrency, 'orders': order_count, 'trial': trial,
        'merchants': order_count if topology == 'different' else 1,
        'started_monotonic': started, 'ended_monotonic': ended,
        'wall_seconds': ended-started, 'client_cpu_seconds': cpu_seconds,
        'interactive_ms': percentiles([r['latency_ms'] for r in interactive]),
        'sync_ms': percentiles([r['latency_ms'] for r in sync]),
        'eventloop_admission_ms': percentiles([r['eventloop_admission_ms'] for r in records]),
        'mongo_command_ms': percentiles([r['duration_ms'] for r in commands]),
        'interactive_mongo_roundtrips': percentiles([r['mongo_roundtrips'] for r in interactive]),
        'sync_mongo_roundtrips': percentiles([r['mongo_roundtrips'] for r in sync]),
        'transaction_observed_ms': percentiles([r['observed_duration_ms'] for r in transaction_rows]),
        'transaction_terminal_not_observed': sum(r['terminal'] == 'not_observed' for r in transaction_rows),
        'interactive_throughput_rps': sum(r['status'] == 200 and r['response_valid'] for r in interactive)/(ended-started),
        'sync_throughput_rps': sum(r['status'] == 200 for r in sync)/(ended-started),
        'successful_transition_throughput_rps': successful_transitions/(ended-started),
        'combined_throughput_rps': success_count/(ended-started),
        'attempted_throughput_rps': len(records)/(ended-started),
        'successful_transitions': successful_transitions,
        'idempotent_responses': sum(r['idempotent'] for r in interactive),
        'mongo_commands': len(commands),
        'transaction_attempts': sum(transactions.values()),
        'transaction_retries': (sum(max(0, n-1) for n in transactions.values())
                                if None not in transactions else None),
        'transaction_conflicts': sum(r['code'] == 112 for r in commands),
        'transaction_conflicts_by_collection': conflict_categories,
        'request_context_missing': sum(r['request'] is None for r in commands),
        'errors': errors,
        'timeouts': sum(r['timed_out'] for r in records),
        'violations': violations, 'persisted_orders': persisted,
        'persisted_counters': persisted_counters,
        'quiescent': quiescent, 'quiescence_observations': observations,
        'retained_database': db.name,
        'pass': not errors and not violations,
    }


async def polling_controls(case, meter, designs):
    """Small noncontended persistence controls, not full Salla polling benchmarks."""
    db = case.mongo['shipping_poll_control_' + uuid4().hex]
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': 'control',
                                       'order_status': 'in_progress'})
    results = []
    for design in designs:
        async with design_adapter(design) as writer:
            for mode in ('unchanged', 'changed'):
                await db.unified_orders.update_one({'order_number': 'control'},
                                                  {'$set': {'order_status': 'in_progress'}})
                records = []
                cpu_started = time.process_time()
                for index in range(20):
                    value = 'processing' if mode == 'changed' and index % 2 == 0 else 'in_progress'
                    async def apply(scoped):
                        return await scoped.unified_orders.update_one(
                            {'user_id': 'owner', 'order_number': 'control'}, {'$set': {'order_status': value}})
                    meter.rows.clear()
                    token = REQUEST.set(f'poll-control:{design}:{mode}:{index}')
                    meter.enabled = True
                    started = time.monotonic()
                    try:
                        result = await asyncio.wait_for(writer(db, 'owner', apply), timeout=90)
                        row = {'index': index, 'latency_ms': (time.monotonic()-started)*1000,
                               'matched': result.matched_count, 'modified': result.modified_count,
                               'error': None}
                    except Exception as exc:
                        row = {'index': index, 'latency_ms': (time.monotonic()-started)*1000,
                               'error': type(exc).__name__ + ': ' + str(exc)}
                    finally:
                        meter.enabled = False
                        REQUEST.reset(token)
                    row['mongo_commands'] = copy.deepcopy(meter.rows)
                    row['mongo_roundtrips'] = len(meter.rows)
                    records.append(row)
                    if row['error']:
                        return {'results': results + [{'design': design, 'mode': mode,
                                                       'records': records, 'pass': False}],
                                'pass': False, 'stop_before_matrix': True,
                                'reason': 'Control errored; no further work starts against possibly active commands.'}
                errors = [r for r in records if r['error'] or r.get('matched') != 1
                          or r.get('modified') != int(mode == 'changed')]
                results.append({'design': design, 'mode': mode, 'records': records,
                                'latency_ms': percentiles([r['latency_ms'] for r in records]),
                                'mongo_roundtrips': percentiles([r['mongo_roundtrips'] for r in records]),
                                'client_cpu_seconds': time.process_time()-cpu_started,
                                'pass': not errors, 'errors': errors})
    return {'results': results, 'pass': all(r['pass'] for r in results),
            'scope': 'Sequential local Mongo persistence only. No HTTP, provider fetch, or mark-ready contention.',
            'unchanged_semantics': 'Actual no-op $set, without forced revision/inc.',
            'changed_semantics': 'Alternating in_progress/processing $set, without forced revision/inc.'}


async def main(args):
    localhost_only()
    assert os.environ['MZ2_TEST_MONGO_URI'] == 'mongodb://127.0.0.1:27018/?replicaSet=shippingbenchmark'
    meter = Commands()
    original_motor = AsyncIOMotorClient
    def mongo(*a, **kw):
        return original_motor(*a, event_listeners=[meter], maxPoolSize=300, **kw)
    case = fixtures.ComponentRouteTests()
    with patch.object(fixtures, 'AsyncIOMotorClient', mongo):
        await case.asyncSetUp()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        hello = await case.db.command('hello')
        assert hello['setName'] == 'shippingbenchmark' and hello['isWritablePrimary']
        case.empty = await dump(case.db)
        await case.client.aclose()
        app = FastAPI()
        async def actor():
            return {'id': ACTOR.get(), 'role': 'owner', 'name': 'Synthetic operator'}
        async def context(*unused, **kwargs):
            return {**case.context, 'merchant_id': ACTOR.get(), 'actor_id': ACTOR.get()}
        app.include_router(review.make_order_review_router(case.db, actor))
        app.include_router(ops.make_preparation_piece_operations_router(case.db, actor))
        case.client = AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://synthetic')
        with patch.object(ops, '_actor_context', context), \
             patch.object(ops, 'call_salla', AsyncMock(side_effect=AssertionError('provider networking forbidden'))), \
             patch.object(ops, 'sync_completed_carrier_label', AsyncMock(side_effect=AssertionError('shipping forbidden'))):
            templates = {kind: await fixture_template(case, kind == 'virtual') for kind in ('physical', 'virtual')}
            await ops.ensure_piece_operation_indexes(case.db)
            case.index_specs = {name: await case.db[name].index_information()
                                for name in await case.db.list_collection_names()}
            scenarios = [(1, 1), (10, 1), (10, 10), (50, 1), (50, 50), (100, 1), (100, 100)]
            trials = [args.trial] if args.trial is not None else range(args.trials)
            designs = ('baseline', 'canonical', 'conditional')
            control_results = await polling_controls(case, meter, designs)
            output.with_suffix('.polling-controls.json').write_text(json.dumps(control_results, indent=2))
            if control_results.get('stop_before_matrix'):
                output.write_text(json.dumps({'completed': False, 'benchmark_pass': False,
                                              'polling_control_error': control_results}, indent=2))
                return 1
            cells = [(topology, kind, c, n) for c, n in scenarios
                     for topology in ('same', 'different') for kind in ('physical', 'virtual')
                     if not (topology == 'different' and n == 1)]
            random.Random(1300).shuffle(cells)
            jobs = []
            # Adjacent matched cells with Latin-square rotation over the trials:
            # each design occupies every position, instead of all D runs last.
            for trial in trials:
                for position, (topology, kind, c, n) in enumerate(cells):
                    offset = (position + trial) % len(designs)
                    for design in designs[offset:] + designs[:offset]:
                        jobs.append((design, topology, kind, c, n, trial))
            # Small actual-route smoke must succeed before the full matrix.
            if args.smoke:
                jobs = [(d, t, k, 2, 2, 0) for d in designs
                        for t in ('same', 'different') for k in ('physical', 'virtual')]
            results = []
            with output.with_suffix('.jsonl').open('w') as raw:
                # Identical unreported warm-up matrix before measured trials.
                # Rows retained separately for audit, never pooled into results.
                with output.with_suffix('.warmup.jsonl').open('w') as warmup:
                    for design in designs:
                        for kind in ('physical', 'virtual'):
                            row = await run_batch(case, meter, design, 'same', kind, 1, 1,
                                                  -1, templates[kind], warmup)
                            assert row['pass'], ('warmup_failed', row)
                for job in jobs:
                    row = await run_batch(case, meter, *job, templates[job[2]], raw)
                    results.append(row)
                    output.write_text(json.dumps({'results': results, 'completed': False}, indent=2))
                    print(json.dumps({k: row[k] for k in ('batch', 'interactive_ms', 'sync_ms', 'transaction_retries')}), flush=True)
                    if not row['quiescent']:
                        # Another cell would have contaminated resource timing.
                        break
            benchmark_pass = (len(results) == len(jobs) and all(row['pass'] for row in results)
                              and control_results['pass'])
            output.write_text(json.dumps({
                'completed': len(results) == len(jobs), 'benchmark_pass': benchmark_pass,
                'planned_cells': len(jobs), 'results': results, 'production_writes': 0,
                'polling_controls_pass': control_results['pass'],
                'motor_executor_workers': __import__('motor.frameworks.asyncio', fromlist=['_EXECUTOR'])._EXECUTOR._max_workers,
                'limitations': [
                    'Synthetic ASGI routes, no public network or Salla latency.',
                    'Writer load measures persistence segment; safety suite exercises actual upsert/reconciliation paths.',
                    'Each order owns isolated component stock; this does not model common stock contention.',
                    'Hot-order repeats contain idempotent responses, reported separately from successful transitions.',
                    'Queue metric is event-loop admission, not production HTTP admission.',
                    'Mongo pool maxPoolSize=300; no intentional pool admission cap below the 200-operation burst.',
                    'Three trials and finite bursts are not a seasonal sustained-load proof.',
                    'No automatic material-regression threshold: compare measured distributions and agree SLO.',
                    'Transaction observed duration spans monitored commands of one attempt; missing terminal commands are explicit, not inferred.',
                    'Concurrent writer mix retains forced actual writes for comparability; separate sequential polling controls measure real no-op versus changed $set.',
                ]}, indent=2))
            return 0 if benchmark_pass else 1
    finally:
        meter.enabled = False
        await case.asyncTearDown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--trial', type=int, help='Run one named trial in an isolated CI job')
    parser.add_argument('--smoke', action='store_true')
    sys.exit(asyncio.run(main(parser.parse_args())))
