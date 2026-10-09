"""Test-only attribution timers; no yielding, reduction, DB or runtime edits."""
import ast
import inspect
import time
from collections import defaultdict
from contextlib import contextmanager
import dashboard_v2_ads_executive as ads_module
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
            end, cpu_end = time.perf_counter(), time.thread_time()
            wall, elapsed = (end-start)*1000, (cpu_end-cpu)*1000
            row = self.stats[name]
            row['calls'] += 1
            row['records'] += records
            row['wall_ms'] += wall
            row['cpu_ms'] += elapsed
            row['max_wall_ms'] = max(row['max_wall_ms'], wall)
            row['max_cpu_ms'] = max(row['max_cpu_ms'], elapsed)
            if name in {'whole_breakdown', 'order_traversal', 'provider_finalization', 'total_calculations', 'response_DTO'}:
                self.blocks.append(dict(name=name, start=start, end=end, cpu_ms=elapsed, records=records))


def block(name, body, count='0'):
    return ast.With(items=[ast.withitem(context_expr=ast.parse(f"_ads_probe.span({name!r}, {count})", mode='eval').body)], body=body)


def instrumented(recorder, fine=False):
    function = ads_module.build_salla_ads_executive_breakdown
    node = ast.parse(inspect.getsource(function)).body[0]
    loops = [(i,n) for i,n in enumerate(node.body) if isinstance(n,ast.For)]
    assert len(loops)==2
    (oi,order_loop),(pi,provider_loop)=loops
    assert ast.unparse(order_loop.iter)=='orders'
    assert ast.unparse(provider_loop.iter)=='PROVIDER_ORDER'
    namespace = {**function.__globals__, '_ads_probe':recorder}
    if fine:
        assert ast.unparse(order_loop.body[0])=='provider = resolve_salla_ad_platform(order)'
        assert ast.unparse(order_loop.body[1])=='sales = _salla_sales(order)'
        order_loop.body = [block('attribution_matching',[order_loop.body[0]],'1'),
            block('currency_conversion',[order_loop.body[1]],'1'),
            block('order_aggregation',order_loop.body[2:],'1')]
        resolve = ast.parse(inspect.getsource(ads_module.resolve_salla_ad_platform)).body[0]
        assert ast.unparse(resolve.body[1])=='provider = canonical_ad_platform(order)'
        resolve.body = [resolve.body[0],block('canonical_attribution',resolve.body[1:3],'1'),
            block('fallback_matching',resolve.body[3:],'1')]
        namespace['resolve_salla_ad_platform']=compile_function(resolve,{**ads_module.resolve_salla_ad_platform.__globals__,'_ads_probe':recorder})
        dto = next(i for i,n in enumerate(provider_loop.body) if isinstance(n,ast.Assign)
            and isinstance(n.targets[0],ast.Subscript) and ast.unparse(n.targets[0])=='providers[provider]')
        provider_loop.body=[block('provider_calculations',provider_loop.body[:dto],'1'),
            block('provider_DTO',[provider_loop.body[dto]],'1'),
            block('provider_total_accumulation',provider_loop.body[dto+1:],'1')]
    assert isinstance(node.body[-1],ast.Return)
    node.body=[node.body[0],block('input_accumulator_preparation',node.body[1:oi],'len(PROVIDER_ORDER)'),
        block('order_traversal',[order_loop],'len(orders)'),
        block('provider_preparation',node.body[oi+1:pi],'len(PROVIDER_ORDER)'),
        block('provider_finalization',[provider_loop],'len(PROVIDER_ORDER)'),
        block('total_calculations',node.body[pi+1:-1],'len(PROVIDER_ORDER)'),
        block('response_DTO',[node.body[-1]],'len(PROVIDER_ORDER)')]
    compiled=compile_function(node,namespace)
    def run(orders, ads):
        with recorder.span('whole_breakdown',len(orders)):
            return compiled(orders,ads)
    return run
