"""Independent full-state and scaling comparison against immutable PR1918 base."""
from copy import deepcopy
from decimal import Decimal, getcontext
import importlib.util
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
BASE = '23cc06da003c18c0f3fc6c2e749aad1326666166'
source = subprocess.check_output(['git', 'show', f'{BASE}:backend/operational_balance_engine.py'], cwd=ROOT, text=True)
page_source = subprocess.check_output(['git', 'show', f'{BASE}:backend/operational_order_page_payment.py'], cwd=ROOT, text=True)
baseline_page = types.ModuleType('independent_baseline_order_page')
baseline_page.__file__=str(HERE/'baseline_operational_order_page_payment.py')
exec(compile(page_source,baseline_page.__file__,'exec'),baseline_page.__dict__)
baseline = types.ModuleType('independent_baseline_engine')
baseline.__file__ = str(HERE / 'baseline_operational_balance_engine.py')
previous_page_module=sys.modules.get('operational_order_page_payment')
sys.modules['operational_order_page_payment']=baseline_page
try:
    exec(compile(source, baseline.__file__, 'exec'), baseline.__dict__)
finally:
    if previous_page_module is None: sys.modules.pop('operational_order_page_payment',None)
    else: sys.modules['operational_order_page_payment']=previous_page_module
import operational_balance_engine as candidate
from test_operational_balance_engine import initial, order, policy, NOW, START
source_hashes = {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
    'backend/operational_balance_engine.py','backend/operational_reconciliation_state.py',
    'backend/operational_order_page_payment.py') if (ROOT/p).is_file()}


