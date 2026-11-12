#!/usr/bin/env python3
"""Synthetic-only measurements of current refresh code; no database or network.

Run with PYTHONPATH=backend:backend/tests. Plain-dict timings are separate from
instrumented operation counts. No implementation is substituted or optimized.
"""
import argparse
import asyncio
from copy import deepcopy
from hashlib import sha256
import inspect
import json
from pathlib import Path
import platform
import subprocess
import sys
import types
from time import perf_counter

from fastapi import HTTPException
import pytest

import operational_balance_sources as sources
import operational_balance_store as store
from operational_demand_refresh import LEASES, run_visible_refresh
from test_operational_balance_sources import DB as SourceDB, START, NOW, order, row
from test_operational_balance_demand_refresh import DB as LeaseDB, Request, allowed
from test_operational_balance_activity_cash_projection import state_fixture, receipt


ROOT = Path(__file__).resolve().parents[3]


def configure_sources(baseline_ref=None):
    global sources, MATCH_LINE, MATCH_CODE, INDEX_CODE, SOURCE_TEXT
    if baseline_ref:
        SOURCE_TEXT = subprocess.check_output(
            ['git', 'show', baseline_ref + ':backend/operational_balance_sources.py'], cwd=ROOT, text=True)
        module = types.ModuleType('operational_balance_sources_baseline')
        exec(compile(SOURCE_TEXT, baseline_ref + ':operational_balance_sources.py', 'exec'), module.__dict__)
        sources = module
    else:
        SOURCE_TEXT = (ROOT/'backend/operational_balance_sources.py').read_text()
    MATCH_LINE = next((i for i, text in enumerate(SOURCE_TEXT.splitlines(), 1)
                       if 'matching = [p for p in pieces' in text), None)
    MATCH_CODE = next((code for code in sources.collect_sources.__code__.co_consts
                      if inspect.iscode(code) and code.co_firstlineno == MATCH_LINE), None)
    INDEX_CODE = getattr(getattr(sources, 'index_preparation_pieces', None), '__code__', None)


configure_sources()


class CountedPiece(dict):
    visits = 0
    trigger = None

    def get(self, key, *args):
        caller = sys._getframe(1)
        matching = (caller.f_code is INDEX_CODE or caller.f_code is MATCH_CODE or
                    MATCH_LINE is not None and caller.f_code is sources.collect_sources.__code__ and
                    MATCH_LINE <= caller.f_lineno <= MATCH_LINE + 1)
        if key == 'order_number' and matching:
            type(self).visits += 1
            if type(self).trigger is not None:
                callback, type(self).trigger = type(self).trigger, None
                callback()
        return super().get(key, *args)


class CountedMovements(list):
    visits = 0

    def __iter__(self):
        for item in super().__iter__():
            type(self).visits += 1
            yield item


def source_data(order_count, piece_count, *, counted=False):
    orders = []
    for i in range(order_count):
        value = order(id=f'order-{i}', reference_id=f'number-{i}',
                      items=[{'id': 'line', 'product_id': 'p1', 'quantity': 1}])
        value['order_number'] = f'number-{i}'
        orders.append(value)
    piece_type = CountedPiece if counted else dict
    # Deliberately unrelated, valid-shaped pieces. Each full scan must reject
    # every row and leave the source calculation's known cost unchanged.
    pieces = [piece_type(row(id=f'piece-{i}', order_number=f'outside-{i}',
                            order_item_id=f'salla:outside-{i}:line', unit_index=1))
              for i in range(piece_count)]
    return {'unified_orders': orders, 'mezan_preparation_pieces_v1': pieces,
            'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')]}


