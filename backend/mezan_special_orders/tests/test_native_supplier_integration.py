"""Native supplier close and real MZ2 journals on disposable replica-set data.

Only the authenticated owner and already-scanned receiving input are synthetic.
The actual invoice, source verifier, transaction and accounting hooks run.
"""
from datetime import datetime, timezone
from uuid import uuid4
import os
import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorCollection
import supplier_receiving_routes as receiving
import order_review_routes as review
from mezan_special_orders.tests.test_core import OWNER
from mezan_special_orders.tests.test_financial_integration import run, gl_balance
from mezan_special_orders.tests.test_shared_workflow_integration import setup
from mezan_special_orders.ledger_adapter import EVENTS, verify_event

pytestmark = pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'), reason='Dedicated replica set required')


def native_client(db):
    app = FastAPI()
    def current():
        return {'id': OWNER.tenant_id, 'role': 'owner', 'name': 'Synthetic owner'}
    app.include_router(review.make_order_review_router(db, current))
    app.include_router(receiving.make_supplier_receiving_router(db, current))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic.test')


async def reviewed_order(h):
    order = await setup(h)
    async with native_client(h.db) as client:
        result = await client.post(f'/order-reviews-v1/{order["order_number"]}/complete', json={'expected_revision': 0})
        assert result.status_code == 200, result.text
    return await h.integrated.get(OWNER, order['order_id'])


async def seed_receiving(h, order, *, product_cost=2100, service_cost=325, ordinary=False):
    r = receiving
    now = datetime.now(timezone.utc)
    sid = 'special-native-session-' + uuid4().hex
    supplier = {'id': 'supplier-demo', 'company_name': 'Synthetic supplier', 'phone': '', 'service_links': []}
    await h.raw[r.SUPPLIERS].update_one({'user_id': OWNER.tenant_id, 'id': supplier['id']}, {'$set': supplier}, upsert=True)
    session = {'id': sid, 'user_id': OWNER.tenant_id, 'client_request_id': uuid4().hex,
               'reference': 'SR-TEST-' + uuid4().hex[:8], 'status': 'open', 'supplier_id': supplier['id'],
               'supplier_snapshot': supplier, 'opened_by': OWNER.tenant_id, 'opened_by_name': 'Synthetic owner',
               'opened_at': now, 'scan_count': 1 + ordinary, 'order_numbers': [order['order_number']], 'file_numbers': ['test-file']}
    await h.raw[r.SESSIONS].insert_one(dict(session))
    if service_cost:
        await h.raw[r.RESOURCES].update_one({'user_id': OWNER.tenant_id, 'id': 'svc-demo'}, {'$set': {
            'name': 'Synthetic service', 'code': 'SVC', 'kind': 'service', 'unit_cost': service_cost/100,
            'unit': 'job', 'status': 'active', 'track_inventory': False}}, upsert=True)
    lines = []
    item = order['items'][0]
    for index in range(1 + ordinary):
        number = order['order_number'] if index == 0 else 'ORDINARY-SYNTHETIC-001'
        pid = sid + f'-piece-{index}'
        event_id = sid + f'-event-{index}'
        services = [{'service_id': 'svc-demo', 'service_name': 'Synthetic service', 'required_quantity': 1,
                     'status': 'pending', 'customer_selected': True, 'supplier_invoice_required': True,
                     'reference_unit_price_halalas': service_cost}] if service_cost else []
        piece = {'user_id': OWNER.tenant_id, 'piece_id': pid, 'product_id': 'p-demo',
                 'product_name': 'Synthetic item', 'sku': 'TEST-P', 'order_number': number,
                 'order_item_id': item['order_item_id'] if index == 0 else 'ordinary-item', 'unit_index': 1,
                 'status': r.PIECE_STATUS_IN_PROGRESS, 'supplier_receiving_session_id': sid,
                 'receipt_event_id': event_id, 'services': services}
        await h.raw[r.PIECES].insert_one(dict(piece))
        event = {**piece, 'id': event_id, 'session_id': sid, 'event_type': 'supplier_piece_scanned',
                 'occurred_at': now, 'product_charge_eligible': True, 'invoice_services': services,
                 'reference_product_unit_price_halalas': product_cost, 'reference_product_price_source': 'mezan_v2_base',
                 'reference_product_price_complete': True}
        await h.raw[r.RECEIVING_EVENTS].insert_one(dict(event))
        await h.raw[r.PIECE_EVENTS].insert_one(dict(event))
        lines.append({'piece_ids': [pid], 'product_unit_price_halalas': product_cost,
                      'services': [{'service_id': 'svc-demo', 'unit_price_halalas': service_cost}] if service_cost else []})
    await h.raw[r.COST_PROFILES].update_one({'user_id': OWNER.tenant_id, 'salla_product_id': 'p-demo'},
        {'$set': {'base_cost': product_cost / 100, 'mezan_product_id': 'p-demo'}}, upsert=True)
    return session, {'expected_supplier_id': supplier['id'], 'confirmed_total_halalas': (product_cost+service_cost)*(1+ordinary),
                     'invoice_lines': lines}


