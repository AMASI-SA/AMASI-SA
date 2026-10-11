"""Read-only baseline RCA for Dashboard B; no runtime optimization or cache."""
import ast
import asyncio
import hashlib
import json
import logging
import math
import os
import platform
import socket
import statistics
import subprocess
import threading
import time
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import Depends, Query
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from pymongo.monitoring import CommandListener
from pymongo.uri_parser import parse_uri
import auth
import dashboard_v2_routes as dash
from balances import compute_balances
from excel_parser import match_settings
from expenses_routes import compute_operating_expenses_for_range
from orders_db import orders_to_parsed
from settlements_routes import aggregate_settlements_by_provider, classify_14d_window

BASE = '79c47cdfbb95bd9ce944c0e61d639d349f9e7af0'


class ReadCollection:
    def __init__(self, collection): self.collection = collection
    def __getattr__(self, name):
        assert name in {'find', 'find_one', 'aggregate', 'count_documents', 'distinct'}, 'Unexpected collection operation: ' + name
        return getattr(self.collection, name)


class ReadDB:
    def __init__(self, db): self.db = db
    def __getitem__(self, name): return ReadCollection(self.db[name])
    def __getattr__(self, name): return self[name]


def legacy_handler(db):
    # Load actual pure function bodies/constants, not server startup, schedulers,
    # production environment initialization or a synthetic legacy-dashboard stub.
    source = Path('server.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    names = {'NetSalesConfig', 'DEFAULT_NET_SALES_CONFIG', 'DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES',
             'DEFAULT_SHIPPING_APPROVED', 'DEFAULT_COD_APPROVED', '_parse_date_or',
             '_is_excluded_for_electronic_net', '_spend_by_date_from_ledger', 'dashboard'}
    nodes = []
    for node in tree.body:
        name = getattr(node, 'name', None)
        if isinstance(node, ast.Assign): name = getattr(node.targets[0], 'id', None)
        if isinstance(node, ast.AnnAssign): name = getattr(node.target, 'id', None)
        if name in names:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)): node.decorator_list = []
            nodes.append(node)
    namespace = dict(vars(dash))
    namespace.update(db=db, Optional=Optional, BaseModel=BaseModel, Depends=Depends, Query=Query,
        datetime=datetime, timezone=timezone, current_user=lambda: None,
        ensure_user_settings=auth.ensure_user_settings, DEFAULT_PAYMENT_METHODS=auth.DEFAULT_PAYMENT_METHODS,
        DEFAULT_SHIPPING_COMPANIES=auth.DEFAULT_SHIPPING_COMPANIES, orders_to_parsed=orders_to_parsed,
        match_settings=match_settings, compute_balances=compute_balances,
        compute_operating_expenses_for_range=compute_operating_expenses_for_range,
        aggregate_settlements_by_provider=aggregate_settlements_by_provider, classify_14d_window=classify_14d_window,
        logger=logging.getLogger('dashboard_b_baseline'))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'server.py:dashboard-baseline', 'exec'), namespace)
    return namespace['dashboard']


class Commands(CommandListener):
    def __init__(self): self.lock = threading.Lock(); self.reset()
    def reset(self):
        self.counts = Counter(); self.queries = {}; self.docs = Counter(); self.duration_ms = 0; self.writes = []
    def started(self, event):
        with self.lock:
            self.counts[event.command_name] += 1
            if event.command_name in {'insert', 'update', 'delete', 'findAndModify', 'bulkWrite'}: self.writes.append(event.command_name)
            if event.command_name == 'find':
                cmd = event.command
                identity = {k: cmd.get(k) for k in ('find', 'filter', 'projection', 'sort', 'limit')}
                key = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
                row = self.queries.setdefault(key, {'collection': cmd['find'], 'filter_fields': sorted(cmd.get('filter', {})),
                    'projection_fields': sorted(cmd.get('projection', {})), 'count': 0})
                row['count'] += 1
    def succeeded(self, event):
        with self.lock:
            self.duration_ms += event.duration_micros / 1000
            cursor = event.reply.get('cursor', {})
            rows = cursor.get('firstBatch', cursor.get('nextBatch', []))
            self.docs[cursor.get('ns', 'non_cursor')] += len(rows)
    def failed(self, event): pass