def serial(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def run(module, state, sources):
    before_state, before_sources = serial(state), serial(sources)
    getcontext().clear_flags()
    try:
        output = module.reconcile(state, sources, NOW)
        outcome = ('result', serial(output))
    except Exception as exc:
        output = None
        outcome = ('error', type(exc).__name__, str(exc))
    flags = sorted(key.__name__ for key, value in getcontext().flags.items() if value)
    assert serial(state) == before_state, 'input state mutated'
    assert serial(sources) == before_sources, 'input sources mutated'
    return outcome, flags, output


def assert_pair(name, state, sources):
    expected, ef, _ = run(baseline, deepcopy(state), deepcopy(sources))
    actual, af, output = run(candidate, deepcopy(state), deepcopy(sources))
    if (actual, af) != (expected, ef):
        (HERE / 'first-difference.json').write_text(json.dumps({
            'name': name, 'state': state, 'sources': sources,
            'expected': expected, 'actual': actual, 'baseline_flags': ef, 'candidate_flags': af,
        }, ensure_ascii=False, indent=2, default=str))
        raise AssertionError(f'full state/order/exception/Decimal mismatch: {name}')
    return output


def payment(oid, **kw):
    return {'provider': 'tabby', 'gross': '1000', 'captures': [
        {'id': 'capture-' + oid, 'amount': '1000', 'captured_at': NOW}], **kw}


def good_orders(count=5):
    return [order(id=f'order-{i}', payment=payment(f'order-{i}')) for i in range(count)]


def page_orders(count=5):
    rows=[]
    for i in range(count):
        oid=f'page-{i}'
        row=order(id=oid,order_number=1000+i)
        row['payment']={'provider':'tabby','gross':'1000','order_page_balance':{
            'owner_id':'owner','order_id':oid,'order_number':1000+i,'currency':'SAR','provider':'tabby',
            'source':'canonical_salla_order_page','gross':'1000','paid':'1000','remaining':'0',
            'source_updated_at':row['updated_at']}}
        rows.append(row)
    return rows


def held_delete_case():
    o = order(id='held', carrier={'id': 'imile', 'party_type': 'courier', 'cost': None,
        'fee_components': [{'id': 'base_shipping', 'amount': '17.25', 'complete': True},
                           {'id': 'cod_commission', 'amount': '5', 'complete': True}]})
    surrounding = [order(id='before'), o, order(id='after')]
    state = baseline.reconcile(initial(), {'orders': surrounding}, NOW)
    delivered = '2026-10-07T11:00:00+00:00'
    bad = deepcopy(o)
    bad.update(status='delivered', delivered_at=delivered, updated_at=NOW, order_number=123,
        delivery_event={'schema':'operational_delivery_event_v1', 'id':'delivery-held',
            'order_id':'held', 'order_number':123, 'order_created_at':o['created_at'],
            'delivered_at':delivered, 'currency':'SAR', 'party_id':'imile','party_type':'courier'},
        payment={'provider':None, 'gross':'1000'})
    bad['carrier']['fee_recognition_held'] = True
    return state, {'orders':[bad]}, ['shipping:held:base_shipping','shipping:held:cod_commission']


def cases():
    p = policy(refund_fee_treatment='recalculate')
    sources = {'orders':good_orders(), 'fee_policies':[p]}
    saved = baseline.reconcile(initial(), sources, NOW)
    yield 'empty', initial(), {}
    yield 'fresh', initial(), sources
    yield 'replay', saved, sources
    yield 'saved without source', saved, {}
    for field, value in [('created_at', '2026-10-04T12:00:00+00:00'), ('updated_at','2026-10-06T09:01:00+00:00'),
            ('items', []), ('carrier', None), ('currency','QAR'), ('status','cancelled'),
            ('cod_amount', 'bad'), ('cod_amount', None), ('cod_amount', True), ('payment', {})]:
        changed = deepcopy(sources)
        changed['orders'][2][field] = value
        yield f'changed {field}={value}', saved, changed
    for capture in [
            [{'id':'new-valid','amount':'20','captured_at':NOW},{'id':'new-bad','amount':'bad','captured_at':NOW}],
            [{'id':'dup','amount':'30','captured_at':NOW},{'id':'dup','amount':'40','captured_at':NOW}],
            [{'id':'too-much','amount':'1001','captured_at':NOW}],
            [{'id':'capture-order-0','amount':'1000','captured_at':NOW}]]:
        changed = {'orders':[order(id='nested', status='delivered', payment=payment('nested', captures=capture))], 'fee_policies':[p]}
        yield 'provider rollback ' + str(capture), saved, changed
    for status in ['requested', 'executed', 'settled', 'Refund Executed', 'Settled']:
        changed=deepcopy(sources)
        changed['orders'][2]['payment']['refunds']=[{'id':'refund2','amount':'100','status':status}]
        yield 'refund '+status, saved, changed
    with_unused = deepcopy(saved)
    with_unused['engine']['facts']['capture:unused']={'order_id':'unused','provider':'tabby','amount':'not-money'}
    with_unused['engine']['facts']['refund:tabby:unused']={'order_id':'unused','amount':'not-money'}
    yield 'unused invalid money remains lazy', with_unused, sources
    for key in ['capture:orphan', 'refund:tabby:orphan', 'refund:tamara:orphan']:
        for missing in [True,False]:
            altered=deepcopy(saved)
            altered['engine']['facts'][key]={'amount':'bad'} if missing else {'order_id':{},'amount':'bad'}
            yield f'malformed unused fact {key} missing={missing}', altered, sources
    frozen_refund = deepcopy(sources)
    frozen_refund['orders'][0]['payment']['refunds']=[{'id':'rf0','amount':'10','status':'executed'}]
    prior=baseline.reconcile(initial(), frozen_refund, NOW)
    yield 'saved refund absent in source', prior, sources
    restored_state, rollback_source, deleted_keys = held_delete_case()
    restored = assert_pair('delete rollback preliminary', restored_state, rollback_source)
    assert restored['engine']['obligations'] == restored_state['engine']['obligations']
    assert all(k in restored['engine']['obligations'] for k in deleted_keys)
    assert list(restored['engine']['obligations']) == list(restored_state['engine']['obligations'])
    yield 'whole-order deletion rollback preserves ordering', restored_state, rollback_source
    split_state=initial()
    split_state['owner_id']='owner'
    for bad_second in [False,True]:
        split={'order_id':'split','order_number':99,'owner_id':'owner','currency':'SAR','gross':'1000.00',
            'tenders':[{'reference':'split-a','method':'tabby','provider':'tabby','amount':'400'},
                       {'reference':'split-b','method':'tamara','provider':'tamara','amount':None if bad_second else '600'}]}
        split_order=order(id='split',order_number=99,payment={'split':split})
        yield 'split '+str(bad_second), split_state, {'orders':[split_order], 'fee_policies':[p, policy(id='tamara',provider='tamara')]}
    page_state={**initial(),'owner_id':'owner'}
    page_source={'orders':page_orders(),'fee_policies':[p]}
    page_saved=baseline.reconcile(page_state,page_source,NOW)
    yield 'page fresh',page_state,page_source
    yield 'page replay',page_saved,page_source
    mixed={'orders':good_orders()+page_orders(),'fee_policies':[p]}
    mixed_saved=baseline.reconcile(page_state,mixed,NOW)
    yield 'mixed page/capture replay',mixed_saved,mixed
    for variation in ['lower paid','wrong reference','wrong provider','same capture','bad capture amount','duplicated capture','refund after capture']:
        changed=deepcopy(page_source)
        o=changed['orders'][2];pay=o['payment'];page=pay['order_page_balance']
        if variation=='lower paid': page.update(paid='900',remaining='100')
        if variation=='wrong reference': page['transaction_reference']='capture-order-0'
        if variation=='wrong provider': page['provider']='tamara'
        if variation in ['same capture','bad capture amount','duplicated capture','refund after capture']:
            pay['captures']=[{'id':'page-capture','amount':'1000','captured_at':NOW}]
        if variation=='bad capture amount': pay['captures'][0]['amount']='1001'
        if variation=='duplicated capture': pay['captures']*=2
        if variation=='refund after capture':
            page_saved_capture=baseline.reconcile(page_saved,changed,NOW)
            pay['refunds']=[{'id':'page-refund','amount':'100','status':'executed'}]
            yield 'page '+variation,page_saved_capture,changed
        else: yield 'page '+variation,mixed_saved if variation=='wrong reference' else page_saved,changed
    for missing_key in ['order_id','amount']:
        for family in ['capture:','refund:tabby:']:
            changed=deepcopy(page_saved)
            fact={'order_id':'page-2','provider':'tabby','amount':'10'}
            fact.pop(missing_key)
            changed['engine']['facts'][family+'malformed-page']=fact
            yield f'page malformed {family}{missing_key}',changed,page_source
    rng=random.Random(1918)
    for n in range(250):
        rows=good_orders(rng.randrange(1,8))
        if n%17 == 0:
            rows[-1]['id']='legacy:order'
        for row in rows:
            if rng.randrange(5)==0:
                row.pop('payment',None)
            if rng.randrange(4)==0:
                row['status']=rng.choice(['delivered','cancelled','new'])
            if rng.randrange(4)==0:
                row['carrier']=rng.choice([None, {'id':'imile','cost':None}, {'id':'driver','party_type':'store_driver','cost':'20'}])
            if rng.randrange(4)==0:
                row['items']=rng.choice([[],[{'id':'extra','cost':'40','quantity':2}], [{'id':'bad','cost':rng.choice([None,True,'-1','NaN','bad'])}]])
            if rng.randrange(7)==0:
                row['currency']=rng.choice(['QAR','USD',None])
            if rng.randrange(4)==0 and 'payment' in row:
                row['payment'].update(gross=rng.choice(['999','1000','1100',None]),
                    refunds=[{'id':'random-refund','amount':rng.choice(['0','20','1010']),'status':rng.choice(['executed','requested'])}])
        yield f'random {n}', saved if rng.randrange(2) else initial(), {'orders':rows,'fee_policies':[p] if n%11 else []}


def profile(module, state, sources):
    copied=starts=0
    def trace(frame,event,op):
        nonlocal copied,starts
        if event!='c_call': return
        name=getattr(op,'__name__',None)
        owner=getattr(op,'__self__',None)
        if name=='copy' and isinstance(owner,dict): copied+=len(owner)
        if name=='startswith' and isinstance(owner,str): starts+=1
    before=sys.getprofile()
    try:
        sys.setprofile(trace)
        module.reconcile(state,sources,NOW)
    finally: sys.setprofile(before)
    return {'copied_dict_entries':copied,'string_prefix_checks':starts}


def scaling():
    records=[]
    for count in [50,100,200,400]:
        sources={'orders':good_orders(count),'fee_policies':[policy()]}
        saved=baseline.reconcile(initial(),sources,NOW)
        assert_pair(f'scale {count}',saved,sources)
        row={'orders':count}
        for name,module in [('baseline',baseline),('candidate',candidate)]:
            timings=[]
            for _ in range(3):
                started=time.perf_counter()
                module.reconcile(saved,sources,NOW)
                timings.append((time.perf_counter()-started)*1000)
            row[name]={'median_ms':statistics.median(timings),**profile(module,saved,sources)}
        records.append(row)
    return records


def page_scaling():
    records=[]
    for count in [50,100,200,400]:
        sources={'orders':page_orders(count),'fee_policies':[policy()]}
        saved=baseline.reconcile({**initial(),'owner_id':'owner'},sources,NOW)
        assert_pair(f'page scale {count}',saved,sources)
        row={'orders':count}
        for name,module in [('baseline',baseline),('candidate',candidate)]:
            timings=[]
            for _ in range(3):
                started=time.perf_counter();module.reconcile(saved,sources,NOW)
                timings.append((time.perf_counter()-started)*1000)
            row[name]={'median_ms':statistics.median(timings),**profile(module,saved,sources)}
        records.append(row)
    return records


if __name__=='__main__':
    count=0
    for name,state,sources in cases():
        assert_pair(name,state,sources)
        count+=1
    precision=getcontext().prec
    try:
        for prec in [3,6,12,28,50]:
            getcontext().prec=prec
            assert_pair(f'Decimal precision {prec}',initial(),{'orders':good_orders(7),'fee_policies':[policy()]})
            count+=1
    finally:
        getcontext().prec=precision
    result={'baseline':BASE,'candidate_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'source_hashes':source_hashes,
            'parity_cases':count,'checks':['complete value','recursive insertion order','exception type/message','Decimal flags','input immutability'],
            'scaling':scaling(),'page_scaling':page_scaling()}
    (HERE/'review-result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
