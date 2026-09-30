"""Synthetic real-Mongo Track C contracts; never a production database."""
import asyncio
import os
from decimal import Decimal
from uuid import uuid4
import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_atomic import atomic_owner
from accounting_ledger_v2 import (ensure_accounting_ledger_v2_indexes, post_opening_journal_v2,
    post_journal_v2, reverse_journal_v2, OPERATION_ID)
from accounting_module_contract import EVIDENCE_SECTIONS
from accounting_supplier_payments_v2 import (PaymentIn, AllocationIn, settle, payment_workspace,
    INVOICE_CONTRACT, SUPPLIERS, INVOICES, OPERATIONS)

OWNER='synthetic-track-c'
USER={'id':OWNER}
CUT='2026-09-01T00:00:00.000000Z'
class BankSeam:
    async def list_payment_accounts(self, db, owner):
        return [{'id':'canonical-bank','name':'Synthetic bank'}]
    async def require_payment_account(self, db, owner, account_id):
        if account_id != 'canonical-bank':
            raise HTTPException(409,'canonical_account_required')
        return dict(id=account_id,entity_type='bank',entity_id=account_id,sub_account='main',currency='SAR',status='active',account_type='bank')
PORT=BankSeam()
def leg(key,entity,eid,sub,side,amount,kind='opening_balance',metadata=None):
    return dict(leg_key=key,entity_type=entity,entity_id=eid,sub_account=sub,side=side,amount=amount,entry_type=kind,metadata=metadata or {})
@pytest_asyncio.fixture
async def db():
    uri=os.environ.get('MZ2_TEST_MONGO_URI')
    if not uri: pytest.skip('MZ2_TEST_MONGO_URI required')
    client=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=5000)
    database=client['mz2_track_c_'+uuid4().hex]
    assert (await client.admin.command('hello')).get('setName')
    await ensure_accounting_ledger_v2_indexes(database)
    await database.users.insert_one(dict(id=OWNER,role='owner',is_active=True))
    await database[SUPPLIERS].insert_one(dict(user_id=OWNER,id='s-v2',company_name='Synthetic V2',status='active'))
    await database.suppliers.insert_one(dict(user_id=OWNER,id='legacy-only'))
    await database.accounts.insert_one(dict(user_id=OWNER,id='legacy-bank'))
    await database.mz2_atomic_owners.insert_one(dict(_id=OWNER,writes_paused=False,revision=0,
        ledger_backend_state='v2_active',ledger_backend_revision=2,ledger_backend_contract_revision=1,ledger_backend_activation_ref='synthetic'))
    async def opening(s):
        return await post_opening_journal_v2(s._db,user_id=OWNER,actor_id=OWNER,actor_name=OWNER,
            opening_operation_id='synthetic-opening',approved_preview_hash='a'*64,effective_at=CUT,
            entries=[leg('bank','bank','canonical-bank','main','debit','10000.00'),leg('eq','equity','opening','main','credit','10000.00')],mongo_session=s._session)
    opened=await atomic_owner(database,OWNER,opening)
    gid=opened['group']['txn_group_id']
    state=dict(operation_id=OPERATION_ID,status='active',cutover_at=CUT,ledger_source='accounting_v2_operation_scoped',
        evidence_sheet_ref='synthetic',evidence_sections={s['id']:'synthetic' for s in EVIDENCE_SECTIONS},
        opening_balance_preview_id='synthetic',opening_balance_preview_balanced=True,
        opening_balance_approved_at=CUT,opening_balance_approved_by=OWNER,
        opening_balance_txn_group_id=gid,opening_active_txn_group_id=gid,opening_root_txn_group_id=gid,
        opening_balance_zero_accounts=[dict(entity_type='supplier',entity_id='s-v2',sub_account=sub,accounting_at=CUT,
            opening_balance_txn_group_id=gid,evidence_ref='documented-zero') for sub in ('payable','advance')])
    await database.settings.insert_one(dict(user_id=OWNER,mezan2_financial_cutover=state))
    try: yield database
    finally:
        await client.drop_database(database.name)
        client.close()
async def invoice(db,iid='inv-1',amount='1000.00'):
    meta=dict(supplier_invoice_id=iid,supplier_source=SUPPLIERS,invoice_contract=INVOICE_CONTRACT)
    async def post(s):
        return await post_journal_v2(s._db,user_id=OWNER,actor_id=OWNER,actor_name=OWNER,idempotency_key='test-invoice:'+iid,
            txn_type='supplier_invoice',source=INVOICE_CONTRACT,effective_at='2026-09-02T00:00:00Z',
            entries=[leg('expense','expense','services','main','debit',amount,'supplier_invoice'),
                leg('supplier','supplier','s-v2','payable','credit',amount,'supplier_invoice',meta)],mongo_session=s._session)
    journal=await atomic_owner(db,OWNER,post)
    await db[INVOICES].insert_one(dict(user_id=OWNER,id=iid,supplier_id='s-v2',invoice_number=iid,total_halalas=int(Decimal(amount)*100),
        experiment_mode=False,currency='SAR',mz2_financial_contract=INVOICE_CONTRACT,mz2_txn_group_id=journal['group']['txn_group_id'],
        paid_halalas=999999,payment_status='paid'))
    return journal

def payment(**kw):
    return PaymentIn(**{**dict(operation_id=uuid4().hex,amount='1000.00',payment_date='2026-09-03',financial_account_id='canonical-bank',reference='SYN'),**kw})
async def view(db): return await payment_workspace(db,USER,'s-v2',bank_port=PORT)

