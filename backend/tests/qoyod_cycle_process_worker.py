"""Synthetic two-process cycle probe. Cannot connect outside loopback tests."""
import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient
from integrations.qoyod_manual.auto_cycle import run_due_cycle


async def main():
    name, label = sys.argv[1:]
    uri = os.environ['QOYOD_TEST_MONGO_URI']
    assert uri.startswith('mongodb://127.0.0.1:') and name.startswith('qoyod_test_')
    client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    db = client[name]
    try:
        await db.test_workers.insert_one({'_id': label, 'ready': True, 'pid': os.getpid()})
        async with asyncio.timeout(15):
            while not await db.test_control.find_one({'_id': 'cycle-start'}):
                await asyncio.sleep(.02)
        offset = 0
        async def send(inner, *, batch_limit):
            nonlocal offset
            count = min(batch_limit, 12 - offset)
            if count:
                # No uniqueness constraint: a second wrongly admitted cycle
                # would insert duplicate synthetic invoice references.
                await inner.test_invoice_sink.insert_many([
                    {'reference': str(i)} for i in range(offset, offset + count)
                ])
            offset += count
            await asyncio.sleep(.03)
            return {'ok': True, 'status': 'succeeded' if count else 'idle',
                    'sent_count': count, 'results': [{}] * count,
                    'discovery': {'checked': count}}
        result = await run_due_cycle(db, batch_runner=send, batch_pause=0)
        await db.test_workers.update_one({'_id': label}, {'$set': {
            'done': True, 'claimed': result.get('claimed', False),
            'status': result['status'],
        }})
    finally:
        client.close()


if __name__ == '__main__':
    asyncio.run(main())
