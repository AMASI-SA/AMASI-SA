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
from pathlib import Path
from unittest.mock import patch

ARM = os.environ['DASHBOARD_ADOPTION_ARM']
GOLDEN = Path(os.environ['DASHBOARD_ADOPTION_GOLDEN'])
records = {}
calls = Counter()
current_test = None
sequence = 0


def pytest_runtest_setup(item):
    global current_test, sequence
    current_test, sequence = item.nodeid, 0


def pytest_collection_modifyitems(session, config, items):
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
    expected = json.loads(GOLDEN.read_text()) if ARM == 'runtime' else None

    async def recorded(*args, **kwargs):
        global sequence
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
        records[key] = value
        if expected is not None:
            assert value == expected[key], key
        return result, metrics

    cases.invoke = recorded
    if ARM != 'runtime':
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
        if ARM == 'runtime':
            assert set(records) == set(json.loads(GOLDEN.read_text()))
            assert all(calls[name] > 0 for name in ('parser', 'index', 'cost', 'advertising', 'forced_index_checkpoints'))
            output.with_suffix('.calls.json').write_text(json.dumps(calls, indent=2))
