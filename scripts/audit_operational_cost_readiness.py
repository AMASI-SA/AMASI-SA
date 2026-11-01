#!/usr/bin/env python3
"""Inspect current owner data using the checked-out repair, without installing or writing.

Run from an isolated task checkout, not by changing the deployed /app source.
Existing credentials are read locally and never printed or sent to another API.
No server/startup import, lease operation, source repair, or financial mutation.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


async def main(args):
    from dotenv import dotenv_values
    from motor.motor_asyncio import AsyncIOMotorClient
    from operational_cost_readiness import inspect_readiness
    values = dotenv_values(args.env_file)
    uri = os.environ.get('MONGO_URL') or values.get('MONGO_URL')
    name = os.environ.get('DB_NAME') or values.get('DB_NAME')
    if not uri or not name:
        raise ValueError('readiness_database_configuration_missing')
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=15000,
        connectTimeoutMS=15000, socketTimeoutMS=120000,
        maxPoolSize=2, tz_aware=True, appname='mezan-read-only-cost-readiness')
    try:
        db = client[name]
        states = db['operational_balance_states_v1']
        owner = args.owner
        if not owner:
            candidates = await states.find({'status':'active'},
                {'owner_id':1}).limit(2).to_list(2)
            if len(candidates) != 1 or candidates[0].get('owner_id') != candidates[0].get('_id'):
                raise ValueError('readiness_requires_one_active_owner_or_explicit_owner')
            owner = candidates[0]['owner_id']
        state = await states.find_one({'_id':owner,'owner_id':owner})
        if not state:
            raise ValueError('readiness_owner_state_missing')
        result = await inspect_readiness(db, owner, state)
        after = await states.find_one({'_id':owner,'owner_id':owner}, {'revision':1})
        result['state_revision_stable_during_read'] = bool(after and after.get('revision') == state.get('revision'))
        if not result['state_revision_stable_during_read']:
            result['ready_for_operational_use'] = False
        print('=== OPERATIONAL_READINESS_READ_ONLY ===', flush=True)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
        print('=== READ_ONLY_AUDIT_COMPLETE — NO FINANCIAL WRITES ===', flush=True)
    finally:
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='/app/backend/.env')
    parser.add_argument('--owner', default=None, help='Only needed when more than one operational owner is active.')
    options = parser.parse_args()
    try:
        asyncio.run(main(options))
    except Exception as error:
        # Never expose a database URI, credentials, raw documents or customer data.
        code = str(error) if isinstance(error, ValueError) and str(error).startswith('readiness_') else type(error).__name__
        print('READ_ONLY_AUDIT_STOPPED:', code, flush=True)
        raise SystemExit(1)
