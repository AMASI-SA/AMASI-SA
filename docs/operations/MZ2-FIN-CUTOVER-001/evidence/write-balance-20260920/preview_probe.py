#!/usr/bin/env python3
"""One-shot synthetic HTTP acceptance, strictly the NEW isolated Preview DB.

Seeds source evidence/opening/statement fixtures, never calls financial writers
directly. Tokens and private signing material remain in process memory. Refuses
an existing populated DB; preserves every resulting record, including sentinels.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

SOURCE = 'e8818bb67d84ccd0487e126a06f5e9c3e09702c7'
STATE = Path('/app/.worktrees/p01-write-final-preview-20260920')
DATABASE = 'mz2_write_balance_preview_20260920'
HOST = 'salla-analytics.preview.emergentagent.com'
BASE = '/api/financial-provider-apps/accounting-module'
OPERATION = 'MZ2-FIN-CUTOVER-001'
HASHES = {
    'auth.py': '5d2ad7a3e5382e8bdd091996e4f135e70a0e3602b0e214344427f73535120f71',
    'accounting_customer_refunds.py': 'ff1b1807645133495f087038d77d5ebb2e6e96247c4afcd7ee23de45673ad36b',
    'accounting_customer_advances.py': '9819581e114402d3556768185b0739e69ef64f149083129644ea7697657edb74',
    'accounting_mz2_balances.py': '4984f67c6223af939ddecc040fa761c6127974dc665a2b321fe8f60d28642342',
    'accounting_mz2_reports.py': 'bc2db4552d51c5460a1319a5807fcdafb61fab692dd946d526b54e8314d21c7b',
}


def values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from values(item)
    elif isinstance(value, list):
        for item in value:
            yield from values(item)
    else:
        yield value


async def main(args):
    state, backend, output = Path(args.state).resolve(), Path(args.backend_source).resolve(), Path(args.output).resolve()
    assert state == STATE and backend == STATE / 'source/backend', 'wrong isolated source/state'
    assert args.database == DATABASE and output.is_relative_to(state) and not output.exists(), 'wrong output/database'
    uri = urlsplit(args.db_uri)
    assert (uri.scheme, uri.hostname, uri.port, uri.path) == ('mongodb', '127.0.0.1', 27038, '/'), 'not isolated loopback DB'
    assert not uri.username and not uri.password and parse_qs(uri.query) == {'replicaSet': ['p01writepreview']}, 'wrong replica URI'
    for name, expected in HASHES.items():
        assert hashlib.sha256((backend/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected, 'source hash mismatch: '+name
    sys.path[:0] = [str(backend), str(backend/'tests')]
    os.environ['JWT_SECRET'] = (state/'preview-session-signing.secret').read_text().strip()
    os.environ['MONGO_URL'], os.environ['DB_NAME'] = args.db_uri, DATABASE
    from motor.motor_asyncio import AsyncIOMotorClient
    import httpx
    from auth import create_access_token
    from mz2_report_fixtures import provision_report_opening
    mongo = AsyncIOMotorClient(args.db_uri, serverSelectionTimeoutMS=5000)
    db = mongo[DATABASE]
    assert (await db.command('hello')).get('setName') == 'p01writepreview', 'replica identity mismatch'
    assert all([await db[n].count_documents({}) == 0 for n in await db.list_collection_names()]), 'database must be NEW and empty; never resume or erase'
    evidence = dict(source_a=SOURCE, database=DATABASE, replica='p01writepreview', preview_host=HOST,
        production_changed=False, prior_acceptance_databases_accessed=False, status='running', steps=[])

    def save():
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    async def snapshot():
        digest = hashlib.sha256(); counts = {}
        for name in sorted(await db.list_collection_names()):
            rows = await db[name].find({}).sort('_id', 1).to_list(None)
            if rows:
                digest.update(json.dumps([name, rows], sort_keys=True, default=str, separators=(',', ':')).encode())
                counts[name] = len(rows)
        groups = await db.general_ledger.distinct('txn_group_id')
        return dict(sha256=digest.hexdigest(), collection_counts=counts, journal_groups=len(groups))

    async with httpx.AsyncClient(base_url='http://127.0.0.1:8001', timeout=60,
            headers={'Host': HOST, 'Origin': 'https://'+HOST}, follow_redirects=False, trust_env=False) as http:
        policy = await http.get('/api/preview-auth-policy')
        assert policy.status_code == 200 and SOURCE in list(values(policy.json())), 'runtime is not pinned Preview source'
        evidence['runtime_policy'] = policy.json()
        await db.users.insert_one(dict(id='owner', role='owner', name='SYN write isolation accountant',
            email='synthetic-write-probe@example.invalid', is_active=True))
        http.headers['Authorization'] = 'Bearer '+create_access_token('owner', 'synthetic-write-probe@example.invalid', mfa_verified=True)

        async def request(label, method, path, payload=None, expected=200):
            response = await http.request(method, BASE+path, json=payload)
            body = response.json()
            evidence['steps'].append(dict(label=label, http=response.status_code, response=body))
            save()
            assert response.status_code == expected, label+': unexpected HTTP status'
            return body

        async def denied(label, path, expected_reason):
            before = await snapshot()
            body = await request(label, 'POST', path, {}, 409)
            assert expected_reason in json.dumps(body, ensure_ascii=False), label+': wrong rejection'
            after = await snapshot()
            evidence['steps'][-1].update(before=before, after=after, no_partial_write=before==after)
            save(); assert before == after, label+': rejected write mutated DB'

        async def sentinel(entity, identifier, sub, side, value=999999):
            group = 'SYN-HTTP-LEGACY-'+uuid4().hex
            await db.general_ledger.insert_many([dict(id=uuid4().hex, user_id='owner', txn_group_id=group,
                status='posted', entry_type='bank_transfer', entity_type=et, entity_id=eid, sub_account=account,
                side=direction, amount=value, metadata={'accounting_at':'2026-08-02T00:00:00Z'},
                created_at='2026-09-20T12:00:00Z', posted_at='2026-09-20T12:00:00Z')
                for et, eid, account, direction in ((entity,identifier,sub,side),
                    ('equity','SYN-LEGACY','capital','credit' if side=='debit' else 'debit'))])
            evidence.setdefault('retained_sentinels', []).append(group)

        async def source(provider, suffix):
            number, payment = 'SYN-HTTP-ORDER-'+suffix, 'SYN-HTTP-CAPTURE-'+suffix
            await db.payment_transactions.insert_one(dict(id=payment, user_id='owner', provider=provider,
                provider_id=payment, order_reference_id=number, amount='115.00', captured_amount='115.00',
                currency='SAR', status='captured', source='synthetic_preview_evidence', captured_at='2026-08-01T10:00:00Z'))
            await db.orders_db.insert_one(dict(id=number, user_id='owner', order_number=number, total_amount='115.00',
                currency='SAR', payment_method=provider, order_status='completed', delivered_at='2026-08-01T11:00:00Z',
                order_created_at='2026-08-01T09:00:00Z'))
            return dict(provider=provider, payment_id=payment)

        async def sale(identity):
            preview = await request('sale preview '+identity['payment_id'], 'POST', '/receivables/preview', identity)
            assert preview['state'] == 'eligible'
            result = await request('sale execute '+identity['payment_id'], 'POST', '/receivables/execute',
                {**identity, 'preview_hash':preview['preview_hash']})
            return result['event_key']

        async def case(original, reference):
            row = await request('create '+reference, 'POST', '/customer-refunds', dict(original_key=original,
                case_reference=reference, amount='115', recognized_at='2026-08-31T23:30:00+03:00', reason='SYN confirmed customer right'))
            return await request('recognize '+reference, 'POST', '/customer-refunds/'+row['id']+'/recognize', dict(
                amount='115', recognized_at='2026-08-31T23:30:00+03:00', reason='SYN month-end right', evidence_ref=reference+'-DOC'))

        async def payment(original, case_reference, amount, day, channel='bank', proof=False):
            payload = dict(original_key=original, case_reference=case_reference, amount=str(amount), paid_at=day+'T10:00:00+03:00',
                bank_account_id='bank' if channel=='bank' else '', execution_channel=channel,
                bank_reference='SYN-HTTP-'+case_reference+'-'+str(amount))
            if proof:
                payload.update(provider_refund_id='SYN-HTTP-DISTINCT-EXECUTION', proof_name='SYN-accountant-evidence.txt',
                    proof_base64=base64.b64encode(b'SYN reviewed customer/order amount40 September2 Tabby execution').decode())
            return await request('draft payment '+case_reference+' '+str(amount), 'POST', '/customer-refunds/bank-payments', payload)

        async def approve(row):
            before = await db.general_ledger.count_documents({})
            result = await request('approve '+row['id'], 'POST', '/customer-refunds/bank-payments/'+row['id']+'/approve', {})
            assert await db.general_ledger.count_documents({}) == before+2
            legs = await db.general_ledger.find({'txn_group_id':result['txn_group_id']}, {'_id':0}).to_list(None)
            assert len(legs)==2 and not any(r['entity_type'] in {'tax','revenue'} for r in legs)
            assert sum(r['amount']*(1 if r['side']=='debit' else -1) for r in legs)==0
            evidence.setdefault('payment_journals', []).append(legs)
            again = await request('duplicate '+row['id'], 'POST', '/customer-refunds/bank-payments/'+row['id']+'/approve', {})
            assert again['txn_group_id']==result['txn_group_id'] and await db.general_ledger.count_documents({})==before+2
            save(); return result

        try:
            group = await provision_report_opening(db, amount=10)
            await db.accounts.update_one({'id':'bank'}, {'$set':{'name':'SYN write isolation bank','current_balance':999999}})
            await db.settings.update_one({'user_id':'owner'}, {'$push':{'mezan2_financial_cutover.opening_balance_zero_accounts':dict(
                entity_type='payment_gateway',entity_id='tabby',sub_account='receivable',evidence_ref='SYN-ZERO-TABBY',
                accounting_at='2020-01-01T00:00:00Z',opening_balance_txn_group_id=group)}})
            await db.mz2_atomic_owners.insert_one({'_id':'owner','revision':0})
            await request('synthetic policy only', 'PUT', '/sales-tax', dict(rate='15',effective_at='2020-01-01T00:00:00Z',revision=0,reason='SYN testing only; not Production settings'))
            identity = await source('tamara','MAIN')
            await sentinel('payment_gateway','tamara','receivable','debit')
            # Only non-ledger fixture evidence is seeded. Actual settlement
            # authorization, receipt consumption and posting run through HTTP.
            draft = dict(id='SYN-HTTP-SETTLEMENT',user_id='owner',provider='tamara',currency='SAR',status='reviewed',
                workflow_state='reviewed',bank_account_id='bank',statement_reference='SYN-HTTP-STATEMENT',statement_date='2026-09-01',
                source_file_id='SYN-HTTP-FILE',source_file_hash='SYN-HTTP-HASH',source_review_count=0,
                source_snapshot={'matched':1,'unmatched':0},idempotency_key='SYN-HTTP-SETTLEMENT',
                amounts={'gross_sales':115,'reported_net':115},bank_receipt_id='SYN-HTTP-RECEIPT',review_reasons=[])
            await db.accounting_settlements_v2.insert_one(draft)
            await db.mz2_bank_receipts.insert_one(dict(id='SYN-HTTP-RECEIPT',user_id='owner',provider='tamara',currency='SAR',
                bank_account_id='bank',amount='115',status='linked',settlement_id=draft['id']))
            await denied('legacy provider cannot fund settlement', '/settlements/drafts/'+draft['id']+'/post', 'غير كافية')
            original = await sale(identity)
            due = await case(original, 'SYN-HTTP-MONTH-END')
            first = await payment(original, due['case_reference'], 40, '2026-09-02')
            await sentinel('bank','bank','main','debit')
            await denied('legacy bank/current_balance cannot fund payment', '/customer-refunds/bank-payments/'+first['id']+'/approve', 'insufficient_refund_execution_balance')
            report = await request('August before cash', 'GET', '/reports/financial-position?as_of=2026-08-31')
            assert report['status']=='available' and report['liabilities']['customer_refund_payable']==115 and report['assets']['banks']==10
            settlement = await request('qualified sale funds settlement', 'POST', '/settlements/drafts/'+draft['id']+'/post', {})
            evidence['settlement_group'] = settlement['ledger_txn_group_id']
            await sentinel('bank','bank','main','credit',value=1999998)
            await sentinel('liability',due['id'],'customer_refund_payable','debit')
            await approve(first)
            second = await payment(original,due['case_reference'],75,'2026-09-05'); await approve(second)
            for day, remaining, bank in [('2026-08-31',115,10),('2026-09-02',75,85),('2026-09-05',0,10)]:
                result = await request('historical '+day, 'GET', '/reports/financial-position?as_of='+day)
                assert result['status']=='available' and result['liabilities']['customer_refund_payable']==remaining
                assert result['assets']['banks']==bank
            trial = await request('isolated trial balance', 'GET', '/reports/trial-balance?as_of=2026-09-05')
            assert abs(sum(x['debits']-x['credits'] for x in trial['items'])) < .001
            # Independent second customer case: a different execution provider
            # is documented explicitly; legacy provider funds still cannot pay.
            original2 = await sale(await source('tamara','DISTINCT-ORIGINAL'))
            due2 = await case(original2,'SYN-HTTP-DISTINCT')
            foreign = await payment(original2,due2['case_reference'],40,'2026-09-02',channel='tabby',proof=True)
            await sentinel('payment_gateway','tabby','receivable','debit')
            await denied('different provider legacy cannot fund', '/customer-refunds/bank-payments/'+foreign['id']+'/approve', 'insufficient_refund_execution_balance')
            await sale(await source('tabby','EXECUTOR-FUNDING'))
            await approve(foreign)
            evidence['status']='passed'; evidence['final_snapshot']=await snapshot(); save()
        except Exception:
            evidence['status']='failed'; evidence['failure_after_step']=len(evidence['steps']); save()
            raise
        finally:
            mongo.close()
    print(json.dumps({'status':evidence['status'],'output':str(output),'source_a':SOURCE,'database':DATABASE}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('backend-source','state','db-uri','database','output'):
        parser.add_argument('--'+flag, required=True)
    asyncio.run(main(parser.parse_args()))
