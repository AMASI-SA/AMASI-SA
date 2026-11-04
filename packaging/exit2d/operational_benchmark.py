"""Real employee handlers + real Mongo; synthetic-only, no provider transport.

Runs after http_benchmark has created its 5,000-order analytics cohort.
Print acceptance covers the real local-courier print-data contract, not a
physical printer or an external carrier purchase.
"""
import asyncio
import json
import statistics
import sys
import time

sys.path[:0] = ['/opt/acceptance', '/opt/mezan/backend']
from acceptance import boundary, db, owner_session, employee_session, ORIGIN
import httpx


def seed(database):
    owner = database.users.find_one({'email': 'admin@hesab.app'})['id']
    employee = 'exit2c-employee'
    database.mezan_role_assignments_v2.replace_one({'owner_user_id': owner, 'user_id': employee}, {
        'owner_user_id': owner, 'user_id': employee, 'enabled': True, 'role_key': 'preparation_operator',
        'extra_permissions': ['preparation.assigned.read', 'preparation.assigned.work',
            'fulfillment.ready.read', 'fulfillment.labels.print'],
        'fulfillment_responsibilities': ['instant_ready', 'shipping_labeling']}, upsert=True)
    database.mezan_suppliers_v2.insert_one({'user_id': owner, 'id': 'exit2d-perf-supplier',
        'name': 'Synthetic supplier', 'status': 'active', 'service_ids': ['synthetic-service']})
    for index in range(10):
        file = f'PF-EXIT2D-PERF-{index}'
        order = f'EXIT2D-OP-{index}'
        database.mezan_preparation_file_registry_v2.insert_one({'user_id': owner,
            'file_number': file, 'batch_id': file, 'status': 'ready', 'execution_status': 'assigned',
            'responsible_employee_id': employee, 'client_request_id': file})
        database.mezan_preparation_batches_v2.insert_one({'user_id': owner, 'id': file,
            'file_number': file, 'status': 'ready', 'lines': []})
        database.mezan_preparation_pieces_v1.insert_one({'user_id': owner, 'piece_id': file,
            'file_number': file, 'batch_id': file, 'order_number': order, 'order_item_id': 'item',
            'group_key': 'product:synthetic', 'product_id': 'synthetic-product',
            'product_name': 'Synthetic product', 'quantity': 1, 'unit_index': 1,
            'status': 'assigned', 'responsible_employee_id': employee,
            'services': [{'service_id': 'synthetic-service', 'status': 'pending'}]})
        database.unified_orders.insert_one({'user_id': owner, 'order_number': order,
            'order_id': file, 'order_status': 'reviewed', 'order_date': '2026-09-06',
            'currency': 'SAR', 'total_amount': 100, 'total_product_cost': 10,
            'raw_by_source': {'salla_direct': {'id': index + 10000, 'reference_id': order,
                'date': '2026-09-06T12:00:00Z', 'status': {'slug': 'reviewed'}, 'items': []}}})
    number = 'EXIT2D-PRINT'
    database.order_review_workflows.insert_one({'user_id': owner, 'order_number': number,
        'stage': 'completed', 'assembly_status': 'completed',
        'salla_order_status_verified_at': '2026-09-06'})
    database.unified_orders.insert_one({'user_id': owner, 'order_number': number,
        'order_id': 'exit2d-print-local', 'order_status': 'completed', 'order_date': '2026-09-06',
        'shipping_company': 'مندوب الرياض', 'currency': 'SAR', 'total_amount': 100,
        'total_product_cost': 10, 'salla_address_verified_at': '2026-09-06',
        'shipping_address_source_path': 'shipping.address',
        'shipping_address_raw': {'city': 'الرياض', 'street': 'شارع اصطناعي'},
        'raw_by_source': {'salla_direct': {'id': 99999, 'reference_id': number,
            'date': '2026-09-06', 'status': {'slug': 'completed'}, 'delivery_method': 'shipping',
            'receiver': {'name': 'Synthetic recipient'}, 'items': []}}})
    return owner