class Work:
    def __init__(self): self.calls = defaultdict(lambda: {'calls': 0, 'rows': 0, 'cpu_ms': 0, 'wall_ms': 0})
    def wrap(self, name, function):
        def wrapped(*args, **kwargs):
            stat = self.calls[name]; stat['calls'] += 1
            if args and isinstance(args[0], (list, tuple, dict)): stat['rows'] += len(args[0]) if not isinstance(args[0], dict) else 1
            cpu, wall = time.thread_time(), time.perf_counter()
            try: return function(*args, **kwargs)
            finally:
                stat['cpu_ms'] += (time.thread_time() - cpu) * 1000
                stat['wall_ms'] += (time.perf_counter() - wall) * 1000
        return wrapped


async def populate(db, size):
    for name in await db.list_collection_names(): await db[name].delete_many({})
    await auth.ensure_user_settings(db, 'fixture-owner')
    await db.unified_orders.create_index([('user_id', 1), ('order_date', 1)])
    for collection in (dash.PRODUCTS, dash.COST_PROFILES):
        await db[collection].create_index([('user_id', 1), ('salla_product_id', 1)])
    for start in range(0, size, 1000):
        orders, products, profiles = [], [], []
        for i in range(start, min(start + 1000, size)):
            product = str(i)
            orders.append({'user_id': 'fixture-owner', 'order_number': str(1000000+i), 'order_date': '2026-10-05',
                'order_status': 'completed', 'payment_method': 'mada', 'shipping_company': 'Fixture carrier',
                'currency': 'SAR', 'total_amount': 100, 'total_amount_sar': 100, 'total_product_cost': 37.5,
                'products': [{'product_id': product, 'name': 'Fixture product '+product, 'quantity': 1, 'price': 100}],
                'raw_by_source': {'salla_direct': {'currency': 'SAR', 'source': 'snapchat'}}})
            products.append({'user_id': 'fixture-owner', 'id': 'product-'+product, 'salla_product_id': product,
                'name': 'Fixture product '+product, 'variants': [], 'cost_price': 37.5})
            profiles.append({'user_id': 'fixture-owner', 'salla_product_id': product, 'base_cost': 37.5})
        await db.unified_orders.insert_many(orders)
        await db[dash.PRODUCTS].insert_many(products)
        await db[dash.COST_PROFILES].insert_many(profiles)


