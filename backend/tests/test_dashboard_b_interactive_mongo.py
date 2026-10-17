"""Real HTTP/real Mongo, same API event loop, independent load process.

No runtime edits. Synthetic lightweight endpoints intentionally isolate loop
responsiveness from unrelated operational business logic. Not a Production trace.
"""
import asyncio
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
from contextlib import ExitStack, asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import patch

import pytest
import uvicorn
from fastapi import FastAPI, Header, HTTPException

import auth
import dashboard_v2_routes as dash
from dashboard_b_computation_experiment import candidate
from resource_governor import ResourceGovernor
from test_dashboard_b_baseline_mongo import legacy_handler, populate
from test_dashboard_b_request_scope_mongo import isolated, permission_guard

request_id = ContextVar('interactive_fixture_id', default='none')


def quantiles(values):
    values = sorted(values)
    if not values:
        return {'n':0}
    return {'n':len(values), 'p50':statistics.median(values),
            'p95':values[math.ceil(.95*len(values))-1],
            'p99':values[math.ceil(.99*len(values))-1], 'max':values[-1]}


class ObservedGovernor:
    """Delegate unchanged policy; observe entry/admission/body separately."""
    def __init__(self, governor):
        self.base = governor
        self.rows = []

    @asynccontextmanager
    async def heavy(self, *args, **kwargs):
        row = {'id':request_id.get(), 'start':time.perf_counter()}
        self.rows.append(row)
        try:
            async with self.base.heavy(*args, **kwargs) as token:
                row['admitted'] = time.perf_counter()
                try:
                    yield token
                finally:
                    row['body_end'] = time.perf_counter()
        finally:
            row['end'] = time.perf_counter()


class RequestTimes:
    def __init__(self, app):
        self.app, self.rows = app, []

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        token = request_id.set(headers.get(b'x-fixture-id', b'none').decode())
        row = {'id':request_id.get(), 'path':scope['path'], 'start':time.perf_counter()}
        self.rows.append(row)
        async def observed_send(message):
            if message['type'] == 'http.response.start':
                row.update(response_start=time.perf_counter(), status=message['status'])
            await send(message)
        try:
            await self.app(scope, receive, observed_send)
        finally:
            row['end'] = time.perf_counter()
            request_id.reset(token)


async def seed(raw, size, scenario):
    await populate(raw, size)
    # Keep interactive queries intentionally bounded/indexed in all designs.
    await raw.unified_orders.create_index([('user_id',1), ('order_number',1)])
    if scenario == 'multi-different':
        for owner in ('fixture-second', 'fixture-third'):
            await auth.ensure_user_settings(raw, owner)
            for name in ('unified_orders', dash.PRODUCTS, dash.COST_PROFILES):
                batch = []
                async for document in raw[name].find({'user_id':'fixture-owner'}, {'_id':0}).batch_size(1000):
                    document['user_id'] = owner
                    batch.append(document)
                    if len(batch) == 1000:
                        await raw[name].insert_many(batch)
                        batch = []
                if batch:
                    await raw[name].insert_many(batch)
    await auth.ensure_user_settings(raw, 'fixture-interactive')
    rows = await raw.unified_orders.find({'user_id':'fixture-owner'}, {'_id':0}).limit(10).to_list(10)
    for row in rows:
        row['user_id'] = 'fixture-interactive'
    await raw.unified_orders.insert_many(rows)


