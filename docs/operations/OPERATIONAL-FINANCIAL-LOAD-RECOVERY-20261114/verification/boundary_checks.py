"""Independent cancellation and generic transaction boundary checks."""
from copy import deepcopy
import asyncio
import json
import random
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import parity_harness as p
from operational_reconciliation_state import ReconciliationMap
import operational_demand_refresh as demand


def journal_sequences():
    rng=random.Random(191899)
    checks=0
    for sequence in range(100):
        expected={f'capture:{i}':{'order_id':f'order-{i%3}','amount':str(i)} for i in range(12)}
        actual=ReconciliationMap(expected,lambda:None,index_facts=True)
        saved=[]
        for _ in range(100):
            op=rng.choice(['set','del','pop','clear','update','setdefault','checkpoint','rollback','ior'])
            key=rng.choice(['capture:','refund:tabby:','irrelevant:'])+str(rng.randrange(20))
            value={'order_id':'order-'+str(rng.randrange(3)),'amount':str(rng.randrange(100))}
            if op=='set': expected[key]=value; actual[key]=value
            elif op=='del' and key in expected: del expected[key]; del actual[key]
            elif op=='pop': assert expected.pop(key,None)==actual.pop(key,None)
            elif op=='clear': expected.clear(); actual.clear()
            elif op=='update': expected.update({key:value}); actual.update({key:value})
            elif op=='setdefault': assert expected.setdefault(key,value)==actual.setdefault(key,value)
            elif op=='ior': expected|={key:value}; actual|={key:value}
            elif op=='checkpoint': saved.append((actual.checkpoint(),expected.copy()))
            elif op=='rollback' and saved:
                checkpoint,expected=saved.pop()
                actual.rollback(checkpoint)
            assert list(actual.items())==list(expected.items()),(sequence,op)
            for family in ['capture:','refund:']:
                for oid in ['order-0','order-1','order-2']:
                    assert list(actual.facts_for(family,oid))==[(k,v) for k,v in expected.items() if k.startswith(family) and v['order_id']==oid]
            checks+=1
    return checks


class CountingContext:
    def __init__(self,stop=None): self.calls=0; self.stop=stop
    def require_connected(self):
        self.calls+=1
        if self.stop is not None and self.calls>=self.stop: raise asyncio.CancelledError


def cancellation():
    source={'orders':p.good_orders(50),'fee_policies':[p.policy()]}
    state=p.baseline.reconcile(p.initial(),source,p.NOW)
    before_state,before_source=p.serial(state),p.serial(source)
    counts={}
    for name,module in [('baseline',p.baseline),('candidate',p.candidate)]:
        counter=CountingContext()
        token=demand._CURRENT.set(counter)
        try: result=module.reconcile(state,source,p.NOW)
        finally: demand._CURRENT.reset(token)
        counts[name]={'checkpoints':counter.calls,'cancelled_cases':0}
        for stop in sorted({1,2,20,counter.calls//2,counter.calls-1}):
            token=demand._CURRENT.set(CountingContext(stop))
            try:
                try: module.reconcile(state,source,p.NOW)
                except asyncio.CancelledError: counts[name]['cancelled_cases']+=1
                else: raise AssertionError((name,stop,'cancellation swallowed'))
            finally: demand._CURRENT.reset(token)
            assert p.serial(state)==before_state and p.serial(source)==before_source
        assert all(type(result['engine'][field]) is dict for field in ['obligations','facts','provider_reports','cod_reports'])
        rows=result['engine']['obligations'].values()
        assert sum(p.Decimal(r['confirmed']) for r in rows if r['kind']=='provider')==p.Decimal('50000')
        assert sum(p.Decimal(r['expected']) for r in rows if r['kind']=='supplier')==p.Decimal('10000')
        assert sum(p.Decimal(r['expected']) for r in rows if r['kind']=='shipping')==p.Decimal('862.50')
        assert not result.get('movements')
    return counts


def targeted_cancel():
    results={}
    source={'orders':p.good_orders(50),'fee_policies':[p.policy()]}
    state=p.baseline.reconcile(p.initial(),source,p.NOW)
    rollback_state,rollback_source,_=p.held_delete_case()
    for target,hit,s,src in [('__init__',1,state,source),('rollback',1,rollback_state,rollback_source),
                            ('rollback',2,rollback_state,rollback_source)]:
        class AtFrame:
            calls=0
            def require_connected(self):
                frame=sys._getframe(1)
                while frame:
                    if frame.f_code.co_filename.endswith('operational_reconciliation_state.py') and frame.f_code.co_name==target:
                        self.calls+=1
                        if self.calls==hit: raise asyncio.CancelledError
                        break
                    frame=frame.f_back
        stop=AtFrame()
        before_s,before_src=p.serial(s),p.serial(src)
        token=demand._CURRENT.set(stop)
        try:
            try: p.candidate.reconcile(s,src,p.NOW)
            except asyncio.CancelledError: results[f'{target}:{hit}']='cancelled'
            else: raise AssertionError((target,hit,'target never cancelled'))
        finally: demand._CURRENT.reset(token)
        assert p.serial(s)==before_s and p.serial(src)==before_src
    return results


if __name__=='__main__':
    result={'source_hashes':p.source_hashes,'journal_operations_checked':journal_sequences(),'cancellation':cancellation(),
        'targeted_cancellation':targeted_cancel(),
        'plain_output_maps':True,'analytical_totals_and_no_bank_movement':True}
    (HERE/'boundary-result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
