"""Read-only, order-scoped explanation of historical provider holds. Never refreshes."""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(os.environ.get('MEZAN_DIAGNOSTIC_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'backend'))


def describe(state, row, code):
    from operational_payment_evidence import _instant
    from operational_provider_history import _context
    now = datetime.now(timezone.utc).isoformat()
    blocks, _, movements = _context(state, row, through=now)
    comparison = deepcopy(state)
    comparison['engine']['as_of'] = state['started_at']
    alternate, _, _ = _context(comparison, row, through=now)
    return dict(order_number=row['order_number'], provider=row['provider'], currency=row['currency'],
        created_at=row['created_at'], started_at=state['started_at'], last_calculation=state['engine'].get('as_of'),
        after_start=_instant(row['created_at']) > _instant(state['started_at']),
        saved_issue=code, current_blocks=blocks, blocks_without_snapshot_age=alternate,
        provider_movements=[dict(date=m.get('occurred_at'), currency=m.get('currency'),
            kind=m.get('kind'), direction=m.get('direction'), amount=m.get('amount'),
            allocation_count=len(m.get('allocations') or [])) for m in movements])


async def main(numbers):
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient
    from operational_balance_store import STATES, read
    from operational_provider_history_service import _candidates
    load_dotenv(ROOT / 'backend' / '.env', override=False)
    client = AsyncIOMotorClient(os.environ['MONGO_URL'], serverSelectionTimeoutMS=10000)
    try:
        db = client[os.environ['DB_NAME']]
        query = {'engine.issues.order_page_balance_review.order_number': {'$in': numbers}}
        owners = await db[STATES].find(query, {'owner_id':1}).limit(3).to_list(3)
        if len(owners) > 1:
            raise ValueError('ambiguous_owner_scope')
        results = []
        for item in owners:
            state = await read(db, item['owner_id'])
            for row, code in _candidates(state).values():
                if str(row['order_number']) in numbers:
                    results.append(describe(state, row, code))
        print(json.dumps({'read_only':True, 'orders':results,
            'not_found':[n for n in numbers if n not in {str(r['order_number']) for r in results}]}, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('orders', nargs='+')
    args = parser.parse_args()
    try:
        asyncio.run(main(args.orders))
    except Exception as exc:
        print(json.dumps({'read_only':True, 'error_type':type(exc).__name__}))
        raise SystemExit(1)
