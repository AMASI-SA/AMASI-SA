"""Private indexing experiment. Parser5/cost5/ads1 remain unchanged.

Only checkpoint between whole products and, subsequently, whole name groups.
All original accumulator statements, reference relationships and reads remain.
"""
import ast
import inspect
import time
from contextlib import contextmanager
from unittest.mock import patch

import product_catalog_cost_resolution as catalog
import dashboard_b_cost_instrumentation as cost_instrumentation
from dashboard_b_ads_cooperative_experiment import candidate as ads_candidate
from dashboard_b_cooperative_experiment import Budget
from dashboard_b_request_scope_experiment import compile_function


def index_builder(budget_ms=None, observer=None, hook=None):
    original = catalog.index_current_catalog_products
    if budget_ms is None:
        async def current(products):
            start, cpu = time.perf_counter(), time.thread_time()
            completed = False
            try:
                result = original(products)
                completed = True
                return result
            finally:
                if observer:
                    observer(dict(slices=[dict(start=start, end=time.perf_counter(),
                        cpu_ms=(time.thread_time()-cpu)*1000,
                        records=len(products) if isinstance(products, list) else 0)],
                        yields=0, completed=completed))
        return current
    assert budget_ms > 0
    node = ast.parse(inspect.getsource(original)).body[0]
    assert isinstance(node, ast.FunctionDef)
    loops = [n for n in node.body if isinstance(n, ast.For)]
    assert len(loops) == 2
    assert ast.unparse(loops[0].iter) == 'products'
    assert ast.unparse(loops[1].iter) == 'names.items()'
    assert not any(isinstance(n, ast.Await) for n in ast.walk(node))
    node = ast.AsyncFunctionDef(**vars(node))
    node.args.kwonlyargs.append(ast.arg(arg='_checkpoint'))
    node.args.kw_defaults.append(None)
    for loop in loops:
        loop.body.insert(0, ast.parse('await _checkpoint()').body[0])
    reduce = compile_function(node, dict(original.__globals__))

    async def cooperative(products):
        budget = Budget(budget_ms, observer, hook)
        completed = False
        try:
            result = await reduce(products, _checkpoint=budget.checkpoint)
            completed = True
            return result
        finally:
            budget.finish(completed)
    return cooperative


@contextmanager
def candidate(db, scope=None, index_budget_ms=None, index_observer=None, index_hook=None, **kwargs):
    compiled = 0
    def await_index(node, namespace):
        nonlocal compiled
        if node.name == 'build_mezan_v2_product_cost':
            matches = 0
            body = []
            for n in node.body:
                if (isinstance(n, ast.With) and isinstance(n.items[0].context_expr, ast.Call)
                        and n.items[0].context_expr.args[0].value == 'product_indexing'):
                    matches += 1
                    assert len(n.body) == 1
                    assignment = n.body[0]
                    assert isinstance(assignment, ast.Assign)
                    assert ast.unparse(assignment.value) == '_index_products(products)'
                    assignment.value = ast.Await(value=assignment.value)
                    # A synchronous span must NOT enclose the new awaits; the
                    # observer measures disjoint active slices instead.
                    body.append(assignment)
                else:
                    body.append(n)
            assert matches == 1, 'Index call-site source drift'
            node.body = body
            namespace['_index_products'] = index_builder(index_budget_ms, index_observer, index_hook)
            compiled += 1
        return compile_function(node, namespace)

    with patch.object(cost_instrumentation, 'compile_function', await_index):
        with ads_candidate(db, scope, ads_budget_ms=1, **kwargs) as pair:
            assert compiled == 1
            # The patch is used only during private AST compilation; its target
            # is never invoked by a running request.
            yield pair