async def wave(db, commands, mode, scenario, baseline=False):
    base = ResourceGovernor()
    assert base._global_capacity == 2 and base._weights['dashboard'] == 2
    assert base._limits['dashboard']._value == 2
    governor = ObservedGovernor(base)
    spans = []
    mongo_events, in_flight = [], {}
    with ExitStack() as stack:
        original_started, original_succeeded = commands.started, commands.succeeded
        def mongo_started(event):
            original_started(event)
            query = event.command
            kind = ('order' if query.get('find') == 'unified_orders' and 'order_number' in query.get('filter', {}) else
                    'small-list' if query.get('find') == 'unified_orders' and query.get('limit') == 10 else 'dashboard')
            in_flight[event.request_id] = kind
        def mongo_succeeded(event):
            original_succeeded(event)
            mongo_events.append({'kind':in_flight.pop(event.request_id,'other'),
                                 'command':event.command_name, 'ms':event.duration_micros/1000})
        stack.enter_context(patch.object(commands, 'started', mongo_started))
        stack.enter_context(patch.object(commands, 'succeeded', mongo_succeeded))
        stack.enter_context(patch.object(dash, 'governor', governor))
        if mode == 'computation':
            factory, legacy = stack.enter_context(candidate(db, None))
        else:
            factory, legacy = dash.make_dashboard_v2_router, legacy_handler(db)
        # Aggregate synchronous work and keep coarse spans for stall attribution.
        def observe(name, function):
            def wrapped(*args, **kwargs):
                start, cpu = time.perf_counter(), time.thread_time()
                try:
                    return function(*args, **kwargs)
                finally:
                    spans.append({'name':name, 'id':request_id.get(), 'start':start,
                        'end':time.perf_counter(), 'cpu_ms':(time.thread_time()-cpu)*1000})
            return wrapped
        seen = set()
        for namespace in (vars(dash), factory.__globals__, legacy.__globals__):
            if id(namespace) in seen:
                continue
            seen.add(id(namespace))
            for name in ('orders_to_parsed', 'summarize_orders_sar', 'match_settings',
                         '_index_products', '_finalize_product_profit_rows',
                         'build_salla_ads_executive_breakdown', '_copy'):
                if name in namespace:
                    stack.enter_context(patch.dict(namespace, {name:observe(name, namespace[name])}))
        app = FastAPI()
        async def user(x_fixture_merchant: str = Header()):
            if x_fixture_merchant not in {'fixture-owner','fixture-second','fixture-third','fixture-interactive'}:
                raise HTTPException(403)
            return {'id':x_fixture_merchant, 'role':'owner'}
        app.include_router(factory(db, user, legacy, permission_guard()))
        projection = {'_id':0, 'order_number':1, 'order_status':1}
        @app.get('/fixture/small-list')
        async def small_list(x_fixture_merchant: str = Header()):
            return await db.unified_orders.find({'user_id':x_fixture_merchant}, projection).sort('order_number',1).limit(10).to_list(10)
        @app.get('/fixture/order')
        async def one_order(x_fixture_merchant: str = Header()):
            return await db.unified_orders.find_one({'user_id':x_fixture_merchant, 'order_number':'1000000'}, projection)
        @app.get('/fixture/ping')
        async def ping():
            return {'ok':True}
        timed = RequestTimes(app)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(timed, host='127.0.0.1', port=port,
            log_level='error', access_log=False, lifespan='off', loop='asyncio', http='h11'))
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if server_task.done():
                await server_task
            await asyncio.sleep(.01)
        dashboards = ['fixture-owner']
        if scenario.startswith('multi'):
            dashboards = (['fixture-owner']*3 if scenario.endswith('same') else
                          ['fixture-owner','fixture-second','fixture-third'])
        config = {'base':f'http://127.0.0.1:{port}', 'dashboards':[] if baseline else dashboards,
                  'interactive_merchant':'fixture-owner' if scenario.endswith('same') else 'fixture-interactive'}
        heartbeat, finished = [], asyncio.Event()
        async def beat():
            while not finished.is_set():
                expected = time.perf_counter() + .01
                await asyncio.sleep(.01)
                heartbeat.append({'expected':expected, 'actual':time.perf_counter()})
        pulse = asyncio.create_task(beat())
        commands.reset()
        cpu, started = time.thread_time(), time.perf_counter()
        try:
            child = await asyncio.create_subprocess_exec(sys.executable,
                str(Path(__file__).with_name('dashboard_b_interactive_client.py')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await child.communicate(json.dumps(config).encode())
            assert child.returncode == 0, err.decode()[-3000:]
            load = json.loads(out)
            cpu_ms = (time.thread_time()-cpu)*1000
            wall_ms = (time.perf_counter()-started)*1000
            assert not commands.writes
        finally:
            finished.set()
            await pulse
            server.should_exit = True
            await server_task
            sock.close()
        for row in governor.rows:
            if 'admitted' in row:
                row['admission_wait_ms'] = (row['admitted']-row['start'])*1000
                row['execution_ms'] = (row['body_end']-row['admitted'])*1000
                request = next(r for r in timed.rows if r['id'] == row['id'])
                row['post_handler_to_headers_ms'] = (request['response_start']-row['body_end'])*1000
        return {'load':load, 'requests':timed.rows, 'governor':governor.rows,
                'heartbeat':heartbeat, 'spans':spans, 'api_thread_cpu_ms':cpu_ms,
                'wall_ms':wall_ms, 'mongo_commands':dict(commands.counts),
                'mongo_driver_total_ms':commands.duration_ms,
                'mongo_events':mongo_events,
                'documents_read':sum(commands.docs.values())}


def summary(waves):
    rows = [r for w in waves for r in w['load']['records']]
    report = {}
    for kind in ('dashboard','small-list','order','ping','interactive'):
        selected = [r for r in rows if (r['kind'] != 'dashboard' if kind == 'interactive' else r['kind'] == kind)]
        successful = [r for r in selected if r.get('status') == 200]
        server_rows = [r for w in waves for r in w['requests'] if
                       (r['path'].startswith('/fixture/') if kind=='interactive' else
                        r['path'] == ('/dashboard-v2' if kind=='dashboard' else '/fixture/'+kind))]
        report[kind] = {'scheduled_ms':quantiles([r['scheduled_ms'] for r in successful]),
            'observed_including_timeouts_ms':quantiles([r['scheduled_ms'] for r in selected if 'scheduled_ms' in r]),
            'http_ms':quantiles([r['http_ms'] for r in successful]),
            'asgi_execution_ms':quantiles([(r['end']-r['start'])*1000 for r in server_rows]),
            'dispatch_ms':quantiles([r['dispatch_ms'] for r in selected if 'dispatch_ms' in r]),
            'offered':len(selected), 'success':len(successful),
            'timeouts':sum(r.get('timeout',False) for r in selected),
            'errors':sum(r.get('status') != 200 for r in selected),
            'throughput_success_rps':len(successful)/sum(w['load']['end']-w['load']['start'] for w in waves)}
        waits = []
        for w in waves:
            client_by_id = {r['id']:r for r in w['load']['records'] if 'sent' in r}
            for r in w['requests']:
                applies = r['path'].startswith('/fixture/') if kind=='interactive' else r['path'] == ('/dashboard-v2' if kind=='dashboard' else '/fixture/'+kind)
                if applies and r['id'] in client_by_id:
                    waits.append((r['start']-client_by_id[r['id']]['sent'])*1000)
        report[kind]['pre_asgi_transport_and_scheduling_ms'] = quantiles(waits)
    report['heartbeat_lag_ms'] = quantiles([(r['actual']-r['expected'])*1000 for w in waves for r in w['heartbeat']])
    report['wave_max_heartbeat_lag_ms'] = quantiles([max((r['actual']-r['expected'])*1000 for r in w['heartbeat']) for w in waves])
    report['mongo_command_ms'] = {kind:quantiles([r['ms'] for w in waves for r in w['mongo_events'] if r['kind']==kind])
                                 for kind in ('dashboard','small-list','order')}
    for field in ('api_thread_cpu_ms','mongo_driver_total_ms'):
        report[field] = quantiles([w[field] for w in waves])
    for field in ('admission_wait_ms','execution_ms','post_handler_to_headers_ms'):
        report[field] = quantiles([r[field] for w in waves for r in w['governor'] if field in r])
    return report


@pytest.mark.asyncio
async def test_interactive_concurrency():
    size = int(os.environ['DASHBOARD_INTERACTIVE_SIZE'])
    scenario = os.environ['DASHBOARD_INTERACTIVE_SCENARIO']
    repeats = int(os.environ.get('DASHBOARD_INTERACTIVE_REPEATS','6'))
    assert scenario in {'single-same','single-different','multi-same','multi-different'}
    evidence = {'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python':platform.python_version(), 'platform':platform.platform(), 'mongo':'8.0.12',
        'size':size, 'scenario':scenario, 'runtime_adoption':'NOT_PERFORMED',
        'materiality_rule':'interactive p95 or p99 >10% AND >25ms; timeouts +1 percentage point; inspect per-kind and paired direction',
        'baseline':{}, 'waves':{'current':[], 'computation':[]}}
    path = Path(os.environ['DASHBOARD_INTERACTIVE_EVIDENCE_PATH'])
    async with isolated() as (raw, db, commands):
        await seed(raw, size, scenario)
        for mode in ('current','computation'):
            evidence['baseline'][mode] = await wave(db, commands, mode, scenario, baseline=True)
        path.write_text(json.dumps(evidence,indent=2),encoding='utf8')
        for iteration in range(repeats+1):
            pairs = {}
            for mode in (('current','computation') if iteration%2 == 0 else ('computation','current')):
                result = await wave(db, commands, mode, scenario)
                evidence['last_wave'] = {'mode':mode, 'iteration':iteration, 'result':result}
                path.write_text(json.dumps(evidence,indent=2),encoding='utf8')
                assert not any(r.get('generator_overflow') for r in result['load']['records'])
                dashboard_rows = [r for r in result['load']['records'] if r['kind']=='dashboard']
                assert all(r.get('status') == 200 for r in dashboard_rows), dashboard_rows
                pairs[mode] = sorted((r['merchant'], r['digest']) for r in dashboard_rows)
                if iteration:
                    evidence['waves'][mode].append(result)
            assert pairs['current'] == pairs['computation'], 'Full HTTP Dashboard JSON differs'
            evidence['summary'] = {mode:summary(waves) for mode,waves in evidence['waves'].items() if waves}
            path.write_text(json.dumps(evidence,indent=2),encoding='utf8')
            print('INTERACTIVE_PAIR '+json.dumps({'size':size,'scenario':scenario,'iteration':iteration}),flush=True)
    print('INTERACTIVE_RESULT '+json.dumps(evidence['summary']),flush=True)
