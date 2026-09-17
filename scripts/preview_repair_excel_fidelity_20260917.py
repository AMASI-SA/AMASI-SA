"""Repair only stale Excel-owned subtotal/date fields in isolated Preview.

Dry run first; apply requires the exact plan digest. Never invokes order
upserts, external integrations, settlement recomputation or journal posting.
"""
import argparse
import asyncio
import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True


async def main(apply_digest):
    from bson import json_util
    from dotenv import dotenv_values
    from motor.motor_asyncio import AsyncIOMotorClient
    assert socket.gethostname() == 'agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
    assert json.loads(subprocess.check_output([
        sys.executable, '-B', '/app/scripts/production_release_guard.py', 'status'
    ]))['active'] is False
    backup = Path('/opt/mezan-preview-excel-20260917/before-fidelity/data.json')
    before = json_util.loads(backup.read_text())
    drafts = [x for x in before['accounting_settlements_v2']
              if x.get('statement_reference') == '6743152' and x.get('provider') == 'salla']
    assert len(drafts) == 1
    owner = drafts[0]['user_id']
    env = dotenv_values('/app/backend/.env')
    assert urlsplit(env['MONGO_URL']).hostname in {'localhost', '127.0.0.1', '::1'}
    client = AsyncIOMotorClient(env['MONGO_URL'])
    db = client[env['DB_NAME']]
    scope = {'user_id': owner}
    def canonical(rows):
        return sorted(json_util.dumps(x, sort_keys=True) for x in rows)
    async def verify_financial():
        for name in ['accounting_settlements_v2', 'settlement_entries', 'settlement_files', 'general_ledger']:
            assert canonical(await db[name].find(scope).to_list(None)) == canonical(before[name]), name
        old = {x['order_number']: {k: v for k, v in x.items() if k.startswith('actual_')}
               for x in before['unified_orders']}
        now = await db.unified_orders.find(scope).to_list(None)
        assert len(now) == len(old)
        for row in now:
            assert {k: v for k, v in row.items() if k.startswith('actual_')} == old[row['order_number']]
    await verify_financial()
    plan, protected = [], 0
    rows = await db.unified_orders.find({**scope, 'raw_by_source.excel.skus_json': {'$exists': True}}).to_list(None)
    for row in rows:
        raw = row['raw_by_source']['excel']
        changes = {k: raw[k] for k in ['subtotal', 'order_date_raw']
                   if raw.get(k) is not None and row.get(k) != raw[k]}
        if not changes:
            continue
        if row.get('raw_by_source', {}).get('salla_direct') or any(
            row.get('field_sources', {}).get(k) != 'excel' for k in changes
        ):
            protected += 1
            continue
        plan.append({'id': row['_id'], 'number': row['order_number'], 'changes': changes,
                     'old': {k: row.get(k) for k in changes}, 'raw': raw})
    digest = hashlib.sha256(json_util.dumps(plan, sort_keys=True).encode()).hexdigest()
    print(json.dumps({'candidates': len(rows), 'repairs': len(plan), 'protected': protected, 'digest': digest}), flush=True)
    if apply_digest:
        assert apply_digest == digest, 'Plan changed'
        for item in plan:
            query = {**scope, '_id': item['id'], **item['old'], 'raw_by_source.excel': item['raw']}
            for k in item['changes']:
                query['field_sources.' + k] = 'excel'
            result = await db.unified_orders.update_one(query, {'$set': item['changes']})
            assert result.matched_count == 1, 'Concurrent order update; stop and inspect'
        await verify_financial()
        print(json.dumps({'applied': len(plan), 'financial_snapshots_unchanged': True}))
    client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply-digest')
    asyncio.run(main(parser.parse_args().apply_digest))
