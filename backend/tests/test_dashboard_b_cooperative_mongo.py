"""Targeted experiment only: current, unchanged sharing, and parser budgets.
Same real HTTP/Mongo harness; no production handler modifications.
"""
import asyncio
import json
import inspect
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import psutil
import pytest
import uvicorn
import fastapi.routing
from fastapi import FastAPI, Header, HTTPException
from starlette.responses import JSONResponse
import dashboard_v2_routes as dash
from resource_governor import ResourceGovernor
from dashboard_b_computation_experiment import candidate
from dashboard_b_cooperative_experiment import candidate as cooperative_candidate
from test_dashboard_b_baseline_mongo import legacy_handler
from test_dashboard_b_request_scope_mongo import isolated, permission_guard
from test_dashboard_b_interactive_mongo import (request_id, ObservedGovernor,
    RequestTimes, quantiles, seed, summary)

async def wave(db, commands, mode, scenario, baseline=False, phase=0):
    base = ResourceGovernor()
    assert base._global_capacity == 2 and base._weights['dashboard'] == 2
    assert base._limits['dashboard']._value == 2
    governor = ObservedGovernor(base)
    spans = []
    parser_spans = []
    rss = [psutil.Process().memory_info().rss]
    rss_stop = threading.Event()
    def sample_rss():
        while not rss_stop.wait(.01): rss.append(psutil.Process().memory_info().rss)
    sampler = threading.Thread(target=sample_rss, daemon=True)
    sampler.start()
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
        if mode.startswith('cooperative-'):
            def parsed(row):
                row['id'] = request_id.get()
                parser_spans.append(row)
            factory, legacy = stack.enter_context(cooperative_candidate(db, budget_ms=float(mode.split('-')[1]), observer=parsed))
        elif mode == 'computation':
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
        stack.enter_context(patch.object(fastapi.routing, 'jsonable_encoder', observe('response_encoding', fastapi.routing.jsonable_encoder)))
        stack.enter_context(patch.object(JSONResponse, 'render', observe('json_render', JSONResponse.render)))
        seen = set()
        for namespace in (vars(dash), factory.__globals__, legacy.__globals__):
            if id(namespace) in seen:
                continue
            seen.add(id(namespace))
            for name in ('orders_to_parsed', 'summarize_orders_sar', 'match_settings',
                         '_index_products', '_finalize_product_profit_rows',
                         'build_salla_ads_executive_breakdown', '_copy'):
                if name in namespace and not inspect.iscoroutinefunction(namespace[name]):
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
                  'phase':phase,
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
                str(Path(__file__).with_name('dashboard_b_cooperative_client.py')),
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
            rss_stop.set()
            sampler.join()
            rss.append(psutil.Process().memory_info().rss)
        for row in governor.rows:
            if 'admitted' in row:
                row['admission_wait_ms'] = (row['admitted']-row['start'])*1000
                row['execution_ms'] = (row['body_end']-row['admitted'])*1000
                request = next(r for r in timed.rows if r['id'] == row['id'])
                row['post_handler_to_headers_ms'] = (request['response_start']-row['body_end'])*1000
        return {'parser_spans':parser_spans, 'rss_start_bytes':rss[0], 'rss_peak_bytes':max(rss), 'load':load, 'requests':timed.rows, 'governor':governor.rows,
                'heartbeat':heartbeat, 'spans':spans, 'api_thread_cpu_ms':cpu_ms,
                'wall_ms':wall_ms, 'mongo_commands':dict(commands.counts),
                'mongo_driver_total_ms':commands.duration_ms,
                'mongo_events':mongo_events,
                'documents_read':sum(commands.docs.values())}


def summarize(waves):
    report = summary(waves)
    for key in ('rss_start_bytes','rss_peak_bytes'):
        report[key] = quantiles([w[key] for w in waves])
    report['serialization'] = {name:quantiles([(s['end']-s['start'])*1000
        for w in waves for s in w['spans'] if s['name']==name and s['id'].startswith('dashboard-')])
        for name in ('response_encoding','json_render')}
    report['parser_slices_cpu_ms'] = quantiles([s['cpu_ms'] for w in waves
        for p in w['parser_spans'] for s in p['slices']])
    report['parser_slices_wall_ms'] = quantiles([(s['end']-s['start'])*1000 for w in waves
        for p in w['parser_spans'] for s in p['slices']])
    report['parser_yields'] = sum(p['yields'] for w in waves for p in w['parser_spans'])
    report['dashboard_mongo_commands_per_wave'] = [sum(m['kind']=='dashboard' for m in w['mongo_events']) for w in waves]
    return report


@pytest.mark.asyncio
async def test_cooperative_interactive():
    size = int(os.environ['DASHBOARD_INTERACTIVE_SIZE'])
    scenario = os.environ['DASHBOARD_INTERACTIVE_SCENARIO']
    repeats = int(os.environ.get('DASHBOARD_INTERACTIVE_REPEATS','6'))
    modes = ['current','computation','cooperative-1','cooperative-5','cooperative-10']
    evidence = {'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python':platform.python_version(),'platform':platform.platform(),'mongo':'8.0.12',
        'size':size,'scenario':scenario,'runtime_adoption':'NOT_PERFORMED',
        'reference':'current runtime and unchanged computation-only control',
        'materiality_rule':'diagnostic: >10% AND >25ms interactive p95/p99; do not select on Dashboard alone',
        'baseline':{},'waves':{m:[] for m in modes}}
    path = Path(os.environ['DASHBOARD_INTERACTIVE_EVIDENCE_PATH'])
    async with isolated() as (raw, db, commands):
        await seed(raw, size, scenario)
        evidence['baseline']['current'] = await wave(db,commands,'current',scenario,baseline=True)
        for iteration in range(repeats+1):
            pairs = {}
            # Cyclic and reversed balanced ordering, deterministic arrival offsets.
            ordered = modes[iteration%len(modes):]+modes[:iteration%len(modes)]
            if iteration%2: ordered.reverse()
            for mode in ordered:
                result = await wave(db,commands,mode,scenario,phase=(iteration*.037)% .25)
                evidence['last_wave'] = {'mode':mode,'iteration':iteration,'result':result}
                path.write_text(json.dumps(evidence),encoding='utf8')
                assert not any(r.get('generator_overflow') for r in result['load']['records'])
                rows = [r for r in result['load']['records'] if r['kind']=='dashboard']
                assert all(r.get('status') == 200 for r in rows), rows
                pairs[mode] = sorted((r['merchant'],r['digest']) for r in rows)
                if mode.startswith('cooperative'):
                    assert result['parser_spans'] and all(p['completed'] for p in result['parser_spans'])
                    if size>=10000: assert all(p['yields']>0 for p in result['parser_spans'])
                if iteration: evidence['waves'][mode].append(result)
                print('COOPERATIVE_WAVE '+json.dumps({'size':size,'scenario':scenario,'iteration':iteration,'mode':mode}),flush=True)
            assert all(pairs[m]==pairs['current'] for m in modes), 'Full HTTP JSON mismatch'
            evidence['summary'] = {m:summarize(w) for m,w in evidence['waves'].items() if w}
            path.write_text(json.dumps(evidence),encoding='utf8')
    print('COOPERATIVE_RESULT '+json.dumps(evidence['summary']),flush=True)
