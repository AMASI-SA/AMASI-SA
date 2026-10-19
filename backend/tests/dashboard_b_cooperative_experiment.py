"""Test-only ordered parser experiment. Never imported by runtime.

Compile the actual reducers with only cooperative checkpoints inserted. Their
accumulators, iteration order, finalizers, rounding and exception behavior stay
verbatim. The currency pass INSIDE the parser shares its checkpoint budget;
other currency summaries and all database reads are untouched.
"""
import ast
import asyncio
import inspect
import time
from contextlib import contextmanager

import orders_db
import order_currency
from dashboard_b_computation_experiment import candidate as computation_candidate
from dashboard_b_request_scope_experiment import compile_function


class Budget:
    def __init__(self, milliseconds, observer=None, hook=None):
        assert milliseconds > 0
        self.seconds = milliseconds / 1000
        self.observer, self.hook = observer, hook
        self.start = time.perf_counter()
        self.cpu = time.thread_time()
        self.slices = []
        self.rows = 0
        self.yields = 0
        self.active = True

    def record(self):
        end, cpu = time.perf_counter(), time.thread_time()
        self.slices.append({'start':self.start, 'end':end,
                            'cpu_ms':(cpu-self.cpu)*1000, 'records':self.rows})
        self.rows = 0

    async def checkpoint(self):
        self.rows += 1
        if time.thread_time() - self.cpu < self.seconds:
            return
        self.record()
        self.active = False
        self.yields += 1
        if self.hook:
            await self.hook()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        def resume():
            if not future.done():
                future.set_result(None)
        handle = loop.call_soon(resume)
        try:
            await future
        finally:
            handle.cancel()
        self.start, self.cpu = time.perf_counter(), time.thread_time()
        self.active = True

    def finish(self, completed):
        if self.active:
            self.record()
        if self.observer:
            self.observer({'slices':self.slices, 'yields':self.yields,
                           'completed':completed})


def _reducer(function, summary=None):
    node = ast.parse(inspect.getsource(function)).body[0]
    assert isinstance(node, ast.FunctionDef)
    node = ast.AsyncFunctionDef(**vars(node))
    node.args.kwonlyargs.append(ast.arg(arg='_checkpoint'))
    node.args.kw_defaults.append(None)
    loops = 0
    class Checkpoints(ast.NodeTransformer):
        def visit_For(self, n):
            nonlocal loops
            # Both reducers have exactly one top-level input-order loop.
            assert isinstance(n.iter, ast.Name) and n.iter.id == 'orders'
            loops += 1
            n.body.insert(0, ast.parse('await _checkpoint()').body[0])
            return n

        def visit_Call(self, n):
            n = self.generic_visit(n)
            if summary is not None and isinstance(n.func, ast.Name) and n.func.id == 'summarize_orders_sar':
                n.func.id = '_cooperative_summary'
                n.keywords.append(ast.keyword(arg='_checkpoint', value=ast.Name(id='_checkpoint', ctx=ast.Load())))
                return ast.Await(value=n)
            return n
    node = Checkpoints().visit(node)
    assert loops == 1, 'Reducer source drift'
    return compile_function(node, {**function.__globals__, '_cooperative_summary':summary})


def parser(budget_ms=5, observer=None, hook=None):
    summary = _reducer(order_currency.summarize_orders_sar)
    reduce = _reducer(orders_db.orders_to_parsed, summary)
    async def run(orders):
        budget = Budget(budget_ms, observer, hook)
        completed = False
        try:
            result = await reduce(orders, _checkpoint=budget.checkpoint)
            completed = True
            return result
        finally:
            budget.finish(completed)
    return run


@contextmanager
def candidate(db, scope=None, budget_ms=5, observer=None, hook=None):
    # Preserve the accepted computation-only candidate exactly; add await only
    # at parser calls in its private compiled Legacy handler.
    with computation_candidate(db, scope) as (factory, legacy):
        from pathlib import Path
        node = ast.parse(Path('server.py').read_text(encoding='utf8'))
        node = next(n for n in node.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'dashboard')
        # Reproduce the existing single identity-based reuse substitution by
        # taking its AST from the same explicit expression, assert source shape.
        replacements = 0
        class Reuse(ast.NodeTransformer):
            def visit_Assign(self, n):
                nonlocal replacements
                if any(isinstance(t, ast.Name) and t.id == 'parsed_elec' for t in n.targets):
                    assert ast.unparse(n.value) == 'orders_to_parsed(electronic_orders_included)'
                    replacements += 1
                    n.value = ast.parse('(_copy(parsed_all) if len(electronic_orders_included) == len(all_orders) and all(a is b for a, b in zip(electronic_orders_included, all_orders)) else orders_to_parsed(electronic_orders_included))', mode='eval').body
                return n
        node = Reuse().visit(node)
        assert replacements == 1
        calls = 0
        class AwaitParser(ast.NodeTransformer):
            def visit_Call(self, n):
                nonlocal calls
                n = self.generic_visit(n)
                if isinstance(n.func, ast.Name) and n.func.id == 'orders_to_parsed':
                    calls += 1
                    return ast.Await(value=n)
                return n
        node = AwaitParser().visit(node)
        assert calls == 2
        cooperative = parser(budget_ms, observer, hook)
        legacy = compile_function(node, {**legacy.__globals__, 'orders_to_parsed':cooperative})
        yield factory, legacy
