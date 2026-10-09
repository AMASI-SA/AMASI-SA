"""Cost experiment only, AFTER phase1 proved the outer loop is largest.

Parser5ms is a fixed, unchanged experimental prerequisite in every arm.
No change to indexing/finalization/sorting, DB reads or loop statements.
"""
import ast
from contextlib import contextmanager
from dashboard_b_cooperative_experiment import Budget, candidate as parser_candidate
from dashboard_b_cost_instrumentation import Recorder, instrumented


def cost_builder(probe=None, budget_ms=None, observer=None, hook=None):
    probe = probe if probe is not None else Recorder()
    if budget_ms is None:
        return instrumented(probe, fine=False)
    def transform(node):
        matches=0
        body=[]
        for n in node.body:
            if isinstance(n,ast.With) and isinstance(n.items[0].context_expr,ast.Call) and n.items[0].context_expr.args[0].value=='cost_profit_loop':
                matches+=1
                loop=n.body[0]
                assert isinstance(loop,ast.For) and ast.unparse(loop.iter)=='orders'
                assert not any(isinstance(x,ast.Await) for x in ast.walk(loop))
                loop.body.insert(0,ast.parse('await _cost_budget.checkpoint()').body[0])
                body.extend(ast.parse('_cost_budget = _new_cost_budget()\n_cost_complete = False').body)
                body.append(ast.Try(body=[loop,*ast.parse('_cost_complete = True').body],handlers=[],orelse=[],
                    finalbody=ast.parse('_cost_budget.finish(_cost_complete)').body))
            else:
                body.append(n)
        assert matches==1
        node.body=body
        return node
    return instrumented(probe,fine=False,transform=transform,
        extra={'_new_cost_budget':lambda:Budget(budget_ms,observer,hook)})


@contextmanager
def candidate(db, scope=None, budget_ms=5, observer=None, hook=None,
              cost_budget_ms=None, cost_probe=None, cost_observer=None, cost_hook=None):
    # The parser is fixed, NOT a new parser benchmark or parser modification.
    assert budget_ms==5
    with parser_candidate(db,scope,budget_ms=5,observer=observer,hook=hook) as (factory,legacy):
        factory.__globals__['build_mezan_v2_product_cost']=cost_builder(
            cost_probe,cost_budget_ms,cost_observer,cost_hook)
        yield factory,legacy