async def run(owner_cookie, employee_cookie, database):
    times = {}
    stop = asyncio.Event()
    reports_completed = 0
    overlap = 0
    async with httpx.AsyncClient(trust_env=False, timeout=60,
            limits=httpx.Limits(max_connections=24)) as client:
        async def call(method, path, cookie, expected=200, payload=None, group=None):
            nonlocal overlap
            started = time.perf_counter()
            response = await client.request(method, 'http://127.0.0.1:8001' + path,
                headers={'Origin': ORIGIN, 'X-Forwarded-Proto': 'https', 'Cookie': cookie},
                **({'json': payload} if payload is not None else {}))
            assert response.status_code == expected, f'{group}: expected={expected} actual={response.status_code} detail={response.text[:160]}'
            if group:
                overlap += int(not stop.is_set())
                times.setdefault(group, []).append((time.perf_counter()-started)*1000)
            return response.json()

        async def analytics():
            nonlocal reports_completed
            while not stop.is_set():
                result = await call('GET', '/api/dashboard?from_date=2026-09-06&to_date=2026-09-06', owner_cookie)
                assert result['totals']['orders_make_count'] >= 5000
                reports_completed += 1

        loads = [asyncio.create_task(analytics()) for _ in range(4)]
        try:
            await asyncio.sleep(.05)
            for index in range(10):
                workspace = await call('GET', '/api/supplier-dispatch-v1/workspace?grain=piece', employee_cookie, group='my_products')
                assert workspace.get('files'), 'my-products fixture not visible'
                await call('GET', '/api/preparation-work-v1/assembly/orders?state=in_progress', employee_cookie, group='assembly_labeling')
                await call('GET', '/api/fulfillment-v2/ready-to-ship', employee_cookie, group='ready_board')
                payload = {'client_request_id': f'exit2d-perf-dispatch-{index}',
                    'supplier_id': 'exit2d-perf-supplier', 'file_number': f'PF-EXIT2D-PERF-{index}',
                    'selections': [{'group_key': f'piece:PF-EXIT2D-PERF-{index}', 'quantity': 1}]}
                result = await call('POST', '/api/supplier-dispatch-v1/dispatches', employee_cookie,
                    expected=201, payload=payload, group='create_supplier_file')
                dispatch = result['dispatch']['id']
                replay = await call('POST', '/api/supplier-dispatch-v1/dispatches', employee_cookie,
                    expected=201, payload=payload)
                assert replay['dispatch']['id'] == dispatch, 'supplier file replay duplicated'
                await call('POST', '/api/supplier-dispatch-v1/dispatches/' + dispatch + '/ready',
                    employee_cookie, payload={}, group='mark_ready')
                label = await call('POST', '/api/fulfillment-v2/completed/EXIT2D-PRINT/carrier-label/v1',
                    employee_cookie, group='shipping_label_print')
                assert label['print_state'] == 'ready' and label['label_type'] == 'store_courier'
                assert label['print_data'], 'no actual printable document data'
        finally:
            stop.set()
            results = await asyncio.gather(*loads, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception): raise result
    assert reports_completed >= 4 and overlap == 60
    assert database.mezan_supplier_dispatches_v1.count_documents({'supplier_id': 'exit2d-perf-supplier'}) == 10
    output = {name: {'requests': len(values), 'p50_ms': round(statistics.median(values), 3),
        'p95_ms': round(sorted(values)[-1], 3)} for name, values in times.items()}
    print(json.dumps({'kind': 'actual_operational_handlers_synthetic_data', 'analytics_completed': reports_completed,
        'paths': output, 'supplier_files_unique': 10, 'provider_calls_possible': False,
        'physical_printer_tested': False}, sort_keys=True), flush=True)


if __name__ == '__main__':
    boundary()
    database = db()
    seed(database)
    owner = database.users.find_one({'email': 'admin@hesab.app'})
    database.users.update_one({'id': owner['id']}, {'$unset': {'mfa_last_totp_counter': ''}})
    asyncio.run(run(owner_session(), employee_session({'employee': 'exit2c-employee'}), database))