@pytest.mark.parametrize('ordinary', [False, True])
def test_native_close_reclassifies_only_local_cost_without_second_supplier_payable(ordinary):
    async def scenario(h):
        order = await reviewed_order(h)
        session, payload = await seed_receiving(h, order, ordinary=ordinary)
        async with native_client(h.db) as client:
            response = await client.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)
            assert response.status_code == 200, response.text
            invoice = response.json()['supplier_invoice']
            assert invoice['total_halalas'] == 2425 * (1 + ordinary)
            count = await h.raw.general_ledger.count_documents({})
            replay = await client.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)
            assert replay.status_code == 200, replay.text
            assert replay.json()['supplier_invoice']['id'] == invoice['id']
            assert await h.raw.general_ledger.count_documents({}) == count
        doc = await h.doc(order)
        assert sorted(c['cost_sar_minor'] for c in doc['costs']) == [325, 2100]
        assert await gl_balance(h, 'supplier', 'supplier-demo', 'payable') == -2425 * (1 + ordinary)
        assert await gl_balance(h, 'expense', 'special_orders:marketing') == 2425
        assert await gl_balance(h, 'expense', 'inventory') == 2425 * ordinary
        for cost in doc['costs']:
            event = await verify_event(h.db, OWNER.tenant_id, doc['order_id'], cost['movement_id'])
            assert event['extra']['new_supplier_payable'] is False
            assert event['extra']['source_invoice']['id'] == invoice['id']
        assert await h.raw.unified_orders.count_documents({}) == 0
    run(scenario)


def test_native_supplier_hook_failure_rolls_back_invoice_payable_piece_and_cost(monkeypatch):
    original = AsyncIOMotorCollection.insert_one
    async def fail(self, *args, **kwargs):
        if self.name == EVENTS:
            raise RuntimeError('synthetic failure after supplier journal reclassification')
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(AsyncIOMotorCollection, 'insert_one', fail)
    async def scenario(h):
        order = await reviewed_order(h)
        session, payload = await seed_receiving(h, order)
        before = await h.doc(order)
        pieces = await h.raw[receiving.PIECES].find({}).sort('piece_id', 1).to_list(10)
        async with native_client(h.db) as client:
            response = await client.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=payload)
            assert response.status_code == 503, response.text
        assert await h.raw.general_ledger.count_documents({}) == 0
        assert await h.raw[receiving.SUPPLIER_INVOICES].count_documents({}) == 0
        assert await h.doc(order) == before
        assert (await h.raw[receiving.SESSIONS].find_one({'id': session['id']}))['status'] == 'open'
        assert pieces == await h.raw[receiving.PIECES].find({}).sort('piece_id', 1).to_list(10)
    run(scenario)