async def source_case(order_count, piece_count, *, counted=False):
    data = source_data(order_count, piece_count, counted=counted)
    before = deepcopy(data)
    db = SourceDB(data)
    CountedPiece.visits, CountedPiece.trigger = 0, None
    started = perf_counter()
    result = await sources.collect_sources(db, 'owner', START, NOW)
    elapsed = perf_counter() - started
    assert len(result['orders']) == order_count
    assert all(len(value['items']) == 1 and value['items'][0]['cost'] == '20'
               and value['items'][0]['quantity'] == '1' for value in result['orders'])
    assert data == before
    measured = CountedPiece.visits if counted else None
    expected = piece_count if INDEX_CODE is not None else order_count * piece_count
    if counted:
        assert measured == expected
    return {'orders': order_count, 'pieces': piece_count, 'elapsed_seconds': round(elapsed, 6),
            'instrumented': counted, 'measured_piece_visits': measured,
            'piece_operation': 'index_build' if INDEX_CODE is not None else 'item_matching',
            'expected_piece_visits': expected,
            'in_memory_source_reads': len(db.reads), 'unchanged_inputs': True,
            'known_cost_per_order': '20', 'output_orders': len(result['orders'])}


async def source_cancel():
    data = source_data(200, 20000)
    source_db, lease_db, request = SourceDB(data), LeaseDB(), Request()
    trace = {'cost_calls': 0}
    original = sources.cost_components

    class Database:
        def __getitem__(self, name):
            return lease_db[name] if name == LEASES else source_db[name]

    def heartbeat():
        trace['heartbeat_at'] = perf_counter()

    def cost(*args):
        result = original(*args)
        trace['cost_calls'] += 1
        if trace['cost_calls'] == 1:
            trace['queued_at'] = perf_counter()
            request.disconnect()
            asyncio.get_running_loop().call_soon(heartbeat)
        return result

    # One wrapper per item, no per-piece counter/frame access in this latency
    # sample. The separate count run establishes the scan multiplication.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sources, 'cost_components', cost)
        try:
            await run_visible_refresh(request, Database(), 'owner', 'staff', allowed,
                                      lambda: sources.collect_sources(source_db, 'owner', START, NOW))
            raise AssertionError('Synthetic disconnect did not cancel')
        except HTTPException as failure:
            assert failure.status_code == 499
            ended = perf_counter()
            assert 0 < trace['cost_calls'] < 200
            assert lease_db.data[LEASES] == {}
            assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            return {'instrumentation': 'one callback per item; plain piece dictionaries',
                    'status': failure.status_code, 'cost_calls_before_join': trace['cost_calls'],
                    'all_possible_items': 200, 'pieces_per_matching_scan': 20000,
                    'disconnect_to_return_ms': round((ended-trace['queued_at'])*1000, 3),
                    'heartbeat_delay_ms': round((trace['heartbeat_at']-trace['queued_at'])*1000, 3),
                    'pending_tasks': 0, 'lease_released': True}


def bank_state(saved_count, *, counted=False):
    state = state_fixture()
    values = [receipt(f'saved-{i}', kind='collection') for i in range(saved_count)]
    state['movements'] = CountedMovements(values) if counted else values
    return state


def append_and_audit(state, index):
    item = receipt(f'new-{index}', kind='collection')
    state['movements'].append(item)
    store.audit(state, 'operational_order_worker', 'order_bank_credited', None, item,
                'synthetic measurement', 'mz2', NOW)
    return state['audit'][-1]['financial_activity_snapshots'][0]['account_balance_after']


def bank_case(saved_count, new_count, *, counted=False):
    state = bank_state(saved_count, counted=counted)
    CountedMovements.visits = 0
    started = perf_counter()
    actual = [append_and_audit(state, i) for i in range(new_count)]
    elapsed = perf_counter() - started
    assert actual == [f'{10000 + saved_count + i + 1}.00' for i in range(new_count)]
    expected = new_count * saved_count + new_count * (new_count + 1) // 2
    measured = CountedMovements.visits if counted else None
    if counted:
        assert measured == expected
    return {'saved_movements': saved_count, 'new_movements': new_count,
            'elapsed_seconds': round(elapsed, 6), 'instrumented': counted,
            'measured_movement_visits': measured, 'expected_movement_visits': expected,
            'first_after_balance': actual[0], 'last_after_balance': actual[-1],
            'all_sequential_balances_exact': True}


