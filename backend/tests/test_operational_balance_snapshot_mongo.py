"""Current financial maps above the 12 MiB guard save without removing money/proofs."""
from copy import deepcopy

from bson import BSON

from operational_balance_archive import SNAPSHOT
from operational_balance_service import create_movement, refresh, report
from operational_balance_store import MAX_STATE_BYTES, STATES, read
from test_operational_balance_integration import movement, run
from test_operational_balance_order_page_payment_flow import T1, T2, T3, order_document, provider_view, start


def test_large_current_maps_refresh_and_settle_once_with_all_proofs_retained():
    async def scenario(db):
        await start(db, 'tamara')
        legacy = await read(db, 'owner')
        proofs = {f'saved-source:{i}': {'reference': str(i), 'note': 'saved source evidence ' * 160}
                  for i in range(3600)}
        legacy['engine']['facts'] = deepcopy(proofs)
        encoded_size = len(BSON.encode(legacy))
        assert MAX_STATE_BYTES < encoded_size < 16 * 1024 * 1024
        await db[STATES].replace_one({'_id': 'owner', 'owner_id': 'owner'}, legacy)
        order = order_document('tamara_installment', '100.00', '0.00')
        await db.unified_orders.insert_one(order)

        first = await refresh(db, 'owner', clock=T1)
        head = await db[STATES].find_one({'_id': 'owner', 'owner_id': 'owner'})
        assert SNAPSHOT in head and len(BSON.encode(head)) <= MAX_STATE_BYTES
        assert first['engine']['as_of'] == T1
        assert provider_view(first, 'tamara')['outstanding_receivable'] == '600.00'
        second = await refresh(db, 'owner', clock=T2)
        assert all(second['engine']['facts'][key] == value for key, value in proofs.items())
        assert second['openings'] == legacy['openings'] and second['movements'] == []

        payout = dict(movement(), request_id='packed-current-payout', party_type='provider', party_id='tamara',
            direction='incoming', kind='settlement', amount='97.00', actual_fee_amount='3.00',
            reference='synthetic-payout', allocations=[{'obligation_id': 'provider:' + order['salla_order_id'], 'amount': '100.00'}])
        paid = await create_movement(db, 'owner', 'owner', payout, clock=T3)
        assert await create_movement(db, 'owner', 'owner', payout, clock=T3) == paid
        saved = await read(db, 'owner')
        assert all(saved['engine']['facts'][key] == value for key, value in proofs.items())
        assert saved['openings'] == legacy['openings'] and len(saved['movements']) == 1
        assert provider_view(saved, 'tamara')['outstanding_receivable'] == '500.00'
        assert report(saved)['summary']['actual_liquidity'] == '1097.00'
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)