@pytest.mark.asyncio
async def test_legacy_invoice_does_not_recreate_documented_zero(db):
    await db[INVOICES].insert_one(dict(user_id=OWNER,id='old',supplier_id='s-v2',total_halalas=100000,paid_halalas=0,payment_status='unpaid'))
    data=await payment_workspace(db,USER,bank_port=PORT)
    assert [s['id'] for s in data['suppliers']]==['s-v2']
    assert data['summary']['outstanding_halalas']==0
    assert data['invoices'][0]['outstanding_halalas'] is None
    assert data['invoices'][0]['financial_eligible'] is False
    with pytest.raises(HTTPException): await settle(db,USER,'s-v2',payment(invoice_id='old'),bank_port=PORT)
    assert await db[OPERATIONS].count_documents({})==0

@pytest.mark.asyncio
async def test_unpaid_partial_paid_derived_and_idempotency(db):
    await invoice(db)
    assert (await view(db))['invoices'][0]['payment_status']=='unpaid'
    req=payment(invoice_id='inv-1',amount='400')
    await settle(db,USER,'s-v2',req,bank_port=PORT)
    assert (await settle(db,USER,'s-v2',req,bank_port=PORT))['replayed']
    row=(await view(db))['invoices'][0]
    assert (row['paid_halalas'],row['outstanding_halalas'],row['payment_status'])==(40000,60000,'partial')
    await settle(db,USER,'s-v2',payment(invoice_id='inv-1',amount='600'),bank_port=PORT)
    row=(await view(db))['invoices'][0]
    assert (row['outstanding_halalas'],row['payment_status'])==(0,'paid')

@pytest.mark.asyncio
async def test_overpayment_explicit_advance_and_no_silent_netting(db):
    await invoice(db)
    req=payment(invoice_id='inv-1',amount='1300')
    with pytest.raises(HTTPException) as error: await settle(db,USER,'s-v2',req,bank_port=PORT)
    assert error.value.detail['code']=='supplier_overpayment_requires_explicit_advance'
    result=await settle(db,USER,'s-v2',req.model_copy(update={'allow_advance':True}),bank_port=PORT)
    assert (result['payable_halalas'],result['advance_halalas'])==(100000,30000)
    await invoice(db,'inv-2','500.00')
    data=await view(db)
    assert (data['summary']['outstanding_halalas'],data['summary']['advance_halalas'])==(50000,30000)
    req=AllocationIn(operation_id=uuid4().hex,payment_id=result['id'],invoice_id='inv-2',amount='200',payment_date='2026-09-03',reference='ALLOC')
    await settle(db,USER,'s-v2',req)
    data=await view(db)
    assert (data['summary']['outstanding_halalas'],data['summary']['advance_halalas'])==(30000,10000)
    assert next(r for r in data['invoices'] if r['id']=='inv-2')['outstanding_halalas']==30000

@pytest.mark.asyncio
async def test_unallocated_then_allocate_no_second_bank_or_payable_debit(db):
    await invoice(db)
    result=await settle(db,USER,'s-v2',payment(unallocated_kind='payable'),bank_port=PORT)
    data=await view(db)
    assert data['summary']['outstanding_halalas']==0
    assert data['invoices'][0]['outstanding_halalas']==100000
    before=await db.accounting_general_ledger_v2.count_documents({})
    req=AllocationIn(operation_id=uuid4().hex,payment_id=result['id'],invoice_id='inv-1',amount='1000',payment_date='2026-09-03',reference='ALLOC')
    await settle(db,USER,'s-v2',req)
    assert (await view(db))['invoices'][0]['outstanding_halalas']==0
    assert await db.accounting_general_ledger_v2.count_documents({})==before
    assert (await settle(db,USER,'s-v2',req))['replayed']

@pytest.mark.asyncio
async def test_concurrent_same_intent_once(db):
    await invoice(db)
    req=payment(invoice_id='inv-1')
    results=await asyncio.gather(*(settle(db,USER,'s-v2',req,bank_port=PORT) for _ in range(3)))
    assert len({r['id'] for r in results})==1
    assert await db[OPERATIONS].count_documents({})==1

@pytest.mark.asyncio
async def test_bank_gap_no_legacy_identity_fallback_and_pause(db):
    await invoice(db)
    with pytest.raises(HTTPException) as error: await settle(db,USER,'s-v2',payment(invoice_id='inv-1'))
    assert error.value.detail['code']=='track_a_canonical_account_contract_required'
    with pytest.raises(HTTPException): await settle(db,USER,'legacy-only',payment(),bank_port=PORT)
    with pytest.raises(HTTPException): await settle(db,USER,'s-v2',payment(financial_account_id='legacy-bank'),bank_port=PORT)
    await db.mz2_atomic_owners.update_one({'_id':OWNER},{'$set':{'writes_paused':True}})
    with pytest.raises(HTTPException) as error: await settle(db,USER,'s-v2',payment(),bank_port=PORT)
    assert error.value.status_code==423
    assert await db[OPERATIONS].count_documents({})==0

@pytest.mark.asyncio
async def test_reversed_payment_fails_reconciliation(db):
    await invoice(db)
    result=await settle(db,USER,'s-v2',payment(invoice_id='inv-1'),bank_port=PORT)
    async def reverse(s):
        return await reverse_journal_v2(s._db,user_id=OWNER,actor_id=OWNER,actor_name=OWNER,original_txn_group_id=result['txn_group_id'],
            reason='synthetic reversal',effective_at='2026-09-04T00:00:00Z',mongo_session=s._session)
    await atomic_owner(db,OWNER,reverse)
    with pytest.raises(HTTPException) as error: await view(db)
    assert error.value.detail['code']=='supplier_payment_reversal_requires_reconciliation'

