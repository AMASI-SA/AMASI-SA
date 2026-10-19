"""Test-only candidate. Never imported by runtime; no process/request cache.

Compile the unchanged function bodies with three explicit substitutions. Keep
query construction, filters, cost formulas and response assembly verbatim.
"""
import ast
import asyncio
import copy
import inspect
import json
from contextlib import contextmanager

import dashboard_v2_routes as dash
from test_dashboard_b_baseline_mongo import legacy_handler


class RequestScope:
    def __init__(self, db):
        self.db = db
        self.loads = {}
        self.hits = 0

    async def orders(self, query, attribution):
        projection = dict(dash.SALLA_RAW_CURRENCY_PROJECTION)
        if attribution:
            projection.update(dash.SALLA_RAW_ATTRIBUTION_PROJECTION)
        # Preserve exact query/projection, date range, tenant and row order.
        # No subset/superset reuse across overlapping ranges.
        key = json.dumps([query, projection], sort_keys=True, default=str)
        if key not in self.loads:
            async def load():
                rows = await self.db.unified_orders.find(query, {'_id': 0, 'raw_by_source': 0}).to_list(100000)
                if rows:
                    proof = await self.db.unified_orders.find(query, projection).to_list(100000)
                    dash.hydrate_order_currency_fields(rows, proof)
                    if attribution:
                        dash.attach_projected_salla_attribution(rows, proof)
                return rows
            self.loads[key] = asyncio.create_task(load())
        else:
            self.hits += 1
        # Both consumers are read-only after hydration (self-heal is false).
        # Equivalence tests fingerprint these rows before consumer use and after
        # response completion. No consumer-specific filtering mutates this list.
        return await self.loads[key]

    async def close(self):
        for task in self.loads.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.loads.values(), return_exceptions=True)
        self.loads.clear()


def replace_load(body, variable, query, attribution):
    indexes = [i for i, n in enumerate(body) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == variable for t in n.targets)
               and isinstance(n.value, ast.Await)]
    assert len(indexes) == 1, 'Source drift: order-loading boundary changed'
    i = indexes[0]
    assert isinstance(body[i+1], ast.If) and isinstance(body[i+1].test, ast.Name)
    assert body[i+1].test.id == variable
    body[i:i+2] = ast.parse(f'{variable} = await _request_scope.orders({query}, {attribution})').body


def compile_function(node, namespace):
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, '<dashboard-request-scope-experiment>', 'exec'), namespace)
    return namespace[node.name]


@contextmanager
def candidate(db, scope):
    legacy = legacy_handler(db)
    source = ast.parse(open('server.py', encoding='utf8').read())
    node = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'dashboard')
    replace_load(node.body, 'all_orders', 'orders_q', 'True')
    replacements = 0
    class ParsedReuse(ast.NodeTransformer):
        def visit_Assign(self, n):
            nonlocal replacements
            if any(isinstance(t, ast.Name) and t.id == 'parsed_elec' for t in n.targets):
                assert ast.unparse(n.value) == 'orders_to_parsed(electronic_orders_included)'
                replacements += 1
                n.value = ast.parse('(_copy(parsed_all) if len(electronic_orders_included) == len(all_orders) and all(a is b for a, b in zip(electronic_orders_included, all_orders)) else orders_to_parsed(electronic_orders_included))', mode='eval').body
            return n
    node = ParsedReuse().visit(node)
    assert replacements == 1
    legacy = compile_function(node, {**legacy.__globals__, '_request_scope': scope, '_copy': copy.deepcopy})
    filtered_node = ast.parse(inspect.getsource(dash._filtered_orders)).body[0]
    replace_load(filtered_node.body, 'orders', 'query', 'include_marketing_attribution')
    filtered = compile_function(filtered_node, {**vars(dash), '_request_scope': scope})

    # Apply just the identity-proven current-month summary reuse to a private
    # router factory. The resource-stage decorator and permission gate remain.
    factory_node = ast.parse(inspect.getsource(dash.make_dashboard_v2_router)).body[0]
    replacements = 0
    class SummaryReuse(ast.NodeTransformer):
        def visit_Assign(self, n):
            nonlocal replacements
            if any(isinstance(t, ast.Name) and t.id == 'sales_currency' for t in n.targets):
                assert ast.unparse(n.value) == 'summarize_orders_sar(orders)'
                replacements += 1
                n.value = ast.parse('(_copy(month_sales) if month_orders is orders else summarize_orders_sar(orders))', mode='eval').body
            return n
    factory_node = SummaryReuse().visit(factory_node)
    assert replacements == 1
    factory = compile_function(factory_node, {**vars(dash), '_filtered_orders': filtered, '_copy': copy.deepcopy})
    yield factory, legacy
