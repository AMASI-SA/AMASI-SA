import asyncio,json,hashlib
from collections import Counter
from final_preview import ROOT, OWNER, DB_NAME, raw_db, client

async def main():
    trace=[json.loads(line) for line in (ROOT/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    writes=[e for e in trace if e['kind']=='database_write']
    connections=[e for e in trace if e['kind']=='connect']
    imports=[e for e in trace if e['kind']=='accounting_import_attempt']
    assert not imports
    assert writes and all(e['allowed'] for e in writes)
    assert connections and all(e['allowed'] for e in connections)
    state=await raw_db.operational_balance_states_v1.find_one({'owner_id':OWNER})
    events=Counter(e['action'] for e in state['audit'])
    assert events['baseline_saved']==1 and events['system_started']==1
    assert events['movement_saved']==1 and events['order_bank_credited']==1
    assert len(state['openings'])==1 and len(state['movements'])==2
    binding=await raw_db.mz2_bank_transfer_bindings.find_one({'user_id':OWNER})
    banks=await raw_db.mz2_financial_accounts.find({'user_id':OWNER},{'_id':0}).to_list(10)
    credit=next(m for m in state['movements'] if m.get('automatic_order_bank'))
    assert credit['bank_id']==binding['financial_account_id']=='uat-bound-bank'
    assert len(banks)==2
    claims=await raw_db.operational_balance_operation_claims_v1.find({'owner_id':OWNER},{'_id':1,'request_id':1,'actor_id':1}).to_list(50)
    scoped_accounting={name:await raw_db[name].count_documents({'$or':[{'user_id':OWNER},{'owner_id':OWNER},{'_id':OWNER}]}) for name in ('general_ledger','mz2_atomic_owners','mz2_ledger','journal_entries')}
    assert not any(scoped_accounting.values())
    stages=['opening-retry','finish-retry','manual-retry','seed-pending','reviewed','in-progress','resync','conflicts','readback']
    files={name+'.json':hashlib.sha256((ROOT/(name+'.json')).read_bytes()).hexdigest() for name in stages}
    for name in stages:assert json.loads((ROOT/(name+'.json')).read_text(encoding='utf-8'))['result']=='PASS'
    summary={
        'marker':'OPERATIONAL_BALANCE_WEB_FINAL_REVIEW','result':'PASS','requirements_passed':12,'failed':0,
        'tested_head':'0d8e4147a398a0ddd3928c5e04a66adf67a66379','tested_tree':'55ca516f0daffc851337bb4f7e84b314261c94ef',
        'branch':'codex/operational-balance-system-20261005','pr':None,
        'database':DB_NAME,'owner':OWNER,'backend':'http://127.0.0.1:8135/api','browser':'http://127.0.0.1:5178/operational-preview.html',
        'opening_count':1,'movement_count':2,'bank_credit_count':1,'final_liquidity':'10050.00 SAR',
        'equation':'10000 - 250 + 300 = 10050','audit_event_counts':dict(events),'bank_binding':binding,
        'banks':banks,'write_collections':sorted({e['collection'] for e in writes}),
        'nonoperational_write_attempts':0,'accounting_import_attempts':0,'nonloopback_connection_attempts':0,
        'socket_destinations':sorted({str(e['address']) for e in connections}),
        'scoped_accounting_document_counts':scoped_accounting,'fixture_boundaries':'New synthetic owner; two MZ2 banks; source rows seeded directly only in fixed local test DB. Product writes guarded to operational collections. No production auth/data used.',
        'production_changed':False,'production_financial_writes':0,'android':'paused; not used','merge_deploy':'not performed',
        'product_code_changed':False,'stage_file_sha256':files,
        'limits':['Synthetic local authentication; not production sign-in or deployment testing.','Resync exercises the real operational refresh against seeded canonical MZ2 order snapshots; no live Salla call.','Existing 149 backend/43 frontend PASS accepted from immutable candidate; this phase is fresh browser/API UAT, not a new regression run.']}
    (ROOT/'RESULT.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('banks','bank_binding','stage_file_sha256')},ensure_ascii=False,default=str))
    client.close()
asyncio.run(main())
