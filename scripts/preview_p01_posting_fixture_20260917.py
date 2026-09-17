"""Owner-authorized synthetic Preview fixture; no production/provider access.

prepare creates synthetic evidence/draft and 100SAR test receivable.
check verifies real browser posting; cleanup reverses only fixture entries.
Never rerun prepare after partial failure; inspect the private checkpoint.
"""
import asyncio, hashlib, json, socket, subprocess, sys
from pathlib import Path
from urllib.parse import urlsplit
sys.dont_write_bytecode = True
sys.path.insert(0, '/app/backend')
REF = 'PREVIEW-P01-POST-TEST-20260917'
STATE = Path('/opt/mezan-preview-excel-20260917/p01-post-test.json')

async def main(mode):
    from bson import json_util
    from dotenv import dotenv_values
    from motor.motor_asyncio import AsyncIOMotorClient
    from ledger_core import post_txn_group, reverse_entry, compute_balance
    from accounting_settlement_routes import _create_draft_from_file
    from accounting_settlement_service import post_reviewed_settlement
    from fastapi import HTTPException
    assert socket.gethostname() == 'agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
    assert not json.loads(subprocess.check_output([sys.executable, '-B', '/app/scripts/production_release_guard.py', 'status']))['active']
    env = dotenv_values('/app/backend/.env')
    assert urlsplit(env['MONGO_URL']).hostname in {'localhost', '127.0.0.1', '::1'}
    client = AsyncIOMotorClient(env['MONGO_URL']); db = client[env['DB_NAME']]
    originals = await db.accounting_settlements_v2.find({'provider':'salla','statement_reference':'6743152'}).to_list(2)
    assert len(originals) == 1
    original = originals[0]; uid = original['user_id']; scope = {'user_id':uid}
    actor = {'id':uid,'name':'Authorized Preview test'}
    def save(state):
        STATE.write_text(json_util.dumps(state)); STATE.chmod(0o600)
    async def balances(keys):
        result = []
        for entity_type, entity_id, sub_account in keys:
            b = await compute_balance(db,user_id=uid,entity_type=entity_type,entity_id=entity_id,sub_account=sub_account)
            result.append(b['net_balance'])
        return result
    if mode == 'prepare':
        assert not STATE.exists(), 'Inspect existing checkpoint; do not repeat'
        assert original['status'] == 'reviewed'
        assert not await db.settlement_files.find_one({**scope,'id':REF})
        baseline = await db.general_ledger.find(scope).to_list(None)
        state = {'original':original,'baseline_ledger':baseline,'seed_ids':[]}
        save(state)
        evidence = {'synthetic':True,'reference':REF,'currency':'SAR','gross':100,'fees':2,'fees_vat':.30,'net':97.70}
        file_doc = {**scope,'id':REF,'provider':'salla','filename':REF+'-SYNTHETIC.json',
                    'file_hash':hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest(),
                    'header':{'statement_id':REF,'statement_date':'2026-09-17','currency':'SAR'},
                    'totals':{'gross':100,'fees':2,'fees_vat':.30,'net':97.70},
                    'matched':0,'unmatched':0,'synthetic':True,'test_evidence':evidence,
                    'notes':'Owner-authorized synthetic aggregate fixture. Not a Salla export.'}
        await db.settlement_files.insert_one(file_doc)
        draft = await _create_draft_from_file(db,owner_id=uid,actor=actor,file_doc=file_doc,
                    bank_account_id=original['bank_account_id'],notes='اختبار البرفيو فقط — بيانات اصطناعية مصرح بها وليست مبيعات فعلية')
        assert not draft['review_reasons']
        state['draft_id'] = draft['id']; save(state)
        seed = [{'entity_type':'payment_gateway','entity_id':'salla','sub_account':'receivable','side':'debit','amount':100,'entry_type':'adjustment'},
                {'entity_type':'test_clearing','entity_id':REF,'side':'credit','amount':100,'entry_type':'adjustment'}]
        keys = sorted({(e['entity_type'],e['entity_id'],e.get('sub_account')) for e in seed + draft['journal_preview']['entries']},key=str)
        state['balance_keys'] = keys; state['baseline_balances'] = await balances(keys); save(state)
        result = await post_txn_group(db,user_id=uid,actor_id=uid,actor_name=actor['name'],entries=seed,
                    txn_type='preview_acceptance_fixture',reason_code='other',notes='Synthetic Preview fixture; owner approved',
                    metadata={'synthetic':True,'test_reference':REF})
        state['seed_ids'] = [e['id'] for e in result['entries']]; state['seed_group'] = result['txn_group_id']; save(state)
        print('PREPARED synthetic draft and receivable100SAR; ready for browser workflow')
    else:
        state = json_util.loads(STATE.read_text())
        assert original == state['original'], 'Real statement changed'
        draft = await db.accounting_settlements_v2.find_one({**scope,'id':state['draft_id']})
        assert draft['statement_reference'] == REF
        group = draft.get('ledger_txn_group_id'); assert group, 'Browser posting not complete'
        entries = await db.general_ledger.find({**scope,'txn_group_id':group}).to_list(None)
        assert len(entries) == 4
        assert round(sum(e['amount'] for e in entries if e['side']=='debit'),2) == 100
        assert round(sum(e['amount'] for e in entries if e['side']=='credit'),2) == 100
        if mode == 'check':
            assert draft['status'] == 'posted'
            # Call replay guard with reviewed precondition, never change persisted state.
            try:
                await post_reviewed_settlement(db,owner_id=uid,actor=actor,draft={**draft,'status':'reviewed'})
                raise AssertionError('Duplicate was not rejected')
            except HTTPException as exc:
                assert exc.status_code == 409 and 'مسبق' in str(exc.detail)
            assert await db.general_ledger.count_documents({**scope,'txn_group_id':group}) == 4
            print('POSTED four balanced legs100SAR; replay409; linked evidence present')
        elif mode == 'cleanup':
            ids = [e['id'] for e in entries] + state['seed_ids']
            for entry_id in ids:
                entry = await db.general_ledger.find_one({**scope,'id':entry_id})
                if not entry.get('reversed_by_entry_id'):
                    await reverse_entry(db,user_id=uid,actor_id=uid,actor_name=actor['name'],entry_id=entry_id,
                                        reason_code='other',notes='End authorized synthetic Preview test '+REF)
            assert await balances(state['balance_keys']) == state['baseline_balances']
            for old in state['baseline_ledger']:
                assert await db.general_ledger.find_one({'_id':old['_id']}) == old
            assert await db.accounting_settlements_v2.find_one({'_id':original['_id']}) == original
            state['cleanup_verified'] = True; save(state)
            print('REVERSED6; all affected balances restored; original ledger and real statement unchanged')
            print('DRAFT_STATUS_AFTER_LEDGER_REVERSAL',draft['status'])
        else:
            raise ValueError(mode)
    client.close()

if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
