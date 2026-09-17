"""Attach read-only Salla evidence to ten Preview Excel records.

Input contains only order/item evidence, never credentials. Does not change
original imported facts, actual settlement fields, or financial collections.
"""
import asyncio
import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True


async def main(apply=False):
    from bson import json_util
    from dotenv import dotenv_values
    from motor.motor_asyncio import AsyncIOMotorClient
    assert socket.gethostname() == 'agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
    assert json.loads(subprocess.check_output([sys.executable, '-B', '/app/scripts/production_release_guard.py', 'status']))['active'] is False
    env = dotenv_values('/app/backend/.env')
    assert urlsplit(env['MONGO_URL']).hostname in {'localhost', '127.0.0.1', '::1'}
    client = AsyncIOMotorClient(env['MONGO_URL']); db = client[env['DB_NAME']]
    drafts = await db.accounting_settlements_v2.find({'provider': 'salla', 'statement_reference': '6743152'}).to_list(2)
    assert len(drafts) == 1
    owner = drafts[0]['user_id']; scope = {'user_id': owner}
    records = json.loads(Path('/tmp/mezan-excel-verification-input.json').read_text())
    assert len(records) == 10 and len({r['order_number'] for r in records}) == 10
    financial_names = ['accounting_settlements_v2', 'settlement_entries', 'settlement_files', 'general_ledger']
    before = {name: await db[name].find(scope).to_list(None) for name in financial_names}
    plan = []
    for evidence in records:
        rows = await db.unified_orders.find({**scope, 'order_number': evidence['order_number']}).to_list(2)
        assert len(rows) == 1
        row = rows[0]; raw = row['raw_by_source']['excel']
        assert not row.get('preview_excel_verification'), 'Existing verification; inspect rather than repeat'
        assert not row['raw_by_source'].get('salla_direct')
        assert evidence['source'] in {'salla_order_read', 'salla_invoice_read'}
        if evidence.get('amounts'):
            amounts = evidence['amounts']
            assert raw['original_currency'] == amounts['currency']
            assert abs(float(raw['original_total_amount']) - amounts['total_amount']) < .001
        else:
            assert not raw.get('skus_json')
            assert sum(i['quantity'] for i in evidence['items']) == float(raw['quantity_reported'])
            assert all(i.get('sku') and i['sku'] in raw['products_description'] for i in evidence['items'])
        evidence['excel_fingerprint'] = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
        plan.append((row, evidence))
    sys.path.insert(0, '/app/backend'); sys.path.insert(0, '/tmp/mezan-preview-excel-20260917')
    from order_engine.excel_projection import map_excel_order
    for row, evidence in plan:
        dto = map_excel_order({**row, 'preview_excel_verification': evidence})
        assert dto.order_number == evidence['order_number']
    print(json.dumps({'validated_records': len(plan), 'apply': apply}), flush=True)
    if apply:
        backup = Path('/opt/mezan-preview-excel-20260917/before-salla-verification.json')
        assert not backup.exists()
        backup.write_text(json_util.dumps({'orders': [x[0] for x in plan], 'financial': before})); backup.chmod(0o600)
        for row, evidence in plan:
            result = await db.unified_orders.update_one({**scope, '_id': row['_id'],
                'raw_by_source.excel': row['raw_by_source']['excel'],
                'preview_excel_verification': {'$exists': False}},
                {'$set': {'preview_excel_verification': evidence}})
            assert result.matched_count == 1
            updated = await db.unified_orders.find_one({'_id': row['_id']})
            assert updated.pop('preview_excel_verification') == evidence
            assert updated == row, 'Only the evidence field may change'
        for name in financial_names:
            assert await db[name].find(scope).to_list(None) == before[name]
        print('Applied10; original records and financial collections unchanged except evidence field')
    client.close()


if __name__ == '__main__':
    asyncio.run(main('--apply' in sys.argv))
