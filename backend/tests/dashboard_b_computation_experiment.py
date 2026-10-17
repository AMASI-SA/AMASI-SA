"""Test-only computation sharing. All database reads remain unchanged.

No values are shared between Legacy and V2. Legacy reuse requires the same
ordered object references from its own filtered dataset. V2 reuse requires
its existing same-list month/selected-period alias. Other inputs recompute.
Deep copies preserve independent result containers. No hashing or DB cache.
"""
import ast
import copy
import inspect
from contextlib import contextmanager
import dashboard_v2_routes as dash
from test_dashboard_b_baseline_mongo import legacy_handler
from dashboard_b_request_scope_experiment import compile_function


@contextmanager
def candidate(db, scope):
    legacy = legacy_handler(db)
    source = ast.parse(open('server.py', encoding='utf8').read())
    node = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'dashboard')
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
    legacy = compile_function(node, {**legacy.__globals__, '_copy': copy.deepcopy})
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
    factory = compile_function(factory_node, {**vars(dash), '_copy': copy.deepcopy})
    yield factory, legacy
