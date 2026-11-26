"""One bounded synthetic profile of the real retained reconciliation tail.

Draft: checkpoint and obtain the coordinator's execution authorization first.
No Mongo client, network, production configuration, Git, or runtime source edit.
The only writes are an exclusively created diagnostic output directory and a
BSON-backed in-memory adapter. The two openings and one allocated payment are
created by the real service functions, using the payload pattern in
test_operational_balance_integration::
test_baseline_is_not_cash_movement_and_payment_allocates_once.

9,000 real four-item synthetic orders produce the source and reconciliation
state. No historical obligation/audit rows are padded or manually fabricated.
The real coverage, full recovery proof, payment-evidence linker, archive plan,
page staging, BSON storage round trip, and archive validation run on first and
retained passes. A full proof must complete: record/metadata-limit early-outs
are failures, never a cheap successful timing sample.

All stage timings are cProfile-instrumented diagnostics, not plain latency or
an 85-second production-SLA forecast. Fake reads have no network, lease or
authorization latency; source cursor rows are already prepared. Profit building,
populated native shipping/advertising/payroll history, automatic bank claims,
Mongo transactions/CAS contention and actual provider I/O are not exercised.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from copy import deepcopy
import cProfile
from datetime import datetime, timezone
from decimal import Decimal
import gc
import hashlib
import json
import os
from pathlib import Path
import pickle
import pstats
import resource
import signal
import socket
import sys
import threading
import time
from types import SimpleNamespace


ROOT = Path('/workspace/scratch/1f2d9548e6ff/mezan-refresh-source-root-fix')
HERE = Path(__file__).resolve().parent
PREVIOUS_PROFILE = ROOT.parent / 'completion-release-prep/reducer-profile/run/result.json'
FIXTURE = ROOT / 'docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence'
ORDER_COUNT = 9_000
TOTAL_SECONDS = 240
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.dont_write_bytecode = True
sys.path[:0] = [str(FIXTURE), str(ROOT / 'backend'), str(ROOT / 'backend/tests')]


def forbidden_network(*_args, **_kwargs):
    raise AssertionError('This diagnostic forbids every network connection and DNS lookup')


# A local asyncio socketpair is still permitted; no connection or DNS is.
socket.socket.connect = forbidden_network
socket.socket.connect_ex = forbidden_network
socket.create_connection = forbidden_network
socket.getaddrinfo = forbidden_network

from bson import BSON, ObjectId, decode as decode_bson
from pymongo.errors import DuplicateKeyError
import profile_source_preparation as fixture_module
import operational_balance_archive as archive
import operational_balance_engine as engine
import operational_balance_recovery_proof as proof_module
import operational_balance_service as service
import operational_balance_sources as sources_module
import operational_balance_store as store
import operational_entity_freshness as freshness
from operational_bank_receipt_rollout import initialize_admission
from operational_financial_activity_runtime import preparation_scope
from operational_payment_cas_reconciliation import reconcile_staged_payment_evidence


OWNER = fixture_module.OWNER
START = fixture_module.START
NOW = fixture_module.NOW
MOVEMENT_TIME = '2026-11-02T08:00:00+00:00'
MISSING = object()


def fingerprint(value):
    class Sink:
        def __init__(self):
            self.digest, self.size = hashlib.sha256(), 0
        def write(self, chunk):
            self.digest.update(chunk)
            self.size += len(chunk)
    sink = Sink()
    pickle.Pickler(sink, protocol=5).dump(value)
    return {'sha256': sink.digest.hexdigest(), 'pickle_bytes': sink.size}


def runtime_hashes():
    # Reuse the complete prior runtime inventory; do not invoke Git or claim
    # that a worktree's content is an immutable commit merely from its name.
    names = json.loads(PREVIOUS_PROFILE.read_text())['runtime_sha256']['files']
    result, digest = {}, hashlib.sha256()
    for name in sorted(names):
        assert name.startswith('backend/') and name.endswith('.py') and '/tests/' not in name
        value = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        result[name] = value
        digest.update(name.encode() + b'\0' + value.encode() + b'\0')
    return {'manifest_sha256': digest.hexdigest(), 'files': result}


def dotted(row, path):
    value = row
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def matches(row, query):
    for key, expected in query.items():
        assert not key.startswith('$'), ('unsupported memory query', key)
        actual = dotted(row, key)
        if type(expected) is dict and '$exists' in expected:
            assert set(expected) == {'$exists'} and type(expected['$exists']) is bool
            if (actual is not MISSING) != expected['$exists']:
                return False
        elif type(expected) is dict and '$elemMatch' in expected:
            assert set(expected) == {'$elemMatch'}
            if not isinstance(actual, list) or not any(matches(item, expected['$elemMatch']) for item in actual):
                return False
        elif actual is MISSING or actual != expected:
            return False
    return True


def project(row, projection):
    if row is None or projection is None:
        return row
    assert all(value in (0, 1) for value in projection.values())
    included = [key for key, value in projection.items() if value == 1]
    if not included:
        assert set(projection) <= {'_id'}
        return {key: value for key, value in row.items() if key not in projection}
    result = {}
    for path in included:
        value = dotted(row, path)
        if value is MISSING:
            continue
        target = result
        parts = path.split('.')
        for key in parts[:-1]:
            target = target.setdefault(key, {})
        target[parts[-1]] = value
    if projection.get('_id') != 0 and '_id' in row:
        result['_id'] = row['_id']
    return result


class MemoryCollection:
    """Only synthetic state, immutable pages and operation claims can be written."""
    def __init__(self, db, name):
        self.db, self.name = db, name

    async def find_one(self, query, projection=None):
        self.db.events.append([self.name, 'find_one'])
        if self.name in self.db.rows:
            values = self.db.rows[self.name]
            # A point read must not repeatedly decode earlier archive pages.
            # This adapter uses its exact-ID map just like the intended query.
            if type(query.get('_id')) is str:
                encoded = values.get(query['_id'])
                payloads = () if encoded is None else (encoded,)
            else:
                payloads = values.values()
            rows = (decode_bson(value) for value in payloads)
        else:
            assert self.name in {'users', 'settings', 'mz2_atomic_owners'}
            rows = (deepcopy(row) for row in self.db.catalog.get(self.name, []))
        return project(next((row for row in rows if matches(row, query)), None), projection)

    def find(self, query, projection=None):
        assert self.name not in self.db.rows and self.name != 'mz2_atomic_owners'
        return self.db.source_db[self.name].find(query, projection)

    def with_options(self, *, write_concern, read_concern):
        assert self.name == archive.AUDIT_PAGES
        assert write_concern.document == {'w': 'majority'}
        assert read_concern.document == {'level': 'majority'}
        return self

    async def insert_one(self, row):
        assert self.name in self.db.rows
        assert row.get('owner_id') == OWNER
        rows = self.db.rows[self.name]
        if row['_id'] in rows:
            raise DuplicateKeyError('synthetic immutable identity already exists')
        rows[row['_id']] = bytes(BSON.encode(row))
        self.db.events.append([self.name, 'insert_one'])
        return SimpleNamespace(inserted_id=row['_id'])

    async def replace_one(self, query, row):
        assert self.name == store.STATES
        assert set(query) == {'_id', 'owner_id', 'revision'}
        assert query['_id'] == query['owner_id'] == row['_id'] == row['owner_id'] == OWNER
        prior = self.db.rows[self.name].get(OWNER)
        matched = prior is not None and matches(decode_bson(prior), query)
        if matched:
            self.db.rows[self.name][OWNER] = bytes(BSON.encode(row))
        self.db.events.append([self.name, 'replace_one', bool(matched)])
        return SimpleNamespace(matched_count=int(matched))

    def __getattr__(self, name):
        raise AssertionError(('unsupported memory storage operation', self.name, name))


class MemoryDatabase:
    def __init__(self, catalog):
        self.catalog = catalog
        self.source_db = fixture_module.collector_database(catalog)
        self.rows = {store.STATES: {}, store.OPERATION_CLAIMS: {}, archive.AUDIT_PAGES: {}}
        self.events = []

    def __getitem__(self, name):
        if name in self.rows or name in {'users', 'settings', 'mz2_atomic_owners'}:
            return MemoryCollection(self, name)
        return self.source_db[name]

    def __getattr__(self, name):
        raise AssertionError(('unsupported memory database access', name))


def catalog_rows():
    # These are the same entity shapes as the existing integration test,
    # with visibly synthetic labels and deterministic identities.
    return {
        'users': [{'id': OWNER, 'role': 'owner', 'name': 'Synthetic Owner', 'is_active': True}],
        'mz2_financial_accounts': [{'id': 'bank', 'user_id': OWNER, 'name': 'Synthetic Bank',
            'account_type': 'bank', 'currency': 'SAR', 'status': 'active'}],
        'mezan_suppliers_v2': [{'id': 'supplier', 'user_id': OWNER,
            'name': 'Synthetic Supplier', 'status': 'active'}],
    }


async def seed_valid_history(db):
    opening = {'request_id': 'baseline1', 'party_type': 'bank', 'party_id': 'bank',
               'amount': '1000', 'currency': 'SAR', 'direction': 'for_us'}
    await service.save_opening(db, OWNER, OWNER, opening, clock=START)
    await service.save_opening(db, OWNER, OWNER, {**opening, 'request_id': 'baseline2',
        'party_type': 'supplier', 'party_id': 'supplier', 'amount': '500',
        'direction': 'for_party'}, clock=START)
    await service.save_opening(db, OWNER, OWNER, {'request_id': 'startnow1'}, finish=True, clock=START)
    payload = {'request_id': 'movement1', 'party_type': 'supplier', 'party_id': 'supplier',
               'bank_id': 'bank', 'amount': '300', 'currency': 'SAR', 'direction': 'outgoing',
               'kind': 'payment', 'note': 'Synthetic documented payment', 'receipt_id': None,
               'order_number': None, 'reference': '', 'allocations': []}
    item = await service.create_movement(db, OWNER, OWNER, payload, clock=MOVEMENT_TIME)
    before_replay = bytes(db.rows[store.STATES][OWNER])
    again = await service.create_movement(db, OWNER, OWNER, payload, clock=MOVEMENT_TIME)
    assert item == again and db.rows[store.STATES][OWNER] == before_replay
    state = await store.read(db, OWNER)
    summary = service.report(state)['summary']
    assert summary['actual_liquidity'] == '700.00'
    assert summary['payable'] == '200.00' and summary['settled'] == '300.00'
    assert len(state['openings']) == 2 and len(state['movements']) == 1
    assert len(state['audit']) == 4 and len(state['requests']) == 4
    assert item['allocations'] and sum((Decimal(row['amount']) for row in item['allocations']), Decimal(0)) == Decimal('300')
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    return state, {'source_test': 'test_operational_balance_integration.py::test_baseline_is_not_cash_movement_and_payment_allocates_once',
        'manual_movements': 1, 'openings': 2, 'requests': 4, 'manual_audit': 4,
        'exact_saved_request_replay': True, 'replay_changed_stored_state': False,
        'summary': summary, 'original_state': fingerprint(state)}


async def collect(state):
    old_count = fixture_module.COUNT
    fixture_module.COUNT = ORDER_COUNT
    try:
        data = fixture_module.fixture()
    finally:
        fixture_module.COUNT = old_count
    data.update(catalog_rows())
    for index, binding in enumerate(data['mezan_product_option_cost_bindings_v2']):
        binding.update(_id=ObjectId(f'{index + 1:024x}'), created_at=datetime(2026, 11, 1),
                       updated_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    input_digest, input_sizes = fixture_module.source_fingerprint(data)
    original_state = fingerprint(state)
    db = fixture_module.collector_database(data)
    old_cursor = fixture_module.Cursor.to_list
    async def prepared_rows(cursor, count=None, *, length=None):
        return list(cursor.values[:length if length is not None else count])
    fixture_module.Cursor.to_list = prepared_rows
    started = time.perf_counter_ns()
    try:
        output = await asyncio.wait_for(sources_module.collect_sources(
            db, OWNER, START, NOW, operational_state=state), timeout=85)
    finally:
        fixture_module.Cursor.to_list = old_cursor
    elapsed = (time.perf_counter_ns() - started) / 1e6
    assert fixture_module.source_fingerprint(data)[0] == input_digest
    assert fingerprint(state) == original_state
    assert len(output['orders']) == ORDER_COUNT
    assert sum(len(order['items']) for order in output['orders']) == ORDER_COUNT * 4
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    return output, {'orders': ORDER_COUNT, 'items': ORDER_COUNT * 4,
        'wall_ms_not_profiled': elapsed, 'full_output': fingerprint(output),
        'input_sha256': input_digest, 'input_bson_bytes_by_collection': input_sizes,
        'queries_by_collection': dict(Counter(db.reads)),
        'query_sequence_sha256': hashlib.sha256(json.dumps(db.reads).encode()).hexdigest(),
        'source_input_unchanged': True, 'state_input_unchanged': True,
        'owned_async_tasks_joined': True,
        'issue_codes': dict(Counter(row['code'] for row in output['issues']))}


def profile_table(profiler):
    stats = pstats.Stats(profiler)
    rows = []
    for (filename, line, function), (primitive, total, own, cumulative, callers) in stats.stats.items():
        try:
            filename = str(Path(filename).relative_to(ROOT))
        except ValueError:
            pass
        rows.append({'file': filename, 'line': line, 'function': function,
                     'primitive_calls': primitive, 'total_calls': total,
                     'self_ms': round(own * 1000, 3), 'cumulative_ms': round(cumulative * 1000, 3)})
    return {'total_calls': stats.total_calls, 'primitive_calls': stats.prim_calls,
            'top_cumulative': sorted(rows, key=lambda row: row['cumulative_ms'], reverse=True)[:45],
            'top_self': sorted(rows, key=lambda row: row['self_ms'], reverse=True)[:45]}


class Measurement:
    def __init__(self, output, result):
        self.output, self.result, self.phase = output, result, 'setup'
        self.encode = defaultdict(lambda: {'calls': 0, 'bytes': 0, 'cpu_ns': 0})
        self.gc_start, self.gc_ns, self.gc_counts = {}, 0, Counter()

    def observe_gc(self, phase, info):
        key = (threading.get_ident(), info['generation'])
        if phase == 'start':
            self.gc_start[key] = time.perf_counter_ns()
        elif key in self.gc_start:
            self.gc_ns += time.perf_counter_ns() - self.gc_start.pop(key)
            self.gc_counts[info['generation']] += 1

    def measured(self, label, function, *args, **kwargs):
        self.phase = label
        profiler = cProfile.Profile()
        wall, cpu, old_gc = time.perf_counter_ns(), time.process_time_ns(), self.gc_ns
        try:
            profiler.enable()
            return function(*args, **kwargs)
        finally:
            profiler.disable()
            self.finish(label, profiler, wall, cpu, old_gc, includes_worker_profiles=False)

    async def measured_async(self, label, function, *args, **kwargs):
        self.phase = label
        profiler = cProfile.Profile()
        wall, cpu, old_gc = time.perf_counter_ns(), time.process_time_ns(), self.gc_ns
        try:
            profiler.enable()
            return await function(*args, **kwargs)
        finally:
            profiler.disable()
            # cProfile follows this event-loop thread, not archive worker
            # threads. Wall/process CPU and encode counters include the joined
            # work; do not call the table a complete unpack CPU profile.
            self.finish(label, profiler, wall, cpu, old_gc, includes_worker_profiles=False)

    def finish(self, label, profiler, wall, cpu, old_gc, *, includes_worker_profiles):
        wall_ms = (time.perf_counter_ns() - wall) / 1e6
        cpu_ms = (time.process_time_ns() - cpu) / 1e6
        filename = label + '.pstats'
        profiler.dump_stats(str(self.output / filename))
        self.result['stages'][label] = {'wall_ms_instrumented': wall_ms,
            'process_cpu_ms_instrumented': cpu_ms, 'gc_wall_ms': (self.gc_ns - old_gc) / 1e6,
            'process_high_water_rss_kib_cumulative': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'pstats': filename, 'profiles_worker_threads': includes_worker_profiles,
            **profile_table(profiler)}
        print(json.dumps({'stage': label, 'instrumented_wall_ms': round(wall_ms, 3)}), flush=True)

    def bson_encode(self, document, *args, **kwargs):
        frame = sys._getframe(1)
        key = (self.phase, frame.f_code.co_name, frame.f_lineno)
        started = time.thread_time_ns()
        encoded = BSON.encode(document, *args, **kwargs)
        elapsed = time.thread_time_ns() - started
        record = self.encode[key]
        record['calls'] += 1
        record['bytes'] += len(encoded)
        record['cpu_ns'] += elapsed
        return encoded

    def encode_summary(self):
        return [{'stage': phase, 'caller': function, 'line': line,
                 'calls': record['calls'], 'encoded_bytes_including_repeated_work': record['bytes'],
                 'encode_thread_cpu_ms_instrumented': record['cpu_ns'] / 1e6}
                for (phase, function, line), record in sorted(self.encode.items())]


def counts(state):
    financial = state.get('engine', {})
    result = {key: len(financial.get(key, {})) for key in (
        'obligations', 'facts', 'audit', 'issues', 'order_versions', 'provider_reports',
        'cod_reports', 'delivery_register')}
    result['obligation_kinds'] = dict(Counter(row.get('kind') for row in financial.get('obligations', {}).values()))
    result['issue_codes'] = dict(Counter(row['code'] for row in financial.get('issues', [])))
    result['provider_fee_orders'] = len(financial.get('provider_fee_inputs', {}).get('orders', {}))
    result.update(manual_movements=len(state['movements']), openings=len(state['openings']),
                  requests=len(state['requests']), manual_audit=len(state['audit']))
    return result


def check_full_proof(proof, before, after):
    records = sum(len(value) for value in (before['engine'].get('obligations', {}),
        after['engine']['obligations'], before['movements'], after['movements']))
    assert records <= proof_module.MAX_RECORDS, ('fixture exceeds full-proof record limit', records)
    assert proof.get('complete') is True, ('full proof did not complete', proof.get('reason'),
                                         proof.get('ungrouped_record_count'))
    assert 'reason' not in proof and proof['source_completeness_verified'] is False
    assert proof['removed_obligation_count'] == 0 and not any(proof['new_duplicate_keys'].values())
    assert proof['previous_revision'] == before['revision']
    assert proof['calculation_revision'] == before['revision'] + 1
    assert proof['preservation']['manual_movements']['before_count'] == 1
    assert proof['preservation']['openings']['before_count'] == 2
    assert proof['preservation']['requests']['before_count'] == 4
    for preserved in proof['preservation'].values():
        assert preserved['preserved'] is True
        assert preserved['missing_count'] == preserved['changed_count'] == 0
        assert preserved['before_digest'] == preserved['after_existing_digest']
    return records


async def names_for(db, state):
    result = {}
    for kind in sorted({row['party_type'] for row in state['engine']['obligations'].values()}):
        result[kind] = {row['id']: row['name'] for row in await sources_module.entities(db, OWNER, kind)}
    return result


async def one_pass(label, before, inputs, db, measurement):
    before_digest, sources_digest = fingerprint(before), fingerprint(inputs)
    raw = await db[store.STATES].find_one({'_id': OWNER, 'owner_id': OWNER})
    raw_digest = fingerprint(raw)
    after = measurement.measured(label + '_reconcile', engine.reconcile, before, inputs, NOW)
    assert after is not before and fingerprint(before) == before_digest
    initialize_admission(after, NOW)
    actions = measurement.measured(label + '_bank_claim_candidates', service._bank_credit_actions,
                                   inputs.get('orders', []), after['movements'])
    assert actions == [], 'fixture must not skip a real bank claim or monetary action'
    names = await measurement.measured_async(label + '_party_catalogs_fake_io', names_for, db, after)
    measurement.measured(label + '_party_names', service._apply_refreshed_party_names, after, names)
    after['engine'][freshness.FIELD] = measurement.measured(label + '_coverage', freshness.build_coverage,
        after, inputs, NOW, entity_names=names)
    proof = measurement.measured(label + '_recovery_proof', proof_module.build_recovery_proof,
                                 before, after, inputs, NOW)
    after['engine'][proof_module.FIELD] = proof
    # Record even a failed proof before asserting, so a cheap early-out cannot
    # disappear from the durable diagnostic failure record.
    measurement.result['passes'][label] = {'proof': proof, 'result_counts': counts(after)}
    record_count = check_full_proof(proof, before, after)
    measurement.measured(label + '_payment_evidence_link', reconcile_staged_payment_evidence, after)
    after['engine']['as_of'] = NOW
    assert fingerprint(before) == before_digest and fingerprint(inputs) == sources_digest
    for field in ('openings', 'movements', 'requests', 'audit'):
        assert after[field] == before[field] and after[field] is not before[field]
    if label == 'retained':
        for field in ('obligations', 'facts', 'audit', 'order_versions', 'provider_fee_inputs'):
            assert after['engine'][field] == before['engine'][field], ('retained financial drift', field)
        assert proof['changed_existing']['count'] == 0 and proof['new_obligation_count'] == 0
    unchanged = measurement.measured(label + '_snapshot_compare', lambda: after == before)
    assert unchanged is False, 'this pass must reach the actual storage plan'
    after['revision'] = before['revision'] + 1
    after_digest = fingerprint(after)
    packed, pages = measurement.measured(label + '_archive_prepare', archive.prepare,
        OWNER, raw, before, after, store.MAX_STATE_BYTES)
    assert fingerprint(raw) == raw_digest and fingerprint(before) == before_digest
    assert fingerprint(after) == after_digest
    packed_bytes = bytes(BSON.encode(packed))
    assert len(packed_bytes) <= store.MAX_STATE_BYTES
    await measurement.measured_async(label + '_stage_pages_memory', archive.stage_pages, db, pages)
    commit = await measurement.measured_async(label + '_head_cas_memory', db[store.STATES].replace_one,
        {'_id': OWNER, 'owner_id': OWNER, 'revision': before['revision']}, packed)
    assert commit.matched_count == 1
    stored = await db[store.STATES].find_one({'_id': OWNER, 'owner_id': OWNER})
    stored_digest = fingerprint(stored)
    restored = await measurement.measured_async(label + '_archive_unpack_validate', archive.unpack, db, OWNER, stored)
    assert restored == after, 'full BSON archive round trip changed values/types/history'
    assert fingerprint(stored) == stored_digest and fingerprint(before) == before_digest
    assert fingerprint(inputs) == sources_digest
    if archive.MARKER in stored:
        assert len(restored['engine']['audit']) == stored[archive.MARKER]['count'] + len(stored['engine']['audit']['tail'])
    entry = measurement.result['passes'][label]
    entry.update(full_proof_record_count=record_count, proof_record_limit=proof_module.MAX_RECORDS,
        coverage={'complete': after['engine'][freshness.FIELD]['complete'],
                  'entity_count': len(after['engine'][freshness.FIELD]['rows']),
                  'reasons': dict(Counter(row['reason'] for row in after['engine'][freshness.FIELD]['rows']))},
        before_revision=before['revision'], after_revision=after['revision'],
        before_state=before_digest, result_state=after_digest,
        complete_bson_round_trip_equal=True, state_source_raw_inputs_unchanged=True,
        full_old_manual_history_equal=True, new_pending_pages=len(pages),
        total_archived_pages=len(stored.get(archive.MARKER, {}).get('pages', [])),
        total_archived_events=stored.get(archive.MARKER, {}).get('count', 0),
        final_stored_head_bson_bytes=len(packed_bytes),
        packed_financial_uncompressed_bytes=stored.get(archive.SNAPSHOT, {}).get('size', 0),
        packed_financial_compressed_bytes=len(stored.get(archive.SNAPSHOT, {}).get('data', b'')),
        packed_issues_uncompressed_bytes=stored.get(archive.ISSUES, {}).get('size', 0),
        packed_issues_compressed_bytes=len(stored.get(archive.ISSUES, {}).get('data', b'')))
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    return restored


async def run(measurement):
    db = MemoryDatabase(catalog_rows())
    state, seed = await seed_valid_history(db)
    measurement.result['seed'] = seed
    inputs, collector = await collect(state)
    measurement.result['collector'] = collector
    first = await one_pass('first', state, inputs, db, measurement)
    first_digest = fingerprint(first)
    final = await one_pass('retained', first, inputs, db, measurement)
    assert fingerprint(first) == first_digest
    assert final['revision'] == state['revision'] + 2
    measurement.result['memory_storage'] = {
        'collections': {name: {'documents': len(rows), 'bson_bytes': sum(map(len, rows.values()))}
                        for name, rows in db.rows.items()},
        'operations': dict(Counter(name + ':' + action for name, action, *_ in db.events)),
        'party_and_seed_source_reads': dict(Counter(db.source_db.reads)),
        'real_database_connections': 0, 'real_financial_writes': 0}
    measurement.result['retained_replay_financial_values_equal'] = True
    measurement.result['owned_async_tasks_joined'] = not [task for task in asyncio.all_tasks()
                                                         if task is not asyncio.current_task()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--source-label', required=True,
                        help='Coordinator-provided provenance label; content hashes are authoritative')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    assert output != ROOT and ROOT not in output.parents
    assert output.parent == HERE, 'keep output in one exclusive full-tail child directory'
    output.mkdir(exist_ok=False)
    started = time.perf_counter_ns()
    result = {'experiment': 'one_real_collector_first_and_retained_full_tail', 'status': 'running',
        'source_label_unverified_by_script': args.source_label, 'clock': NOW,
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'fixture_sha256': hashlib.sha256((FIXTURE / 'profile_source_preparation.py').read_bytes()).hexdigest(),
        'timeout_seconds': TOTAL_SECONDS, 'stages': {}, 'passes': {},
        'limitations': [
            'cProfile and per-encode counters add overhead; no plain-wall or production SLA claim',
            'One deterministic 9000-order cohort; no A/B comparison or production representativeness claim',
            'Synthetic prepared cursor rows exclude Mongo wire/decode/server time and lease/auth I/O',
            'Fake BSON-backed storage validates content and revisions but not Mongo durability/concurrency',
            'No profit building, populated native bundle/shipping/ad/payroll, or bank claim execution',
            'Unknown provider fees remain unknown; full recovery proof is not source completeness',
            'Unpack uses real joined worker preparation; its event-loop cProfile excludes worker stacks',
            'RSS is process high-water including fixture setup, retained sources and diagnostic validation',
            'Existing runtime inventory is hashed; source label alone is not proof of a commit identity']}
    measurement = Measurement(output, result)
    stop = threading.Event()
    prior_bson = archive.BSON
    prior_handler = signal.getsignal(signal.SIGALRM)
    def bounded(_signum, _frame):
        stop.set()
        raise TimeoutError('full-tail diagnostic exceeded the 240-second total bound')
    try:
        signal.signal(signal.SIGALRM, bounded)
        signal.alarm(TOTAL_SECONDS)
        result['runtime_sha256_before'] = runtime_hashes()
        assert gc.isenabled(), 'measure with normal cyclic GC enabled'
        gc.collect()  # One untimed pre-run cleanup; no collection between tail stages.
        gc.callbacks.append(measurement.observe_gc)
        archive.BSON = SimpleNamespace(encode=measurement.bson_encode)
        with preparation_scope(stop):
            asyncio.run(run(measurement))
        result['runtime_sha256_after'] = runtime_hashes()
        assert result['runtime_sha256_before'] == result['runtime_sha256_after'], 'runtime changed during profile'
        result['runtime_unchanged'] = True
        result['status'] = 'complete'
    except BaseException as exc:
        stop.set()
        result['status'] = 'failed'
        result['failure'] = {'type': type(exc).__name__, 'message': str(exc), 'phase': measurement.phase}
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prior_handler)
        archive.BSON = prior_bson
        if measurement.observe_gc in gc.callbacks:
            gc.callbacks.remove(measurement.observe_gc)
        result['archive_encode_calls'] = measurement.encode_summary()
        result['total_gc_wall_ms'] = measurement.gc_ns / 1e6
        result['total_gc_collections'] = dict(measurement.gc_counts)
        result['elapsed_seconds_including_setup_and_validation'] = (time.perf_counter_ns() - started) / 1e9
        result['process_high_water_rss_kib_cumulative'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with (output / 'result.json').open('x') as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
            handle.write('\n')


if __name__ == '__main__':
    main()
