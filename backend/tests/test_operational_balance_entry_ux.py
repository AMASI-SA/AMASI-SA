import asyncio
import pytest
from fastapi import HTTPException
from operational_balance_service import create_movement, save_opening, report
from operational_balance_store import read
from test_operational_balance_integration import run, started, movement, opening, START
from test_operational_balance_app_permissions import client, grant, WRITE
from test_operational_balance_assigned_banks import command


def test_supplier_payment_requires_payable_and_concurrent_requests_cannot_overpay():
    async def scenario(db):
        await started(db)
        async def pay(i):
            try:
                return await create_movement(db, 'owner', 'owner', {**movement(), 'request_id': f'compete-{i}'})
            except HTTPException as exc:
                return exc
        results = await asyncio.gather(pay(1), pay(2))
        assert sum(isinstance(r, HTTPException) for r in results) == 1
        rejected = next(r for r in results if isinstance(r, HTTPException))
        assert rejected.detail['code'] == 'operational_supplier_payment_exceeds_payable'
        assert rejected.detail['not_applied'] is True
        await create_movement(db, 'owner', 'owner', {**movement(), 'amount':'200', 'request_id':'finish-pay'})
        with pytest.raises(HTTPException) as error:
            await create_movement(db, 'owner', 'owner', {**movement(), 'amount':'1', 'request_id':'zero-payable'})
        assert error.value.detail['code'] == 'operational_supplier_payment_exceeds_payable'
        state = await read(db,'owner')
        assert len(state['movements']) == 2
        assert next(p for p in report(state)['parties'] if p['party_id']=='bank')['actual']=='500.00'
    run(scenario)


def test_future_business_date_rejects_persistently_and_riyadh_midnight_is_allowed():
    async def scenario(db):
        await started(db)
        p = {**movement(), 'amount':'1', 'business_date':'2026-10-11', 'request_id':'future-day'}
        for stamp in ['2026-10-10T20:59:59+00:00', '2026-10-10T21:00:00+00:00']:
            with pytest.raises(HTTPException) as error:
                await create_movement(db,'owner','owner',p,clock=stamp)
            assert error.value.detail['code']=='operational_future_business_date'
            assert error.value.detail['not_applied'] is True
        accepted = await create_movement(db,'owner','owner',{**p,'request_id':'next-day-new'},clock='2026-10-10T21:00:00+00:00')
        assert accepted['business_date']=='2026-10-11'
        assert len((await read(db,'owner'))['movements'])==1
    run(scenario)


def test_entry_balances_do_not_grant_reports_and_beneficiaries_do_not_create_users():
    async def scenario(db):
        await started(db); await grant(db,[WRITE])
        before = await db.users.count_documents({})
        async with client(db) as c:
            supplier=(await c.get('/api/operational-balances/entities/supplier')).json()['items'][0]
            assert supplier['outstanding_payable']=='500.00'
            assert supplier['available_to_pay']=='500.00'
            assert (await c.get('/api/operational-balances/reports')).status_code==403
            assert (await c.post('/api/operational-balances/entities/owner_withdrawal',json={'request_id':'owner-add-1','name':'Arafat','currency':'SAR'})).status_code==403
        async with client(db,actor='owner') as c:
            assert (await c.get('/api/operational-balances/context')).json()['can_create_owner_withdrawal'] is True
            payload={'request_id':'owner-add-1','name':'Arafat','currency':'SAR'}
            result=await c.post('/api/operational-balances/entities/owner_withdrawal',json=payload)
            assert result.status_code==200,result.text
            assert (await c.post('/api/operational-balances/entities/owner_withdrawal',json=payload)).json()==result.json()
            second=await c.post('/api/operational-balances/entities/owner_withdrawal',json={**payload,'request_id':'owner-add-2','name':'Khalid'})
            assert second.status_code==200
            assert (await c.post('/api/operational-balances/entities/owner_withdrawal',json={**payload,'name':'conflicting-name'})).status_code==409
            rows=(await c.get('/api/operational-balances/entities/owner_withdrawal')).json()['items']
            assert len(rows)==3
            from operational_balance_store import digest
            withdrawal=command(party_type='owner_withdrawal',party_id=result.json()['id'],amount='25',request_id='withdraw-owner',expected_session_scope=digest(['owner','owner']))
            assert (await c.post('/api/operational-balances/movements',json=withdrawal)).status_code==200
        summary=report(await read(db,'owner'))['summary']
        assert summary['owner_withdrawals']=='25.00' and summary['operating_expenses_paid']=='0.00'
        assert await db.users.count_documents({})==before
        assert await db.mz2_operational_owner_beneficiaries_v2.count_documents({})==2
    run(scenario)


