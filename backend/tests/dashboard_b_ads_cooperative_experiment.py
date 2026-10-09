"""Private advertising experiment; unchanged parser5/cost5 in every arm.

Only insert a checkpoint between complete orders. No database, accumulator,
currency, provider finalizer or production source changes.
"""
import ast
import inspect
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import dashboard_v2_ads_executive as ads
import dashboard_b_computation_experiment as computation
from dashboard_b_cooperative_experiment import Budget
from dashboard_b_cost_cooperative_experiment import candidate as cost_candidate
from dashboard_b_request_scope_experiment import compile_function


def ads_builder(budget_ms=None, observer=None, hook=None):
    original = ads.build_salla_ads_executive_breakdown
    if budget_ms is None:
        async def current(orders, advertising):
            return original(orders, advertising)
        return current
    assert budget_ms > 0
    node = ast.parse(inspect.getsource(original)).body[0]
    assert isinstance(node, ast.FunctionDef)
    loops = [n for n in node.body if isinstance(n, ast.For)]
    assert len(loops) == 2
    assert ast.unparse(loops[0].iter) == 'orders'
    assert ast.unparse(loops[1].iter) == 'PROVIDER_ORDER'
    assert not any(isinstance(n, ast.Await) for n in ast.walk(node))
    node = ast.AsyncFunctionDef(**vars(node))
    node.args.kwonlyargs.append(ast.arg(arg='_checkpoint'))
    node.args.kw_defaults.append(None)
    # Before the next order also covers the existing unattributed `continue`.
    # The final order and four-provider finalization complete without reordering.
    loops[0].body.insert(0, ast.parse('await _checkpoint()').body[0])
    reduce = compile_function(node, dict(original.__globals__))

    async def cooperative(orders, advertising):
        budget = Budget(budget_ms, observer, hook)
        completed = False
        try:
            result = await reduce(orders, advertising, _checkpoint=budget.checkpoint)
            completed = True
            return result
        finally:
            budget.finish(completed)
    return cooperative


@contextmanager
def candidate(db, scope=None, ads_budget_ms=None, ads_observer=None, ads_hook=None, **kwargs):
    # Intercept only private factory compilation, retaining the established
    # identity-proven computation substitutions verbatim in the existing builder.
    compiled_factories = 0
    def await_advertising(node, namespace):
        nonlocal compiled_factories
        if node.name == 'make_dashboard_v2_router':
            calls = 0
            class AwaitAdvertising(ast.NodeTransformer):
                def visit_Call(self, n):
                    nonlocal calls
                    n = self.generic_visit(n)
                    if isinstance(n.func, ast.Name) and n.func.id == 'build_salla_ads_executive_breakdown':
                        calls += 1
                        return ast.Await(value=n)
                    return n
            node = AwaitAdvertising().visit(node)
            assert calls == 1, 'Advertising call-site source drift'
            compiled_factories += 1
        return compile_function(node, namespace)

    with ExitStack() as stack:
        with patch.object(computation, 'compile_function', await_advertising):
            factory, legacy = stack.enter_context(cost_candidate(db, scope, cost_budget_ms=5, **kwargs))
        assert compiled_factories == 1
        factory.__globals__['build_salla_ads_executive_breakdown'] = ads_builder(
            ads_budget_ms, ads_observer, ads_hook)
        yield factory, legacy