@pytest.mark.asyncio
async def test_dashboard_b_baseline_real_mongo():
    uri = os.environ.get('MZ2_TEST_MONGO_URI', '')
    assert uri, 'Explicit isolated replica set required; do not skip acceptance measurements'
    parsed = parse_uri(uri)
    assert all(host in {'127.0.0.1', 'localhost', '::1'} for host, _ in parsed['nodelist'])
    assert not parsed['username'] and not parsed['password'] and not parsed['database']
    commands = Commands()
    client = AsyncIOMotorClient(uri, event_listeners=[commands], serverSelectionTimeoutMS=5000)
    assert (await client.admin.command('buildInfo'))['version'] == '8.0.12'
    assert (await client.admin.command('hello'))['setName'] == 'performancepr1'
    raw = client['dashboard_b_' + uuid4().hex]; db = ReadDB(raw)
    legacy = legacy_handler(db)
    async def current_user(): return {'id': 'fixture-owner', 'role': 'owner'}
    def owner(user): assert user['role'] == 'owner'
    router = dash.make_dashboard_v2_router(db, current_user, legacy, owner)
    endpoint = next(route.endpoint for route in router.routes if route.path == '/dashboard-v2')
    results = {'base': BASE, 'head': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        'python': platform.python_version(), 'platform': platform.platform(), 'mongo': '8.0.12', 'cases': []}
    repetitions = int(os.environ.get('DASHBOARD_PERF_REPEATS', '10'))
    sizes = [int(x) for x in os.environ.get('DASHBOARD_PERF_SIZES', '10000,50000,100000').split(',')]
    try:
        original_connect = socket.socket.connect
        def isolated_connect(sock, address):
            assert isinstance(address, tuple) and address[0] in {'127.0.0.1', 'localhost', '::1'}, 'External network forbidden'
            return original_connect(sock, address)
        with patch.object(dash, '_today_riyadh', lambda: date(2026,10,8)), patch.object(socket.socket, 'connect', isolated_connect):
            for size in sizes:
                await populate(raw, size)
                for period, start, end in [('month', '2026-10-01', '2026-10-08'), ('overlap', '2026-10-02', '2026-10-06')]:
                    samples = []
                    for iteration in range(repetitions + 1):
                        work = Work(); lag = []; stop = asyncio.Event()
                        async def heartbeat():
                            while not stop.is_set():
                                began = time.perf_counter()
                                await asyncio.sleep(.005)
                                lag.append(max(0, time.perf_counter() - began - .005) * 1000)
                        task = asyncio.create_task(heartbeat()); await asyncio.sleep(0)
                        commands.reset()
                        with ExitStack() as stack:
                            for name in ('summarize_orders_sar','hydrate_order_currency_fields','attach_projected_salla_attribution'):
                                stack.enter_context(patch.object(dash, name, work.wrap('v2.'+name, getattr(dash,name))))
                                stack.enter_context(patch.dict(legacy.__globals__, {name: work.wrap('legacy.'+name, legacy.__globals__[name])}))
                            for name in ('_index_products','calculate_mezan_v2_line_cost','_finalize_product_profit_rows','build_salla_ads_executive_breakdown'):
                                stack.enter_context(patch.object(dash,name,work.wrap('v2.'+name,getattr(dash,name))))
                            for name in ('orders_to_parsed','match_settings','compute_balances'):
                                stack.enter_context(patch.dict(legacy.__globals__, {name:work.wrap('legacy.'+name,legacy.__globals__[name])}))
                            wall, cpu = time.perf_counter(), time.thread_time()
                            try:
                                response = await endpoint(user=await current_user(), from_date=start, to_date=end, payment_methods=None, shipping_companies=None)
                                duration = (time.perf_counter()-wall)*1000; cpu_ms = (time.thread_time()-cpu)*1000
                            finally:
                                await asyncio.sleep(.006); stop.set(); await task
                        assert not commands.writes
                        assert response['totals']['total_orders'] == size
                        assert response['totals']['total_sales'] == 100 * size
                        assert response['totals']['total_product_cost'] == 37.5 * size
                        serialize_started = time.perf_counter()
                        encoded = json.dumps(response, sort_keys=True, default=str).encode()
                        sample = {'handler_ms':duration, 'python_cpu_ms':cpu_ms, 'mongo_ms':commands.duration_ms,
                            'mongo_commands':dict(commands.counts), 'docs_returned':dict(commands.docs),
                            'find_signatures':commands.queries, 'computation':dict(work.calls),
                            'event_loop_max_lag_ms':max(lag,default=0),
                            'event_loop_p95_lag_ms':sorted(lag)[math.ceil(.95*len(lag))-1],
                            'response_bytes':len(encoded), 'serialization_ms':(time.perf_counter()-serialize_started)*1000}
                        if iteration: samples.append(sample)
                        del response, encoded
                    fields = ('handler_ms','python_cpu_ms','mongo_ms','event_loop_max_lag_ms','event_loop_p95_lag_ms','response_bytes','serialization_ms')
                    summary = {field:{'p50':statistics.median(s[field] for s in samples),'p95':sorted(s[field] for s in samples)[math.ceil(.95*len(samples))-1]} for field in fields}
                    results['cases'].append({'size':size,'period':period,'samples':samples,'summary':summary})
                    Path(os.environ.get('DASHBOARD_EVIDENCE_PATH','../dashboard-b-evidence.json')).write_text(json.dumps(results,indent=2),encoding='utf-8')
                    print('DASHBOARD_BASELINE '+json.dumps({'size':size,'period':period,'summary':summary}),flush=True)
    finally:
        assert raw.name.startswith('dashboard_b_'); await client.drop_database(raw.name); client.close()
