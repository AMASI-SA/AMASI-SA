"""Current native path: synthetic loopback Mongo only, installed live wrappers."""
import json
import time
import os
import asyncio
import sys
import tracemalloc
from pathlib import Path
import ctypes
from copy import deepcopy
from collections import defaultdict
import pytest
import httpx
from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorClientSession, AsyncIOMotorCursor
import supplier_receiving_routes as r
from test_supplier_native_invoice_v2 import env, identities, mapping, receiving_session, OWNER, ACTOR
from test_supplier_refresh_wrapper import installed_live_cost_support

def current_rss():
    if os.name != 'nt':
        return None
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'rss', 'page_peak', 'page',
                'nonpage_peak', 'nonpage', 'pagefile', 'pagefile_peak')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    row = Counters(); row.cb = ctypes.sizeof(row)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(row), row.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return row.rss

@pytest.mark.asyncio
@pytest.mark.parametrize('count,failure', [(n, False) for n in [2, 4, 6, 10, 17, 20, 24, 50, 75, 100, 150, 200]] + [(200, True)])
async def test_native_close_profile(env, monkeypatch, installed_live_cost_support, count, failure):
    db, commands = env
    await identities(db)
    await mapping(db)
    session, payload = await receiving_session(db)
    await mapping(db, kind='service', source_id='service')
    await db[r.RESOURCES].update_one({'id': 'service'}, {'$set': {
        'name': 'Synthetic engraving', 'unit_cost': 5, 'requires_preparation': True}})
    await db[r.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id': OWNER,
        'salla_product_id': 'product', 'resource_id': 'service', 'quantity': 1,
        'supplier_invoice_required': True})
    await db[r.BINDINGS].insert_one({'user_id': OWNER, 'salla_product_id': 'product',
        'option_id': 'size', 'option_name': 'size', 'value_id': 'large', 'value_name': 'large',
        'mode': 'direct', 'direct_amount': 2})
    for collection in [r.PIECES, r.RECEIVING_EVENTS, r.PIECE_EVENTS]:
        await db[collection].update_many({'user_id': OWNER}, {'$set': {
            'options_normalized': {'size': 'large'}, 'options': {'size': 'large'}}})
    payload['invoice_lines'][0]['product_unit_price_halalas'] = 10200
    payload['invoice_lines'][0]['services'] = [{'service_id': 'service', 'unit_price_halalas': 500}]
    payload['confirmed_total_halalas'] = 10700
    piece = await db[r.PIECES].find_one({'piece_id': 'piece'}, {'_id': 0})
    event = await db[r.RECEIVING_EVENTS].find_one({'id': 'scan-event'}, {'_id': 0})
    for i in range(1, count):
        p = {**deepcopy(piece), 'piece_id': 'piece-' + str(i), 'receipt_event_id': 'scan-' + str(i), 'unit_index': i+1}
        e = {**deepcopy(event), **p, 'id': p['receipt_event_id']}
        await db[r.PIECES].insert_one(deepcopy(p))
        await db[r.RECEIVING_EVENTS].insert_one(deepcopy(e))
        await db[r.PIECE_EVENTS].insert_one(deepcopy(e))
        payload['invoice_lines'][0]['piece_ids'].append(p['piece_id'])
    payload['confirmed_total_halalas'] *= count
    diverse = os.environ.get('ISOLATED_DIVERSE_PRODUCTS') == '1'
    if diverse:
        for pno in range(8):
            pid, sid = f'mixed-{pno}', f'service-{pno % 3}'
            await db[r.PRODUCTS].insert_one({'user_id': OWNER, 'id': pid,
                'mezan_product_id': pid, 'salla_product_id': pid, 'status': 'active',
                'name': pid, 'variants': [{'id': f'variant-{v}', 'sku': f'{pid}-{v}'} for v in range(2)]})
            await db[r.COST_PROFILES].insert_one({'user_id': OWNER, 'salla_product_id': pid,
                'base_cost': 100+pno, 'variant_costs': {f'variant-{v}': 100+pno+v*10 for v in range(2)}})
            await db[r.RESOURCES].update_one({'user_id': OWNER, 'id': sid}, {'$set': {
                'status': 'active', 'kind': 'service', 'name': sid, 'unit_cost': 5+pno%3,
                'requires_preparation': True}}, upsert=True)
            await db[r.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id': OWNER,
                'salla_product_id': pid, 'resource_id': sid, 'quantity': 1, 'supplier_invoice_required': True})
            for option in range(3):
                await db[r.BINDINGS].insert_one({'user_id': OWNER, 'salla_product_id': pid,
                    'option_id': 'size', 'option_name': 'size', 'value_id': f'choice-{option}',
                    'value_name': f'choice-{option}', 'mode': 'direct', 'direct_amount': pno%3+1})
            for v in range(2):
                await mapping(db, source_id=pid, variant=f'variant-{v}')
        for s in range(3): await mapping(db, kind='service', source_id=f'service-{s}')
        payload['invoice_lines'] = []
        payload['confirmed_total_halalas'] = 0
        for i in range(count):
            piece_id = 'piece' if i == 0 else f'piece-{i}'
            pno, variant, option = i % 8, (i//8) % 2, (i//16) % 3
            pid, sid = f'mixed-{pno}', f'service-{pno%3}'
            values = {'product_id': pid, 'product_name': pid, 'variant_id': f'variant-{variant}',
                'sku': f'{pid}-{variant}', 'options': {'size': f'choice-{option}'},
                'options_normalized': {'size': f'choice-{option}'}}
            for collection in [r.PIECES, r.RECEIVING_EVENTS, r.PIECE_EVENTS]:
                await db[collection].update_one({'piece_id': piece_id}, {'$set': values})
            price, service_price = (100+pno+variant*10+pno%3+1)*100, (5+pno%3)*100
            payload['invoice_lines'].append({'piece_ids': [piece_id], 'product_unit_price_halalas': price,
                'services': [{'service_id': sid, 'unit_price_halalas': service_price}]})
            payload['confirmed_total_halalas'] += price+service_price
    expected_by_piece = {pid: line for line in payload['invoice_lines'] for pid in line['piece_ids']}
    identity_projection = {'_id': 0, 'piece_id': 1, 'product_id': 1, 'sku': 1,
        'variant_id': 1, 'options': 1, 'options_normalized': 1, 'product_options_snapshot': 1}
    original_identities = await db[r.PIECES].find({}, identity_projection).sort('piece_id', 1).to_list(None)
    await db[r.SESSIONS].update_one({'id': session['id']}, {'$set': {'scan_count': count}})
    concurrent_mode = os.environ.get('ISOLATED_CONCURRENT_CLOSE', '')
    peer_payload = deepcopy(payload)
    if concurrent_mode == 'two_sessions' and not failure:
        peer = await db[r.SESSIONS].find_one({'id': session['id']}, {'_id': 0})
        peer.update(id='peer-session', client_request_id='peer-request', reference='SR-PEER',
                    opened_by='peer-employee', opened_by_name='Synthetic peer')
        await db.users.insert_one({'id': 'peer-employee', 'role': 'employee', 'created_by': OWNER,
            'is_active': True, 'accounting_permissions': ['accounting.purchases.post', 'accounting.settlements.view']})
        await db[r.SESSIONS].insert_one(peer)
        for collection in [r.PIECES, r.RECEIVING_EVENTS, r.PIECE_EVENTS]:
            rows = await db[collection].find({}).to_list(None)
            for row in rows:
                row.pop('_id', None)
                row['piece_id'] += '-peer'
                row['receipt_event_id'] += '-peer'
                row['supplier_receiving_session_id'] = 'peer-session'
                if 'id' in row: row['id'] += '-peer'
                if 'session_id' in row: row['session_id'] = 'peer-session'
            await db[collection].insert_many(rows)
        for line in peer_payload['invoice_lines']:
            line['piece_ids'] = [pid+'-peer' for pid in line['piece_ids']]
    installed_live_cost_support()
    # Capture all document state, including accounting, before the failed close.
    async def snapshot():
        return {name: await db[name].find({}).sort('_id', 1).to_list(None)
                for name in await db.list_collection_names()}
    before = await snapshot() if failure else None
    if failure:
        original_verify = r.verify_persisted_supplier_invoice
        async def fail_after_persisted_verification(*args, **kwargs):
            await original_verify(*args, **kwargs)
            raise RuntimeError('isolated failure after invoice linkage and verification')
        monkeypatch.setattr(r, 'verify_persisted_supplier_invoice', fail_after_persisted_verification)
        if os.environ.get('ISOLATED_FAIL_AFTER_BULK') == '1':
            original_bulk = AsyncIOMotorCollection.bulk_write
            async def fail_after_batch(collection, *args, **kwargs):
                result = await original_bulk(collection, *args, **kwargs)
                if collection.name == r.PIECES and kwargs.get('session') is not None:
                    raise RuntimeError('isolated failure after first 100-piece batch')
                return result
            monkeypatch.setattr(AsyncIOMotorCollection, 'bulk_write', fail_after_batch)
    times, calls = defaultdict(float), defaultdict(int)
    collection_calls = defaultdict(int)
    phase = ['close']
    latency_ms = int(os.environ.get('ISOLATED_MONGO_OPERATION_DELAY_MS', '0'))
    assert 0 <= latency_ms <= 100
    def trace(owner, name):
        original = getattr(owner, name)
        async def wrapped(*args, **kwargs):
            key = phase[0] + ':' + name
            start = time.perf_counter()
            calls[key] += 1
            if owner in (AsyncIOMotorCollection, AsyncIOMotorCursor):
                collection = args[0] if owner is AsyncIOMotorCollection else args[0].collection
                collection_calls[phase[0] + ':' + collection.name + ':' + name] += 1
            try:
                if latency_ms and owner in (AsyncIOMotorCollection, AsyncIOMotorCursor):
                    await asyncio.sleep(latency_ms / 1000)
                return await original(*args, **kwargs)
            finally:
                times[key] += time.perf_counter()-start
        monkeypatch.setattr(owner, name, wrapped)
    for name in ['_recent_session_events', '_supplier_live_piece_services', '_supplier_service_catalog',
                 '_supplier_product_reference_price', 'post_native_invoice', 'verify_persisted_supplier_invoice']:
        trace(r, name)
    for name in ['find_one', 'update_one', 'find_one_and_update', 'insert_one', 'insert_many', 'bulk_write']:
        trace(AsyncIOMotorCollection, name)
    trace(AsyncIOMotorCursor, 'to_list')
    trace(AsyncIOMotorClientSession, 'commit_transaction')
    group = r.build_supplier_receiving_invoice
    def timed_group(*a, **kw):
        start = time.perf_counter()
        try: return group(*a, **kw)
        finally: times['close:grouping'] += time.perf_counter()-start
    monkeypatch.setattr(r, 'build_supplier_receiving_invoice', timed_group)
    async def current(request: Request):
        if concurrent_mode == 'two_sessions' and 'peer-session' in request.url.path:
            return {**deepcopy(ACTOR), '_session_client': r.MOBILE_APP_CLIENT,
                '_mobile_owner_id': OWNER, '_mobile_actor_id': 'peer-employee',
                '_mobile_actor_name': 'Synthetic peer', '_mobile_app_permissions': [
                    r.MOBILE_MY_PRODUCTS_PAGE_PERMISSION, r.MOBILE_EDIT_PRODUCT_PRICE_PERMISSION,
                    r.MOBILE_EDIT_SERVICE_PRICE_PERMISSION]}
        return deepcopy(ACTOR)
    app = FastAPI(); app.include_router(r.make_supplier_receiving_router(db, current))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic.test') as client:
        # Inclusive wall time between finalize source lines, including awaited
        # database work. Only this synthetic request is active in the process.
        # These pinned boundaries belong to the deployed d0a3ff source.
        spans = {}
        source = Path(r.__file__).read_text(encoding='utf-8').splitlines()
        close_start = next(i for i,l in enumerate(source) if 'async def close_session(' in l)
        close_start = next(i for i in range(close_start, len(source)) if 'async def finalize(' in source[i])
        def boundary(text):
            return next(i+1 for i in range(close_start, len(source)) if text in source[i])
        stages = [(boundary(text), name) for text, name in [
            ('scans = await _recent_session_events(', 'transaction_revalidation'),
            ('actual_count = len(scans)', 'live_product_services_options_costs'),
            ('service_catalog = await _supplier_service_catalog(', 'piece_validation'),
            ('invoice_id = ', 'grouping_and_total_validation'),
            ('invoice["price_changes"] = await apply_supplier_invoice_price_changes(', 'invoice_assembly'),
            ('await db[SUPPLIER_INVOICES].insert_one(', 'financial_post'),
            ('invoice_summary = {', 'invoice_piece_linkage'),
            ('await verify_persisted_supplier_invoice(', 'session_flags_and_audit'),
        ]]
        assert [n for n, _ in stages] == sorted(n for n, _ in stages), stages
        def stage(line):
            for limit, name in stages:
                if line < limit: return name
            return 'persisted_verification'
        def trace_lines(frame, event, arg):
            if frame.f_code.co_name == 'finalize' and frame.f_code.co_filename == r.__file__ and event == 'line':
                now = time.perf_counter()
                previous = spans.get(id(frame))
                if previous:
                    times['stage:' + stage(previous[0])] += now - previous[1]
                spans[id(frame)] = (frame.f_lineno, now)
            return trace_lines
        old_trace = sys.gettrace()
        rss_samples = [current_rss()]
        loop = asyncio.get_running_loop()
        handle = None
        def sample_rss():
            nonlocal handle
            rss_samples.append(current_rss())
            handle = loop.call_later(0.05, sample_rss)
        sample_rss()
        light_profile = os.environ.get('ISOLATED_LIGHT_PROFILE') == '1'
        if not light_profile:
            tracemalloc.start()
        start = time.perf_counter()
        if not light_profile:
            sys.settrace(trace_lines)
        try:
            close_path = '/supplier-receiving-v1/sessions/'+session['id']+'/close'
            if os.environ.get('ISOLATED_OBSERVE_15S_READBACK') == '1':
                # A timed-out client does not prove the server transaction stopped.
                pending = asyncio.create_task(client.post(close_path, json=payload))
                done, _ = await asyncio.wait({pending}, timeout=15)
                if not done:
                    early = await client.get('/supplier-receiving-v1/sessions/'+session['id'])
                    print('EARLY_READBACK', json.dumps({'http': early.status_code,
                        'status': early.json().get('session', {}).get('status'),
                        'invoice_count': await db[r.SUPPLIER_INVOICES].count_documents({}),
                        'close_done': pending.done()}))
                    assert early.status_code == 200
                    assert early.json()['session']['status'] == 'open'
                    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
                response = await pending
            else:
                if concurrent_mode and not failure:
                    peer_path = '/supplier-receiving-v1/sessions/peer-session/close' if concurrent_mode == 'two_sessions' else close_path
                    response, replay = await asyncio.gather(client.post(close_path, json=payload),
                                                           client.post(peer_path, json=peer_payload))
                    assert replay.status_code == 200, replay.text
                    first_id, second_id = response.json()['supplier_invoice']['id'], replay.json()['supplier_invoice']['id']
                    assert (first_id != second_id) if concurrent_mode == 'two_sessions' else (first_id == second_id)
                    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == (2 if concurrent_mode == 'two_sessions' else 1)
                    assert all(replay.json()['session'][key] is True for key in
                        ['financial_integrity_verified','liability_created','financial_invoice_created'])
                else:
                    response = await client.post(close_path, json=payload)
        finally:
            if handle: handle.cancel()
            sys.settrace(old_trace)
            memory_peak = None
            if not light_profile:
                _, memory_peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
        times['close_total'] = time.perf_counter()-start
        if failure:
            assert response.status_code == 503, response.text
            phase[0] = 'rollback_validation'
            after = await snapshot()
            # Index-only collection creation is not a partial document write.
            assert {k: v for k, v in after.items() if v} == {k: v for k, v in before.items() if v}
            print('NATIVE_PROFILE', json.dumps({'count': count, 'failure_injected': True,
                'rollback': True, 'http': response.status_code, 'python_memory_peak_bytes': memory_peak,
                'rss_peak_bytes': max(x or 0 for x in rss_samples),
                'seconds': dict(times), 'calls': dict(calls), 'collection_calls': dict(collection_calls)}))
            return
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['session']['status'] == 'closed'
        assert all(body['session'][key] is True for key in ['financial_integrity_verified','liability_created','financial_invoice_created'])
        phase[0] = 'readback'
        start = time.perf_counter()
        readback = await client.get('/supplier-receiving-v1/sessions/'+session['id'])
        invoice = await client.get('/supplier-receiving-v1/invoices/'+body['supplier_invoice']['id'])
        times['readback_total'] = time.perf_counter()-start
        assert readback.status_code == invoice.status_code == 200
        assert invoice.json()['supplier_invoice']['total_halalas'] == payload['confirmed_total_halalas']
        assert readback.json()['session']['status'] == 'closed'
        saved = invoice.json()['supplier_invoice']
        # The requested amounts must come from live costs, not be silently
        # accepted as owner-authorized manual price changes in this fixture.
        assert saved.get('price_changes', []) == [], saved.get('price_changes')
        lines = saved['lines']
        ids = [piece_id for line in lines for piece_id in line['piece_ids']]
        assert len(ids) == len(set(ids)) == count
        assert set(ids) == set(expected_by_piece)
        assert sum(line['quantity'] for line in lines) == count
        assert sum(line['total_halalas'] for line in lines) == payload['confirmed_total_halalas']
        for line in lines:
            for pid in line['piece_ids']:
                expected = expected_by_piece[pid]
                assert line['product_unit_price_halalas'] == expected['product_unit_price_halalas']
                assert {(s['service_id'], s['unit_price_halalas']) for s in line['services']} == {
                    (s['service_id'], s['unit_price_halalas']) for s in expected['services']}
        assert all(readback.json()['session'][key] is True for key in
                   ['financial_integrity_verified', 'liability_created', 'financial_invoice_created'])
        assert await db[r.PIECES].find({'piece_id': {'$in': list(expected_by_piece)}},
            identity_projection).sort('piece_id', 1).to_list(None) == original_identities
        if os.environ.get('ISOLATED_EXPECT_BOUNDED') == '1' and not concurrent_mode:
            unique_prices = len({json.dumps({k:v for k,v in row.items() if k != 'piece_id'}, sort_keys=True)
                                 for row in original_identities})
            assert calls['close:_supplier_product_reference_price'] <= 2*unique_prices
            assert collection_calls[f'close:{r.PIECES}:find_one'] == 0
            assert calls['close:bulk_write'] <= 4*((count+99)//100)
    print('NATIVE_PROFILE',json.dumps({'count': count, 'simulated_operation_delay_ms': latency_ms,
        'closed': True, 'verified': True, 'unique_linked_pieces': len(ids),
        'python_memory_peak_bytes': memory_peak, 'failure_injected': False,
        'rss_peak_bytes': max(x or 0 for x in rss_samples), 'rss_initial_bytes': rss_samples[0],
        'diverse': diverse, 'concurrent': concurrent_mode,
        'light_profile': light_profile,
        'seconds': dict(times), 'calls': dict(calls), 'collection_calls': dict(collection_calls)},sort_keys=True))
