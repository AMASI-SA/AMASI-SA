"""Actual statement import to driver-review consumer, no destination seam mocks."""
import pytest
import pytest_asyncio
from fastapi import HTTPException, FastAPI
from httpx import AsyncClient, ASGITransport
from store_delivery_payment_review_routes import make_store_delivery_payment_review_router
from test_mz2_shipping_native import db, OWNER
from test_mz2_driver_payment_review import driver_delivery, accept, review, balance, totals
from test_mz2_bank_evidence_adapters import imported
from accounting_shipping_native import recognize_cod
from accounting_shipping_native_contract import EVENTS


@pytest_asyncio.fixture(autouse=True)
async def canonical_bank(db):
    await db.mz2_financial_accounts.insert_one({'user_id':OWNER,'id':'bank-f',
        'account_type':'bank','status':'active','currency':'SAR','name':'Synthetic native bank'})


@pytest.mark.asyncio
async def test_real_imported_arrival_approves_once_and_consumes_bank_movement(db):
    assignment = await driver_delivery(db, 'bank_transfer')
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    row = await imported(db,OWNER,'bank-f',value='500.00',direction='in',day='2026-09-03')
    request = accept('bank_transfer',reference=row['id'])
    before = await totals(db)
    app = FastAPI()
    async def actor(): return {'id':OWNER,'role':'owner'}
    app.include_router(make_store_delivery_payment_review_router(db,actor))
    async with AsyncClient(transport=ASGITransport(app),base_url='http://synthetic') as client:
        response = await client.post('/store-delivery/payment-review/' + assignment,json=request.model_dump(mode='json'))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['state'] == 'posted' and await balance(db) == '0.00'
    assert (await review(db,assignment,request))['state'] == 'already_posted'
    after = await totals(db)
    assert after['bank'] - before['bank'] == 500 and after['revenue'] == before['revenue']
    bank = await db.mz2_daily_movements.find_one({'id':row['id']})
    assert bank['status'] == 'accounting_posted' and bank['accounting_txn_group_id'] == result['txn_group_id']
    assert await db[EVENTS].count_documents({'kind':'driver_payment_review'}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',['foreign','bytes','amount','manual','consumed'])
async def test_invalid_arrival_keeps_review_pending_and_full_responsibility(db,mutation):
    assignment = await driver_delivery(db,'bank_transfer')
    await recognize_cod(db,owner=OWNER,actor_id=OWNER,assignment_id=assignment)
    row = await imported(db,OWNER,'bank-f',value='500.00',direction='in',day='2026-09-03')
    if mutation == 'bytes':
        await db.accounting_source_files.update_one({'file_id':row['file_id']},{'$set':{'content':b'corrupt'}})
    else:
        change = {'foreign':{'user_id':'foreign-owner'},'amount':{'amount':'499.00'},
            'manual':{'source':'manual_accountant'},'consumed':{'status':'accounting_posted'}}[mutation]
        await db.mz2_daily_movements.update_one({'id':row['id']},{'$set':change})
    before = await totals(db)
    with pytest.raises(HTTPException,match='native_bank_statement_evidence_required'):
        await review(db,assignment,accept('bank_transfer',reference=row['id']))
    assert await totals(db) == before and await balance(db) == '500.00'
    assert (await db.store_delivery_payment_reviews.find_one({'assignment_id':assignment}))['status'] == 'pending'
    assert await db[EVENTS].count_documents({'kind':'driver_payment_review'}) == 0


@pytest.mark.asyncio
async def test_real_arrival_transaction_rolls_back_after_journal(db,monkeypatch):
    import accounting_driver_payment_review as module
    assignment = await driver_delivery(db,'bank_transfer')
    await recognize_cod(db,owner=OWNER,actor_id=OWNER,assignment_id=assignment)
    row = await imported(db,OWNER,'bank-f',value='500.00',direction='in',day='2026-09-03')
    original = module._post
    async def fail_after(*args,**kwargs):
        await original(*args,**kwargs)
        raise RuntimeError('synthetic-after-post')
    monkeypatch.setattr(module,'_post',fail_after)
    before = await totals(db)
    with pytest.raises(RuntimeError,match='synthetic-after-post'):
        await review(db,assignment,accept('bank_transfer',reference=row['id']))
    assert await totals(db) == before and await balance(db) == '500.00'
    assert (await db.mz2_daily_movements.find_one({'id':row['id']}))['status'] == 'unclassified'
    assert (await db.store_delivery_payment_reviews.find_one({'assignment_id':assignment}))['status'] == 'pending'
