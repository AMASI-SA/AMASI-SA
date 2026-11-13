"""Synthetic actual-module latency/parity check; no external database access.

Run from the repository root with the repository test dependencies installed:
PYTHONPATH=.:backend:backend/tests python -B \
  scripts/testing/operational_refresh_stage_analysis/measure_cost_authority_reads.py

Timing is environment-dependent. Query counts, financial-result equality,
unchanged inputs and absence of remaining tasks are asserted independently.
"""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import subprocess
import sys
from time import perf_counter
import types
from unittest.mock import patch

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

import operational_balance_sources as source_module
import order_cost_authority as current_authority
import operational_remaining_bundle_sources as current_remaining
from operational_demand_refresh import LEASES, run_visible_refresh
from review_acceptance_config_guard import AcceptanceConfigDatabase
from test_operational_balance_demand_refresh import DB as LeaseDB, Request, allowed
from test_operational_balance_sources import DB as SourceDB, START, NOW
from scripts.testing.operational_refresh_stage_analysis.measure_hotspots import source_data


BASE = '8d25f7a6fa9a310b319b753c32457e70987f7c51'
INPUTS = {
    'mezan_order_item_cost_snapshots_v2',
    'mezan_component_order_lifecycle_v1',
    'mezan_component_consumption_plans_v1',
}


def baseline(name, path):
    module = types.ModuleType(name)
    exec(compile(
        subprocess.check_output(['git', 'show', BASE + ':' + path], text=True),
        BASE + ':' + path, 'exec',
    ), module.__dict__)
    return module


async def measure(authority, remaining, n, latency):
    data = source_data(n, 0)
    before = deepcopy(data)
    sources, leases = SourceDB(data), LeaseDB()
    client = AsyncIOMotorClient('mongodb://127.0.0.1:1', connect=False)
    raw_db = client['synthetic_source_latency']
    db = AcceptanceConfigDatabase(raw_db)
    activity = {'active': 0, 'peak': 0}

    def collection(database, name):
        assert database is raw_db
        if name == LEASES:
            return leases[name]
        original = sources[name]

        class Collection:
            def find(self, *args, **kwargs):
                cursor = original.find(*args, **kwargs)

                class Cursor:
                    async def to_list(self, *args, **kwargs):
                        if name in INPUTS:
                            activity['active'] += 1
                            activity['peak'] = max(activity['peak'], activity['active'])
                        try:
                            if latency:
                                await asyncio.sleep(latency)
                            return await cursor.to_list(*args, **kwargs)
                        finally:
                            if name in INPUTS:
                                activity['active'] -= 1
                return Cursor()
        return Collection()

    try:
        with patch.object(AsyncIOMotorDatabase, '__getitem__', collection), \
                patch.dict(sys.modules, {
                    'order_cost_authority': authority,
                    'operational_remaining_bundle_sources': remaining,
                }):
            started = perf_counter()
            result = await run_visible_refresh(
                Request(), db, 'owner', 'staff', allowed,
                lambda: source_module.collect_sources(db, 'owner', START, NOW))
            elapsed = perf_counter() - started

        assert data == before
        assert activity['active'] == 0 and leases.data[LEASES] == {}
        assert len(result['orders']) == n
        assert all(row['items'][0]['cost'] == '20' for row in result['orders'])
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        return result, {
            'logical_source_reads': len(sources.reads),
            'elapsed_ms': round(elapsed * 1000, 3),
            'authority_peak_reads': activity['peak'],
            'lease_operations': len(leases.writes),
            'pending_tasks': 0,
        }
    finally:
        client.close()


async def main():
    old_authority = baseline('baseline_cost_authority', 'backend/order_cost_authority.py')
    old_remaining = baseline('baseline_remaining_bundles', 'backend/operational_remaining_bundle_sources.py')
    # Warm imports and both exact module paths before the measured workload.
    await measure(old_authority, old_remaining, 1, 0)
    await measure(current_authority, current_remaining, 1, 0)
    for n in (1000, 5000):
        old, before = await measure(old_authority, old_remaining, n, .010)
        new, after = await measure(current_authority, current_remaining, n, .010)
        assert new == old
        assert before['lease_operations'] == after['lease_operations']
        print(json.dumps({
            'actual_collector_and_visible_request': True,
            'production_database_adapter': 'AcceptanceConfigDatabase',
            'base': BASE,
            'orders': n,
            'synthetic_cursor_latency_ms': 10,
            'baseline': before,
            'candidate': after,
            'exact_result_equal': True,
            'result_sha256': sha256(json.dumps(new, sort_keys=True).encode()).hexdigest(),
            'known_cost_per_order': '20',
            'unchanged_sources': True,
        }))


if __name__ == '__main__':
    asyncio.run(main())
