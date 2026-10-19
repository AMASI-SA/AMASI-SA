"""Only replace the pure advertising function with optional diagnostic timers."""
from contextlib import contextmanager
from dashboard_b_cost_cooperative_experiment import candidate as cost_candidate
from dashboard_b_ads_instrumentation import Recorder, instrumented
import dashboard_v2_ads_executive as ads_module


@contextmanager
def candidate(db,scope=None,mode='coarse',probe=None,capture=None,**kwargs):
    probe=probe if probe is not None else Recorder()
    assert mode in {'current','coarse','fine'}
    with cost_candidate(db,scope,cost_budget_ms=5,**kwargs) as (factory,legacy):
        fn=ads_module.build_salla_ads_executive_breakdown if mode=='current' else instrumented(probe,fine=mode=='fine')
        def observed(orders,ads):
            if capture is not None:
                capture(orders,ads)
            if mode=='current':
                with probe.span('whole_breakdown',len(orders)):
                    return fn(orders,ads)
            return fn(orders,ads)
        factory.__globals__['build_salla_ads_executive_breakdown']=observed
        yield factory,legacy
