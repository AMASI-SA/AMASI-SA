"""Current vs test-only shared request data; real isolated Mongo acceptance."""
import ast
import asyncio
import hashlib
import inspect
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import threading
import time
from contextlib import ExitStack, asynccontextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psutil
import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.uri_parser import parse_uri
import dashboard_v2_routes as dash
from test_dashboard_b_baseline_mongo import Commands, ReadDB, Work, legacy_handler, populate
from dashboard_b_request_scope_experiment import RequestScope, candidate, compile_function
from dashboard_b_equivalence_fixtures import CASES, OWNER, DEFAULT_KWARGS, seed_mixed


def wire(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()


def permission_guard():
    tree = ast.parse(Path('server.py').read_text(encoding='utf8'))
    namespace = {'HTTPException': HTTPException}
    for name in ('_is_owner', '_require_owner'):
        compile_function(next(n for n in tree.body if getattr(n, 'name', None) == name), namespace)
    return namespace['_require_owner']


@asynccontextmanager
async def isolated():
    uri = os.environ.get('MZ2_TEST_MONGO_URI', '')
    assert uri
    parsed = parse_uri(uri)
    assert not parsed['username'] and not parsed['password'] and not parsed['database']
    assert all(h in {'127.0.0.1', 'localhost', '::1'} for h, _ in parsed['nodelist'])
    commands = Commands()
    client = AsyncIOMotorClient(uri, event_listeners=[commands], serverSelectionTimeoutMS=5000)
    assert (await client.admin.command('buildInfo'))['version'] == '8.0.12'
    assert (await client.admin.command('hello'))['setName'] == 'performancepr1'
    raw = client['dashboard_b_shared_' + uuid4().hex]
    connect = socket.socket.connect
    def local_only(sock, address):
        assert isinstance(address, tuple) and address[0] in {'127.0.0.1', 'localhost', '::1'}
        return connect(sock, address)
    try:
        with patch.object(socket.socket, 'connect', local_only), patch.object(dash, '_today_riyadh', lambda: date(2026, 10, 8)):
            yield raw, ReadDB(raw), commands
    finally:
        assert raw.name.startswith('dashboard_b_shared_')
        await client.drop_database(raw.name)
        client.close()


async def invoke(db, commands, mode, kwargs, user=OWNER, audit=False):
    scope = RequestScope(db)
    snapshots = {}
    # Fingerprints are only for small correctness cases, outside perf samples.
    if audit:
        original = scope.orders
        async def checked(*args):
            rows = await original(*args)
            snapshots.setdefault(id(rows), (rows, wire(rows)))
            return rows
        scope.orders = checked
    work = Work()
    async def current_user(): return user
    with ExitStack() as stack:
        if mode == 'computation':
            from dashboard_b_computation_experiment import candidate as computation_candidate
            factory, legacy = stack.enter_context(computation_candidate(db, scope))
        elif mode == 'shared':
            factory, legacy = stack.enter_context(candidate(db, scope))
        else:
            factory, legacy = dash.make_dashboard_v2_router, legacy_handler(db)
        seen = set()
        for namespace in (vars(dash), factory.__globals__, legacy.__globals__):
            if id(namespace) in seen: continue
            seen.add(id(namespace))
            for name in ('summarize_orders_sar', 'hydrate_order_currency_fields', 'attach_projected_salla_attribution',
                         '_index_products', 'calculate_mezan_v2_line_cost', '_finalize_product_profit_rows',
                         'build_salla_ads_executive_breakdown', 'orders_to_parsed', 'match_settings', 'compute_balances'):
                if name in namespace:
                    stack.enter_context(patch.dict(namespace, {name: work.wrap(name, namespace[name])}))
        router = factory(db, current_user, legacy, permission_guard())
        endpoint = next(r.endpoint for r in router.routes if r.path == '/dashboard-v2')
        assert 'limit' not in inspect.signature(endpoint).parameters
        lag = []; done = asyncio.Event(); rss_stop = threading.Event()
        process = psutil.Process(); rss_start = process.memory_info().rss; rss = [rss_start]
        def memory_sampler():
            while not rss_stop.wait(.01): rss.append(process.memory_info().rss)
        thread = threading.Thread(target=memory_sampler, daemon=True); thread.start()
        async def heartbeat():
            while not done.is_set():
                start = time.perf_counter(); await asyncio.sleep(.005)
                lag.append(max(0, time.perf_counter()-start-.005)*1000)
        task = asyncio.create_task(heartbeat()); await asyncio.sleep(0)
        commands.reset(); wall, cpu = time.perf_counter(), time.thread_time()
        try:
            try:
                response = await endpoint(user=user, **kwargs)
            except HTTPException as exc:
                response = {'http_error': exc.status_code, 'detail': exc.detail}
            elapsed, cpu_ms = (time.perf_counter()-wall)*1000, (time.thread_time()-cpu)*1000
        finally:
            await asyncio.sleep(.006); done.set(); await task
            rss.append(process.memory_info().rss); rss_stop.set(); thread.join()
        assert not commands.writes
        if audit:
            for rows, snapshot in snapshots.values(): assert wire(rows) == snapshot, 'Shared input mutated by a consumer'
        serialize = time.perf_counter(); encoded = wire(response)
        serialization_ms = (time.perf_counter()-serialize)*1000
        metrics = {'handler_ms': elapsed, 'python_cpu_ms': cpu_ms, 'mongo_ms': commands.duration_ms,
            'mongo_commands': dict(commands.counts), 'documents_read': sum(commands.docs.values()),
            'docs_by_collection': {k.split('.',1)[1]:v for k,v in commands.docs.items()},
            'computations': dict(work.calls), 'shared_load_hits': scope.hits,
            'event_loop_max_lag_ms': max(lag, default=0), 'serialization_ms': serialization_ms,
            'response_bytes': len(encoded), 'json_sha256': hashlib.sha256(encoded).hexdigest(),
            'rss_start_bytes': rss_start, 'rss_peak_bytes': max(rss), 'rss_peak_delta_bytes': max(rss)-rss_start}
        await scope.close()
        return response, metrics


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
async def test_full_json_equivalence(case):
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        if case['settings']:
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': case['settings']})
        before, current = await invoke(db, commands, 'current', case['kwargs'], case['user'])
        after, shared = await invoke(db, commands, 'shared', case['kwargs'], case['user'], audit=True)
        assert before == after
        assert wire(before) == wire(after), case['name']
        if case['name'] == 'permission_denied':
            assert after['http_error'] == 403
            assert sum(current['mongo_commands'].values()) == sum(shared['mongo_commands'].values()) == 0
        else:
            assert 'http_error' not in before


@pytest.mark.asyncio
async def test_new_request_observes_changed_database():
    async with isolated() as (raw, db, commands):
        await populate(raw, 4)
        first, _ = await invoke(db, commands, 'shared', DEFAULT_KWARGS, audit=True)
        await raw.unified_orders.update_one({'order_number': '1000000'}, {'$set': {'total_amount': 123, 'total_amount_sar': 123}})
        second, _ = await invoke(db, commands, 'shared', DEFAULT_KWARGS, audit=True)
        reference, _ = await invoke(db, commands, 'current', DEFAULT_KWARGS)
        assert wire(second) == wire(reference)
        assert first['totals']['total_sales'] != second['totals']['total_sales']


@pytest.mark.asyncio
async def test_paired_request_scope_performance():
    evidence = {'head': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python': platform.python_version(), 'platform': platform.platform(), 'mongo': '8.0.12', 'cases': []}
    repeats = int(os.environ.get('DASHBOARD_PERF_REPEATS', '10'))
    sizes = [int(x) for x in os.environ.get('DASHBOARD_PERF_SIZES','10000,50000,100000').split(',')]
    async with isolated() as (raw, db, commands):
        for size in sizes:
            await populate(raw, size)
            for period, kwargs in [('month', DEFAULT_KWARGS), ('overlap', {**DEFAULT_KWARGS,'from_date':'2026-10-02','to_date':'2026-10-06'})]:
                samples = {'current': [], 'shared': []}
                for iteration in range(repeats+1):
                    digests = []
                    pair = {}
                    for mode in (['current','shared'] if iteration % 2 == 0 else ['shared','current']):
                        response, metrics = await invoke(db, commands, mode, kwargs)
                        assert response['totals']['total_orders'] == size
                        assert response['totals']['total_sales'] == 100*size
                        assert response['totals']['total_product_cost'] == 37.5*size
                        digests.append(metrics['json_sha256'])
                        pair[mode] = metrics
                        if iteration: samples[mode].append(metrics)
                        del response
                    assert digests[0] == digests[1], 'Full JSON mismatch in paired performance sample'
                    assert pair['shared']['shared_load_hits'] == 1
                    assert pair['current']['documents_read'] - pair['shared']['documents_read'] == 2*size
                    assert pair['current']['computations']['orders_to_parsed']['calls'] == 2
                    assert pair['shared']['computations']['orders_to_parsed']['calls'] == 1
                fields = ('handler_ms','python_cpu_ms','mongo_ms','documents_read','event_loop_max_lag_ms','serialization_ms','response_bytes','rss_peak_bytes','rss_peak_delta_bytes')
                summary = {mode:{field:{'p50':statistics.median(s[field] for s in rows),
                    'p95':sorted(s[field] for s in rows)[math.ceil(.95*len(rows))-1]} for field in fields} for mode,rows in samples.items()}
                evidence['cases'].append({'size':size,'period':period,'samples':samples,'summary':summary})
                Path(os.environ.get('DASHBOARD_SHARED_EVIDENCE_PATH','../dashboard-shared-evidence.json')).write_text(json.dumps(evidence,indent=2),encoding='utf8')
                print('DASHBOARD_SHARED '+json.dumps({'size':size,'period':period,'summary':summary}),flush=True)
