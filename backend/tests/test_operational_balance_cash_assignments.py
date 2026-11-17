import asyncio
import pytest
from fastapi import HTTPException
from operational_app_banks import validate_assignment
from operational_balance_service import report
from operational_balance_store import read
from test_operational_balance_integration import run, started
from test_operational_balance_app_permissions import client, grant, WRITE
from test_operational_balance_assigned_banks import workspace_command, workspace_access
from test_operational_balance_workspace import BASE, resource


def test_cash_assignment_must_be_owner_mz2_cash():
    async def scenario(db):
        valid = {'bank_ids': [], 'cash_ids': ['cash'], 'default_bank_id': None}
        assert await validate_assignment(db, 'owner', [WRITE], True, valid) == valid
        for identity in ['bank', 'foreign']:
            with pytest.raises(HTTPException):
                await validate_assignment(db, 'owner', [WRITE], True, {**valid, 'cash_ids':[identity]})
    run(scenario)


def test_funding_cash_supplier_payment_and_bank_return_are_once_only():
    async def scenario(db):
        await started(db)
        await workspace_access(db, operations=['supplier.payment','accounts.transfer'], resources=[resource('supplier','supplier'),resource('bank','bank'),resource('cash','cash')])
        async with client(db) as c:
            assert [r['id'] for r in (await c.get(BASE + '/parties?kind=cash')).json()['items']] == ['cash']
            funding=workspace_command(party_type='cash',party_id='cash',reason='transfer',amount='200',request_id='cash-funding-1')
            replies=await asyncio.gather(*[c.post(BASE + '/entries',json=funding) for _ in range(5)])
            assert all(r.status_code==200 for r in replies),[r.text for r in replies]
            payment=workspace_command(source_id='cash',source_type='cash',amount='50',request_id='cash-payment-1')
            assert (await c.post(BASE + '/entries',json=payment)).status_code==200
            returned={**funding,'party_type':'bank','party_id':'bank','source_type':'cash','source_id':'cash','amount':'30','request_id':'cash-return-1'}
            assert (await c.post(BASE + '/entries',json=returned)).status_code==200
        state=await read(db,'owner'); result=report(state)
        assert len(state['movements'])==3
        balances={p['party_id']:p['actual'] for p in result['parties']}
        assert balances['bank']=='830.00' and balances['cash']=='120.00'
        assert result['summary']['actual_liquidity']=='950.00'
        assert result['summary']['operating_expenses_paid']=='0.00'
        assert result['summary']['payable']=='450.00'
    run(scenario)


def test_unassigned_destination_and_revoked_cash_reject_without_writes():
    async def scenario(db):
        await started(db); await workspace_access(db, operations=['supplier.payment','accounts.transfer'])
        async with client(db) as c:
            for payload in [workspace_command(party_type='cash',party_id='cash',reason='transfer'),workspace_command(source_id='cash',source_type='cash')]:
                assert (await c.post(BASE + '/entries',json=payload)).status_code==403
        assert (await read(db,'owner'))['movements']==[]
    run(scenario)


def test_cash_to_cash_preserves_liquidity_and_rejects_same_account():
    async def scenario(db):
        await started(db); await grant(db,[WRITE])
        await db.mz2_financial_accounts.insert_one({'id':'cash2','user_id':'owner','name':'Second cashbox','account_type':'cash','currency':'SAR','status':'active'})
        await workspace_access(db, operations=['accounts.transfer'], resources=[resource('bank','bank'),resource('cash','cash'),resource('cash','cash2')])
        async with client(db) as c:
            funding=workspace_command(party_type='cash',party_id='cash',reason='transfer',amount='100',request_id='fund-cash')
            assert (await c.post(BASE + '/entries',json=funding)).status_code==200
            transfer={**funding,'party_id':'cash2','source_id':'cash','source_type':'cash','amount':'30','request_id':'cash-to-cash'}
            assert (await c.post(BASE + '/entries',json=transfer)).status_code==200
            rejected=await c.post(BASE + '/entries',json={**transfer,'party_id':'cash','request_id':'same-account'})
            assert rejected.status_code==422 and rejected.json()['detail']['code']=='operational_workspace_same_account',rejected.text
        result=report(await read(db,'owner'))
        balances={p['party_id']:p['actual'] for p in result['parties']}
        assert balances['cash']=='70.00' and balances['cash2']=='30.00'
        assert result['summary']['actual_liquidity']=='1000.00'
        assert len((await read(db,'owner'))['movements'])==2
    run(scenario)


@pytest.mark.parametrize('mobile',[True,False])
def test_only_owner_can_create_cash_without_opening_or_movement(mobile):
    async def scenario(db):
        await started(db); await grant(db,[WRITE])
        payload={'name':'صندوق الاختبار','currency':'SAR','request_id':'cash-create-test'}
        async with client(db,'staff',mobile=mobile) as c:
            assert (await c.post('/api/operational-balances/entities/cash',json=payload)).status_code==403
        async with client(db,'owner',mobile=mobile) as c:
            a=await c.post('/api/operational-balances/entities/cash',json=payload)
            b=await c.post('/api/operational-balances/entities/cash',json=payload)
            assert a.status_code==b.status_code==200,(a.text,b.text)
            assert a.json()['id']==b.json()['id']
            assert await db.mz2_financial_accounts.count_documents({'id':a.json()['id']})==1
        state=await read(db,'owner')
        assert len(state['openings'])==2 and state['movements']==[]
    run(scenario)
