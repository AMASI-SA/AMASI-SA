"""Audit existing Track F against PR1238's photo-less delivered row shape.

No product changes, fallback, or new financial producer. The existing Track F
fixture uses only a unique disposable replica database and a synthetic opening.
"""
from copy import deepcopy

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from test_mz2_shipping_native import db, OWNER, AT, rate, report
from accounting_shipping_native import recognize_cod
from accounting_shipping_native_setup import save_setup
from accounting_shipping_native_observer import observe_driver_delivery
from accounting_shipping_native_contract import EVIDENCE, EVENTS, INBOX
from accounting_ledger_v2 import (
    GROUPS_COLLECTION, GENERAL_LEDGER_COLLECTION, AUDIT_COLLECTION, SEQUENCES_COLLECTION,
)
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router

FINANCIAL = (GROUPS_COLLECTION, GENERAL_LEDGER_COLLECTION, AUDIT_COLLECTION,
             SEQUENCES_COLLECTION, EVIDENCE, EVENTS, 'mz2_atomic_owners')


async def seed_delivered(database, reference='proof-audit'):
    await save_setup(database, OWNER, OWNER, rate(2, kind='store_driver', identity='driver-f', delivery_fee='20.00'))
    await database.store_drivers.update_one({'id': 'driver-f'}, {'$set': {'account_user_id': 'audit-driver'}})
    await database.store_delivery_assignments.insert_one({
        'id': 'assign-audit', 'user_id': OWNER, 'driver_id': 'driver-f',
        'order_id': 'salla-1', 'order_number': '1', 'status': 'delivered',
        'delivered_at': AT, 'active': True,
    })
    await database.store_delivery_collections.insert_one({
        'id': 'collection-audit', 'user_id': OWNER, 'driver_id': 'driver-f',
        'assignment_id': 'assign-audit', 'order_id': 'salla-1', 'order_number': '1',
        'payment_method': 'cash', 'amount': '500.00', 'cod_custody_amount': '500.00',
        'accounting_status': 'operational_only', 'delivery_proof_reference': reference,
        'review_status': 'not_required',
    })


def proof(**changes):
    return {'user_id': OWNER, 'driver_id': 'driver-f', 'token': 'proof-audit',
            'assignment_id': 'assign-audit', 'status': 'bound',
            'bound_assignment_id': 'assign-audit', **changes}


async def financial_snapshot(database):
    return {name: await database[name].find({}).sort('_id', 1).to_list(None) for name in FINANCIAL}


@pytest.mark.asyncio
@pytest.mark.parametrize('alternative', [
    'canonical_salla_delivered_only', 'physical_cash_attestation',
    'payment_receipt', 'customer_conversation', 'uploaded_delivery_proof',
    'foreign_bound_proof', 'other_assignment_bound_proof', 'omitted_reference',
])
async def test_other_existing_evidence_cannot_replace_bound_driver_delivery_proof(db, alternative):
    await seed_delivered(db, None if alternative in ('canonical_salla_delivered_only', 'omitted_reference') else 'proof-audit')
    if alternative == 'physical_cash_attestation':
        # Observation is not an equivalent financial source, even with complete
        # driver/order/amount/time fields. No C3 capture is re-executed here.
        await db.store_delivery_collections.update_one({'id': 'collection-audit'}, {'$set': {
            'physical_cash_evidence': {'source': 'driver_confirmation_at_delivered',
                'driver_id': 'driver-f', 'order_id': 'salla-1', 'cod_amount': '500.00',
                'physical_cash_amount': '450.00', 'confirmed_at': AT,
                'confirmation_actor': 'audit-driver', 'financial_effect': 'none'}}})
    elif alternative in ('payment_receipt', 'customer_conversation'):
        name = 'store_delivery_receipts' if alternative == 'payment_receipt' else 'store_delivery_customer_conversation_evidence'
        await db[name].insert_one(proof(evidence_kind=alternative))
    elif alternative == 'uploaded_delivery_proof':
        await db.store_delivery_delivery_proofs.insert_one(proof(status='uploaded', bound_assignment_id=None))
    elif alternative == 'foreign_bound_proof':
        await db.store_delivery_delivery_proofs.insert_one(proof(user_id='different-owner'))
    elif alternative == 'other_assignment_bound_proof':
        await db.store_delivery_delivery_proofs.insert_one(proof(bound_assignment_id='different-assignment'))
    elif alternative == 'omitted_reference':
        await db.store_delivery_delivery_proofs.insert_one(proof())
    before = await financial_snapshot(db)
    for _ in range(2):
        with pytest.raises(HTTPException) as caught:
            await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id='assign-audit')
        assert caught.value.detail['code'] == 'shipping_driver_bound_delivery_proof_required'
        assert await financial_snapshot(db) == before
    result = await observe_driver_delivery(db, owner=OWNER, assignment_id='assign-audit')
    assert result == {'state': 'pending', 'code': 'shipping_driver_bound_delivery_proof_required'}
    assert (await db[INBOX].find_one({'assignment_id': 'assign-audit'}))['state'] == 'pending'
    assert await financial_snapshot(db) == before
    assert (await report(db, 'store_driver', 'driver-f'))['cod_receivable'] == '0.00'
    assert (await db.store_delivery_collections.find_one({'id': 'collection-audit'}))['amount'] == '500.00'


@pytest.mark.asyncio
async def test_existing_exact_bound_source_reuses_writer_once(db):
    await seed_delivered(db)
    await db.store_delivery_delivery_proofs.insert_one(proof())
    before = await db[GROUPS_COLLECTION].count_documents({})
    first = await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id='assign-audit')
    again = await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id='assign-audit')
    assert first['state'] == 'posted'
    assert again['state'] == 'already_posted'
    assert first['evidence_id'] == again['evidence_id']
    assert await db[GROUPS_COLLECTION].count_documents({}) == before + 1
    assert (await report(db, 'store_driver', 'driver-f'))['cod_receivable'] == '500.00'


@pytest.mark.asyncio
async def test_existing_upload_route_has_no_late_delivery_proof_binding(db):
    await seed_delivered(db, None)
    actor = {'id': 'audit-driver', 'role': 'store_driver', 'created_by': OWNER, '_session_client': 'amasi_mobile'}
    async def current_user(): return deepcopy(actor)
    app = FastAPI()
    app.include_router(make_store_delivery_payment_evidence_router(db, current_user))
    before = await financial_snapshot(db)
    async with AsyncClient(transport=ASGITransport(app), base_url='http://isolated-audit') as client:
        response = await client.post('/store-delivery/evidence/delivery-proof',
            data={'assignment_id': 'assign-audit'}, files={'file': ('proof.png', b'\x89PNG\r\n\x1a\nsynthetic-image', 'image/png')})
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'driver_assignment_not_found'
    assert await db.store_delivery_delivery_proofs.count_documents({}) == 0
    assert await financial_snapshot(db) == before


@pytest.mark.asyncio
async def test_paused_owner_still_stops_before_missing_proof(db):
    await seed_delivered(db, None)
    await db.mz2_atomic_owners.update_one({'_id': OWNER}, {'$set': {'writes_paused': True}})
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException) as caught:
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id='assign-audit')
    assert caught.value.status_code == 423
    assert caught.value.detail['code'] == 'mz2_writes_paused'
    assert await financial_snapshot(db) == before