async def bank_cancel():
    import operational_financial_activity as activity
    from test_operational_balance_bank_refresh_plan import CountedDB, isolate_sources, order as bank_order, visible_refresh
    db, request = CountedDB(), Request()
    state = bank_state(20000)
    state['owner_id'] = state['_id'] = 'owner'
    state.update(revision=1)
    db.data[store.STATES]['owner'] = deepcopy(state)
    saved = deepcopy(state)
    trace = {'calls': 0}
    original = activity._cash_only_projection

    def project(*args):
        trace['calls'] += 1
        trace['queued_at'] = perf_counter()
        request.disconnect()
        asyncio.get_running_loop().call_soon(lambda: trace.update(heartbeat_at=perf_counter()))
        try:
            return original(*args)
        finally:
            trace['projection_done_at'] = perf_counter()

    with pytest.MonkeyPatch.context() as patch:
        isolate_sources(patch, [bank_order('new-a', 'A'), bank_order('new-b', 'B')])
        patch.setattr(activity, '_cash_only_projection', project)
        try:
            await visible_refresh(db, request)
            raise AssertionError('Synthetic disconnect did not cancel')
        except HTTPException as failure:
            ended = perf_counter()
            assert failure.status_code == 499 and trace['calls'] == 1
            assert db.data[store.STATES]['owner'] == saved
            assert len(db.data[store.OPERATION_CLAIMS]) == 1
            assert db.data[LEASES] == {}
            assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            return {'saved_movements': 20000, 'status': failure.status_code,
                    'audit_projections_completed_after_disconnect_queued': trace['calls'],
                    'single_projection_ms': round((trace['projection_done_at']-trace['queued_at'])*1000, 3),
                    'heartbeat_delay_ms': round((trace['heartbeat_at']-trace['queued_at'])*1000, 3),
                    'disconnect_to_return_ms': round((ended-trace['queued_at'])*1000, 3),
                    'retained_claim_intents': 1, 'financial_state_unchanged': True,
                    'pending_tasks': 0, 'lease_released': True}


async def main(stage, baseline_ref=None):
    configure_sources(baseline_ref)
    evidence = {'synthetic_only': True, 'stage': stage, 'python': platform.python_version(),
                'source_variant': baseline_ref or 'working_tree',
                'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'source_hashes': {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
                                  for path in (ROOT/'backend/operational_balance_sources.py',
                                               ROOT/'backend/operational_financial_activity.py')},
                'measurements': []}
    evidence['source_hashes']['backend/operational_balance_sources.py'] = sha256(SOURCE_TEXT.encode()).hexdigest()
    if INDEX_CODE is not None:
        evidence['source_hashes']['backend/operational_piece_index.py'] = sha256(
            (ROOT/'backend/operational_piece_index.py').read_bytes()).hexdigest()
    if stage == 'sources':
        await source_case(5, 10)  # Exclude first-import cost from reported timings.
        for order_count, piece_count in [(500, 0), (500, 5000), (1000, 10000),
                                        (2000, 20000), (5000, 20000), (20000, 20000)]:
            result = await source_case(order_count, piece_count)
            evidence['measurements'].append(result)
            print(json.dumps(result), file=sys.stderr, flush=True)
            if result['elapsed_seconds'] > 20:
                break
        evidence['operation_count'] = await source_case(500, 2000, counted=True)
        evidence['cancellation'] = await source_cancel()
    else:
        bank_case(5, 2)
        for saved_count, new_count in [(500, 100), (2000, 100), (2000, 500),
                                      (5000, 500), (5000, 1000)]:
            result = bank_case(saved_count, new_count)
            evidence['measurements'].append(result)
            print(json.dumps(result), file=sys.stderr, flush=True)
            if result['elapsed_seconds'] > 20:
                break
        evidence['operation_count'] = bank_case(1000, 200, counted=True)
        evidence['cancellation'] = await bank_cancel()
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('sources', 'bank'))
    parser.add_argument('--baseline-ref', help='Read the exact old collector from this local Git ref')
    arguments = parser.parse_args()
    asyncio.run(main(arguments.stage, arguments.baseline_ref))
