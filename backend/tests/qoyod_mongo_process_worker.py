"""Synthetic loopback-only worker used by real-Mongo multiprocess tests."""
import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument


async def main():
    uri = os.environ['QOYOD_TEST_MONGO_URI']
    database, action, label = sys.argv[1:]
    if not uri.startswith('mongodb://127.0.0.1:') or not database.startswith('qoyod_test_'):
        raise RuntimeError('isolated test target required')
    client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    db = client[database]
    from integrations.qoyod import scheduled_scan as scans
    from integrations.qoyod_manual.send import _acquire_lock, _finalize_lock
    if action == 'crash_scan':
        scans.LEASE_SECONDS = 3  # Failure test only; production remains 120.
    await db.test_workers.update_one({'_id': label}, {'$set': {'pid': os.getpid(), 'ready': True}}, upsert=True)
    while not (await db.test_control.find_one({'_id': 'start'})):
        await asyncio.sleep(.02)

    async def executor(inner, **kwargs):
        active = await inner.test_control.find_one_and_update(
            {'_id': 'active'}, {'$inc': {'count': 1}}, upsert=True, return_document=ReturnDocument.AFTER)
        await inner.test_control.update_one({'_id': 'peak'}, {'$max': {'count': active['count']}}, upsert=True)
        await inner.test_workers.update_one({'_id': label}, {'$set': {'entered': True}})
        while not await inner.test_control.find_one({'_id': 'release'}):
            await asyncio.sleep(.03)
        await inner.test_control.update_one({'_id': 'active'}, {'$inc': {'count': -1}})
        return {'ok': True, 'rows': [], 'counts': {'matched': 1}}, {'complete': True, 'count': 0}

    try:
        if action in {'scheduled_scan', 'manual_scan', 'crash_scan'}:
            result = await scans.request_scan(db, tenant='main', owner='owner',
                manual=action == 'manual_scan', executor=executor)
            await db.test_workers.update_one({'_id': label}, {'$set': {'accepted': result['accepted']}})
            await asyncio.gather(*list(scans._TASKS))
        elif action in {'failed_lock', 'fresh_lock'}:
            class Collection:
                async def find_one(self, *args, **kwargs):
                    row = await db.qoyod_manual_send_locks.find_one(*args, **kwargs)
                    await db.test_workers.update_one({'_id': label}, {'$set': {'read_old_lock': True}})
                    while await db.test_workers.count_documents({'read_old_lock': True}) != 2:
                        await asyncio.sleep(.02)
                    return row

                def __getattr__(self, name):
                    return getattr(db.qoyod_manual_send_locks, name)

            class LockDB:
                qoyod_manual_send_locks = Collection()

            try:
                token = await _acquire_lock(LockDB(), user_id='main', order_number='same-order', actor=label)
                # Local synthetic provider sink deliberately has no unique key:
                # only the production send lock may prevent two creates.
                await db.test_invoice_sink.insert_one({'order_number': 'same-order', 'actor': label})
                await _finalize_lock(db, user_id='main', order_number='same-order', lock_id=token,
                    status='succeeded', invoice_id='test-invoice', payment_id='test-payment')
                await db.test_workers.update_one({'_id': label}, {'$set': {'lock_won': True}})
            except Exception as exc:
                await db.test_workers.update_one({'_id': label}, {'$set': {
                    'lock_won': False, 'refused': getattr(exc, 'code', type(exc).__name__)}})
        await db.test_workers.update_one({'_id': label}, {'$set': {'done': True}})
    finally:
        client.close()


if __name__ == '__main__':
    asyncio.run(main())
