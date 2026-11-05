"""Real durable already-sent guard, inside accounting's512MiB cgroup.

No provider credentials or invoice-creation call. Existing financial rules stay
unchanged. The larger inherited suite covers incomplete/unknown-outcome paths.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
import sys

sys.path.insert(0, '/opt/mezan/backend')
from independent_staging import validate
validate()
assert os.environ['MONGO_URL'] == 'mongodb://fixture:synthetic-only-password@staging-mongo:27017'
from motor.motor_asyncio import AsyncIOMotorClient
from integrations.qoyod_manual.send import manual_send_one, ManualSendRefused

async def main():
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client['mezan_exit2d_staging']
    owner = 'economy-synthetic-invoice-owner'
    for index in range(10):
        number = f'ECON-SENT-{index}'
        await db.integration_inbox.update_one({'user_id': owner, 'salla_order_number': number}, {'$setOnInsert': {
            'id': number, 'received_at': datetime.now(timezone.utc),
            'raw_payload': {'data': {'created_at': '2026-09-06'}},
            'canonical_payload': {'order_number': number, 'order_date': '2026-09-06',
                'order_status': 'completed', 'payment_method': 'credit_card', 'currency': 'SAR'},
            'manual_qoyod_invoice_id': f'synthetic-invoice-{index}',
            'manual_qoyod_payment_id': f'synthetic-payment-{index}'}}, upsert=True)
    before = await db.integration_inbox.find({'user_id': owner}).sort('id', 1).to_list(10)
    async def reject(index):
        try:
            await manual_send_one(db, user_id=owner, order_number=f'ECON-SENT-{index%10}')
        except ManualSendRefused as error:
            assert error.code == 'already_sent', error.code
        else:
            raise AssertionError('durable invoice/payment markers did not block replay')
    await asyncio.gather(*(reject(index) for index in range(100)))
    after = await db.integration_inbox.find({'user_id': owner}).sort('id', 1).to_list(10)
    assert before == after
    client.close()
    print(json.dumps({'kind': 'real_qoyod_durable_replay_guard', 'attempts': 100,
        'all_refused_already_sent': True, 'markers_unchanged': True,
        'real_provider_enabled': False}), flush=True)

asyncio.run(main())
