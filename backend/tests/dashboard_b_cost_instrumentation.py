"""Test-only phase attribution; compile unchanged cost statements with timers.

Fine child spans overlap enclosing spans. Never add them to their parents.
Coarse mode measures outer blocks without per-line instrumentation, to expose
profiler overhead. No yielding, query changes, formulas or runtime edits here.
"""
import ast
import inspect
import time
from collections import defaultdict
from contextlib import contextmanager

import dashboard_v2_routes as dash
from dashboard_b_request_scope_experiment import compile_function


class Recorder:
    def __init__(self):
        self.stats = defaultdict(lambda: dict(calls=0, records=0, wall_ms=0., cpu_ms=0., max_wall_ms=0., max_cpu_ms=0.))
        self.blocks = []

    @contextmanager
    def span(self, name, records=0):
        start, cpu = time.perf_counter(), time.thread_time()
        try:
            yield
        finally:
            wall = (time.perf_counter()-start)*1000
            elapsed = (time.thread_time()-cpu)*1000
            r = self.stats[name]
            r['calls'] += 1
            r['records'] += records
            r['wall_ms'] += wall
            r['cpu_ms'] += elapsed
            r['max_wall_ms'] = max(r['max_wall_ms'], wall)
            r['max_cpu_ms'] = max(r['max_cpu_ms'], elapsed)
            if name in {'cost_profit_loop', 'product_indexing', 'profit_finalization'}:
                self.blocks.append(dict(name=name,start=start,end=start+wall/1000,cpu_ms=elapsed,records=records))


def block(name, body, count='0'):
    return ast.With(items=[ast.withitem(context_expr=ast.parse(f"_cost_probe.span({name!r}, {count})", mode='eval').body)], body=body)


def assigned(node, name):
    return (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id==name for t in node.targets)
            or isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id==name)


def instrumented(recorder, fine=True, transform=None, extra=None):
    final = ast.parse(inspect.getsource(dash._finalize_product_profit_rows)).body[0]
    row_loop = next(i for i,n in enumerate(final.body) if isinstance(n, ast.For))
    sort = next(i for i,n in enumerate(final.body) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
                and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='sort')
    assert sort==row_loop+1
    final.body = final.body[:row_loop] + [block('profit_row_DTO_mapping', [final.body[row_loop]], 'len(rows)'),
        block('profit_sorting', [final.body[sort]], 'len(items)'),
        block('profit_summary_finalize', final.body[sort+1:], 'len(items)')]
    finalize = compile_function(final, {**dash._finalize_product_profit_rows.__globals__, '_cost_probe':recorder})

    node = ast.parse(inspect.getsource(dash.build_mezan_v2_product_cost)).body[0]
    outer = next(n for n in node.body if isinstance(n,ast.For) and isinstance(n.iter,ast.Name) and n.iter.id=='orders')
    if fine:
        item = next(n for n in outer.body if isinstance(n,ast.For) and isinstance(n.iter,ast.Name) and n.iter.id=='items')
        lookup = next(i for i,n in enumerate(item.body) if assigned(n,'product'))
        cost = next(i for i,n in enumerate(item.body) if assigned(n,'result'))
        assert cost==lookup+2
        item.body = item.body[:lookup] + [block('cost_lookup', item.body[lookup:cost], '1'),
            block('line_cost_calculation',[item.body[cost]],'1'),
            block('line_bookkeeping_DTO',item.body[cost+1:],'1')]
        policy = next(i for i,n in enumerate(outer.body) if assigned(n,'adjusted_total'))
        profit = next(i for i,n in enumerate(outer.body) if isinstance(n,ast.For) and isinstance(n.iter,ast.Name) and n.iter.id=='order_product_lines')
        outer.body = outer.body[:policy] + [block('cost_policy_and_totals',outer.body[policy:profit],'1'),
            block('profit_accumulation',[outer.body[profit]],'len(order_product_lines)')] + outer.body[profit+1:]

    # Keep all await expressions untouched. Time only synchronous regions.
    result = []
    for n in node.body:
        if n is outer:
            result.append(block('cost_profit_loop',[n],'len(orders)'))
        elif isinstance(n,ast.Assign) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='_index_products':
            result.append(block('product_indexing',[n],'len(products)'))
        elif isinstance(n,ast.Assign) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='_finalize_product_profit_rows':
            result.append(block('profit_finalization',[n],'len(product_profit_rows)'))
        elif isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='sort':
            result.append(block('missing_product_sorting',[n],'len(missing_product_rows)'))
        elif isinstance(n,ast.For) and isinstance(n.iter,ast.Call) and isinstance(n.iter.func,ast.Attribute) and isinstance(n.iter.func.value,ast.Name) and n.iter.func.value.id=='missing_products':
            result.append(block('missing_product_DTO',[n],'len(missing_products)'))
        elif assigned(n,'product_ids'):
            result.append(block('catalog_id_mapping',[n],'len(products)'))
        elif assigned(n,'resource_ids'):
            result.append(block('resource_id_mapping',[n],'len(option_bindings)+len(product_bindings)'))
        elif assigned(n,'profile_map'):
            result.append(block('profile_mapping',[n],'len(profiles)'))
        elif assigned(n,'resources'):
            result.append(block('resource_mapping',[n],'len(resource_rows)'))
        elif isinstance(n,ast.For) and isinstance(n.iter,ast.Name) and n.iter.id in {'option_bindings','product_bindings'}:
            result.append(block('binding_mapping',[n],f'len({n.iter.id})'))
        elif isinstance(n,ast.Return):
            result.append(block('response_construction',[n],'len(product_rows)'))
        else:
            result.append(n)
    node.body = result
    if transform is not None:
        node = transform(node)
    namespace = {**dash.build_mezan_v2_product_cost.__globals__,
        '_cost_probe':recorder, '_finalize_product_profit_rows':finalize, **(extra or {})}
    if fine:
        # Separate the catalog's shallow DTO enrichment from its enclosing
        # index construction. This child is not additive to product_indexing.
        from product_catalog_cost_resolution import index_current_catalog_products, enrich_current_salla_cost
        def enrich(row):
            with recorder.span('catalog_DTO_enrichment',1):
                return enrich_current_salla_cost(row)
        index_node = ast.parse(inspect.getsource(index_current_catalog_products)).body[0]
        index = compile_function(index_node, {**index_current_catalog_products.__globals__, 'enrich_current_salla_cost':enrich})
        wrapper = ast.parse(inspect.getsource(dash._index_products)).body[0]
        namespace['_index_products'] = compile_function(wrapper, {**dash._index_products.__globals__, 'index_current_catalog_products':index})
    return compile_function(node, namespace)
