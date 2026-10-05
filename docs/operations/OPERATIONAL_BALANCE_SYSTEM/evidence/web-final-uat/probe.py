"""Disposable synthetic UAT driver; runs unchanged product services/API."""
import asyncio
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from final_preview import ROOT, OWNER, DB_NAME, raw_db, db, client
from operational_balance_service import refresh

BASE='http://127.0.0.1:8135/api/operational-balances'
def api(path, body=None):
    request=Request(BASE+'/'+path,data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=20) as response:return response.status,json.load(response)
    except HTTPError as error:return error.code,json.load(error)

def captured(path):
    entries=[json.loads(line) for line in (ROOT/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    return json.loads(next(e['request'] for e in entries if e.get('kind')=='http' and e.get('method')=='POST' and e.get('path')==('/api/operational-balances/'+path) and e.get('status')==200))

async def snapshot():
    context=api('context')[1];report=api('reports')[1];movements=api('movements')[1]['items']
    state=await raw_db.operational_balance_states_v1.find_one({'owner_id':OWNER})
    return {'context':context,'report':report,'movements':movements,
            'opening_count':len((state or {}).get('openings',{})),
            'financial_hash':hashlib.sha256(json.dumps({'openings':(state or {}).get('openings'), 'movements':movements},sort_keys=True,default=str).encode()).hexdigest()}

async def main(stage):
    result={'stage':stage,'database':DB_NAME,'owner':OWNER}
    before=await snapshot()
    if stage in ('opening-retry','finish-retry'):
        path='openings' if stage=='opening-retry' else 'finish'
        payload=captured(path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:api(path,payload),range(4)))
        assert all(code==200 for code,_ in responses),responses
        assert (await snapshot())['opening_count']==1
        assert before['financial_hash']==(await snapshot())['financial_hash']
        result['replay_statuses']=[code for code,_ in responses]
    elif stage=='manual-retry':
        payload=captured('movements')
        assert payload.get('receipt_id') is None
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:api('movements',payload),range(4)))
        assert all(code==200 for code,_ in responses),responses
        assert len({body['id'] for _,body in responses})==1
        assert before['financial_hash']==(await snapshot())['financial_hash']
        conflict={**payload,'amount':'275'}
        code,body=api('movements',conflict)
        assert code==409,(code,body)
        invalid={**payload,'request_id':'uat-final-invalid-zero','amount':'0'}
        code2,body2=api('movements',invalid)
        assert code2==422,(code2,body2)
        assert before['financial_hash']==(await snapshot())['financial_hash']
        result.update(replay_statuses=[code for code,_ in responses],conflict_response=body,invalid_response=body2)
    elif stage=='seed-pending':
        selected='مصرف UAT المرتبط';source='salla.payment_method_bank'
        key=hashlib.sha256(json.dumps([OWNER,source,selected],ensure_ascii=False).encode()).hexdigest()
        await raw_db.mz2_bank_transfer_bindings.insert_one({'_id':key,'user_id':OWNER,'upstream_source':source,'upstream_value':selected,'financial_account_id':'uat-bound-bank','status':'active','confirmed':True,'identity_contract_version':1,'bank_account_source':'mz2_financial_accounts','currency':'SAR'})
        now=datetime.now(timezone.utc).isoformat()
        await raw_db.unified_orders.insert_one({'user_id':OWNER,'order_number':'UAT-FINAL-BANK-300','raw_by_source':{'salla_direct':{'id':'uat-final-canonical-order-300','reference_id':'UAT-FINAL-BANK-300','created_at':now,'updated_at':now,'currency':'SAR','status':'pending','payment_method':'تحويل بنكي '+selected,'total':{'amount':'300','currency':'SAR'},'items':[]}}})
        await refresh(db,OWNER)
        assert before['financial_hash']==(await snapshot())['financial_hash']
    elif stage in ('reviewed','in-progress'):
        await raw_db.unified_orders.update_one({'user_id':OWNER,'order_number':'UAT-FINAL-BANK-300'},{'$set':{'raw_by_source.salla_direct.status':'تمت المراجعة' if stage=='reviewed' else 'قيد التنفيذ','raw_by_source.salla_direct.updated_at':datetime.now(timezone.utc).isoformat()}})
        await refresh(db,OWNER)
    elif stage=='resync':
        await asyncio.gather(*(refresh(db,OWNER) for _ in range(4)))
        assert before['financial_hash']==(await snapshot())['financial_hash']
    elif stage=='conflicts':
        payload=captured('movements')
        duplicate={**payload,'request_id':'uat-final-bank-duplicate','party_type':'bank','party_id':'uat-bound-bank','kind':'collection','direction':'incoming','amount':'300','order_number':'UAT-FINAL-BANK-300','reference':'duplicate-attempt'}
        code,body=api('movements',duplicate);assert code==409,(code,body)
        result['duplicate_manual_response']=body
        await raw_db.unified_orders.update_one({'user_id':OWNER,'order_number':'UAT-FINAL-BANK-300'},{'$set':{'raw_by_source.salla_direct.total.amount':'450'}})
        await refresh(db,OWNER)
        conflicted=await snapshot()
        assert conflicted['financial_hash']==before['financial_hash']
        assert any(i['code']=='operational_order_bank_credit_conflict' for i in conflicted['report']['issues'])
        result['amount_conflict_issues']=conflicted['report']['issues']
        await raw_db.unified_orders.update_one({'user_id':OWNER,'order_number':'UAT-FINAL-BANK-300'},{'$set':{'raw_by_source.salla_direct.total.amount':'300'}})
        await refresh(db,OWNER)
        # A new qualifying order with no MZ2 bank binding must not choose either bank.
        original=await raw_db.unified_orders.find_one({'user_id':OWNER,'order_number':'UAT-FINAL-BANK-300'})
        raw=original['raw_by_source']['salla_direct'];raw.update(id='uat-final-unbound',reference_id='UAT-FINAL-UNBOUND',payment_method='تحويل بنكي مصرف بلا ربط')
        await raw_db.unified_orders.insert_one({'user_id':OWNER,'order_number':'UAT-FINAL-UNBOUND','raw_by_source':{'salla_direct':raw}})
        await refresh(db,OWNER)
        missing=await snapshot();assert missing['financial_hash']==before['financial_hash']
        assert any(i['code']=='operational_order_bank_binding_incomplete' for i in missing['report']['issues'])
        result['missing_binding_issues']=missing['report']['issues']
        # Preserve the negative fixture, move it to an ineligible state for final readback.
        await raw_db.unified_orders.update_one({'user_id':OWNER,'order_number':'UAT-FINAL-UNBOUND'},{'$set':{'raw_by_source.salla_direct.status':'pending'}})
        await refresh(db,OWNER)
        assert before['financial_hash']==(await snapshot())['financial_hash']
    elif stage!='readback': raise ValueError(stage)
    after=await snapshot()
    if stage in ('reviewed','in-progress','resync','conflicts','readback'):
        credits=[m for m in after['movements'] if m.get('order_number')=='UAT-FINAL-BANK-300']
        assert len(credits)==1,credits
        assert credits[0]['bank_id']=='uat-bound-bank'
        assert after['report']['summary']['actual_liquidity']=='10050.00',after['report']['summary']
        assert not any(m.get('bank_id')=='uat-unlinked-bank' for m in after['movements'])
    result.update(before=before,after=after,result='PASS')
    (ROOT/(stage+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'stage':stage,'result':'PASS','opening_count':after['opening_count'],'movement_count':len(after['movements']),'liquidity':after['report']['summary']['actual_liquidity'],'issues':after['report']['issues']},ensure_ascii=False))
    client.close()
asyncio.run(main(sys.argv[1]))
