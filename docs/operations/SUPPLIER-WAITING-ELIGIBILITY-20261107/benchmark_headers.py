import asyncio, copy, json, time, uuid
from motor.motor_asyncio import AsyncIOMotorClient
from test_supplier_dispatch_waiting_current_status import seed_order, seed_piece, ObservedDB, OWNER
from preparation_section_reads import waiting_cards
import preparation_supplier_dispatch as dispatch

async def main():
    client = AsyncIOMotorClient('mongodb://127.0.0.1:27858/?directConnection=true', serverSelectionTimeoutMS=5000)
    raw = client['waiting_header_bench_' + uuid.uuid4().hex]
    db = ObservedDB(raw)
    try:
        await seed_order(db, 'base'); await seed_piece(db, 'base')
        order = await raw.unified_orders.find_one({'order_number': 'base'}, {'_id': 0})
        piece = await raw[dispatch.PIECES].find_one({'order_number': 'base'}, {'_id': 0})
        await raw.unified_orders.create_index([('user_id', 1), ('order_number', 1)])
        orders = []; pieces = []
        for i in range(3000):
            number = f'order-{i:04}'; row = copy.deepcopy(order); row['order_number'] = number
            row['raw_by_source']['salla_direct']['reference_id'] = number
            row['raw_by_source']['salla_direct']['id'] = 'salla-' + number
            row['raw_by_source']['salla_direct']['items'][0]['options'] = [{'value': 'x' * 10000}]
            row['order_status'] = 'completed' if i < 2900 else 'processing'
            orders.append(row)
            for unit in range(3):
                part = copy.deepcopy(piece)
                part.update(order_number=number, piece_id=number + '-' + str(unit), product_id=f'p-{i:04}')
                part['product_options_snapshot'] = [{'value': 'x' * 10000}]
                pieces.append(part)
        await raw.unified_orders.insert_many(orders)
        await raw[dispatch.PIECES].insert_many(pieces)
        for mode in ('mostly_terminal', 'all_live'):
            if mode == 'all_live':
                await raw.unified_orders.update_many({}, {'$set': {'order_status': 'processing'}})
            times = []; checked = []
            for _ in range(3):
                db.reads.clear()
                start = time.perf_counter()
                result = await waiting_cards(db, user_id=OWNER['id'], employee_id=OWNER['id'])
                times.append(round((time.perf_counter() - start) * 1000))
                counts = [len(query['order_number']['$in']) for name, query in db.reads if name == 'unified_orders']
                checked.append({'status_dates': counts[0], 'canonical_eligibility': sum(counts[1:])})
            print(json.dumps({'mode': mode, 'candidate_orders': 3001, 'physical_pieces': 9001,
                'headers': len(result['groups']), 'milliseconds': times, 'policy_orders_read': checked}), flush=True)
    finally:
        await client.drop_database(raw.name)
        client.close()

asyncio.run(main())