def test_supplier_credit_reduces_payment_limit_and_failed_retry_cannot_become_payment():
    async def scenario(db):
        await save_opening(db,'owner','owner',opening(),clock=START)
        await save_opening(db,'owner','owner',opening('supplier','supplier','100','for_party','payable-base'),clock=START)
        await save_opening(db,'owner','owner',{'request_id':'start-credit'},finish=True,clock=START)
        # A documented credit creates a receivable and must constrain cash paid.
        await create_movement(db,'owner','owner',{**movement(), 'kind':'correction', 'direction':'incoming',
            'bank_id':None,'amount':'120','request_id':'credit-offset','note':'accepted credit'})
        rejected={**movement(),'amount':'1','request_id':'blocked-credit-pay'}
        for _ in range(2):
            with pytest.raises(HTTPException) as error:
                await create_movement(db,'owner','owner',rejected)
            assert error.value.detail['code']=='operational_supplier_payment_exceeds_payable'
        assert len((await read(db,'owner'))['movements'])==1
    run(scenario)


def test_assigned_custody_entry_card_shows_only_own_balance():
    from test_operational_balance_custody_payments import fixture
    async def scenario(db):
        await fixture(db)
        async with client(db) as c:
            response=await c.get('/api/operational-balances/entities/employee_custody')
            assert response.status_code==200
            rows=response.json()['items']
            assert len(rows)==1 and rows[0]['id']=='employee'
            assert rows[0]['custody_remaining']=='200.00'
            assert 'movements' not in rows[0] and 'audit' not in rows[0]
    run(scenario)



def test_account_statement_matches_actual_including_transfer_correction_and_refund():
    from operational_balance_service import account_statement
    from operational_balance_store import mutate
    async def scenario(db):
        await started(db)
        await create_movement(db,'owner','owner',{**movement(),'party_type':'cash','party_id':'cash','kind':'transfer','amount':'50','request_id':'fund-cash'})
        await create_movement(db,'owner','owner',{**movement(),'party_type':'bank','party_id':'bank','kind':'correction','bank_id':None,'amount':'5','direction':'incoming','request_id':'correct-bank'})
        async def refund(state):
            state['customer_returns']=[{'id':'return-test','status':'refunded','refund_source_type':'bank','refund_source_id':'bank',
                'refund_source_name':'Bank','currency':'SAR','amount':'20.00','refunded_at':'2026-10-10T22:00:00+00:00','refund_reference':'refund-reference'}]
        await mutate(db,'owner',refund)
        state=await read(db,'owner')
        statement=account_statement(state,{'kind':'bank','id':'bank','currency':'SAR','name':'Bank'})
        assert statement['account']['actual']=='935.00'
        assert len(statement['items'])==3
        from decimal import Decimal
        assert Decimal(statement['account']['opening'])+sum(Decimal(r['amount'])*(1 if r['direction']=='incoming' else -1) for r in statement['items'])==Decimal('935')
        assert next(r for r in statement['items'] if r['kind']=='customer_refund')['business_date']=='2026-10-11'
        cash=account_statement(state,{'kind':'cash','id':'cash','currency':'SAR','name':'Cash'})
        assert cash['account']['actual']=='50.00' and len(cash['items'])==1
        assert cash['items'][0]['direction']=='incoming'
    run(scenario)


def test_account_statement_read_grant_and_exact_tenant_account_required():
    from test_operational_balance_app_permissions import READ
    async def scenario(db):
        await started(db); await grant(db,[WRITE])
        async with client(db) as c:
            assert (await c.get('/api/operational-balances/accounts/bank/bank')).status_code==403
        await grant(db,[READ])
        async with client(db) as c:
            response=await c.get('/api/operational-balances/accounts/bank/bank')
            assert response.status_code==200,response.text
            assert response.json()['account']['actual']=='1000.00'
            assert (await c.get('/api/operational-balances/accounts/bank/foreign')).status_code==409
    run(scenario)
