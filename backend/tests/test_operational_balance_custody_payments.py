import asyncio
from operational_balance_service import save_opening, report
from operational_balance_store import read
from test_operational_balance_integration import run, opening, START
from test_operational_balance_app_permissions import client
from test_operational_balance_assigned_banks import workspace_command, workspace_access
from test_operational_balance_workspace import BASE, resource


async def fixture(db):
    await db.mezan_employees_v2.insert_many([
        {'id':'employee','user_id':'owner','account_user_id':'staff','name':'Staff','status':'active'},
        {'id':'other-employee','user_id':'owner','account_user_id':'other-user','name':'Other','status':'active'}])
    for payload in [opening(),opening('supplier','supplier','500','for_party','supplier-open'),opening('employee_custody','employee','200',request='custody-open')]:
        await save_opening(db,'owner','owner',payload,clock=START)
    await save_opening(db,'owner','owner',{'request_id':'start-custody'},finish=True,clock=START)
    await workspace_access(db, operations=['supplier.payment','custody.spend','custody.return'], resources=[resource('supplier','supplier'),resource('bank','bank'),resource('cash','cash'),resource('employee_custody','employee')])


def test_custody_supplier_payment_and_cash_return_do_not_debit_bank():
    async def scenario(db):
        await fixture(db)
        async with client(db) as c:
            rows=(await c.get(BASE + '/parties?kind=employee_custody')).json()['items']
            assert [r['id'] for r in rows]==['employee']
            payment=workspace_command(source_id='employee',source_type='employee_custody',amount='80',request_id='supplier-custody')
            responses=await asyncio.gather(*[c.post(BASE + '/entries',json=payment) for _ in range(5)])
            assert all(r.status_code==200 for r in responses),[r.text for r in responses]
            returned=workspace_command(party_type='employee_custody',party_id='employee',source_id='cash',source_type='cash',reason='collection',side='for_party',amount='40',request_id='return-cash')
            assert (await c.post(BASE + '/entries',json=returned)).status_code==200
        state=await read(db,'owner');result=report(state); rows={r['party_id']:r for r in result['parties']}
        assert len(state['movements'])==2
        assert rows['bank']['actual']=='1000.00'
        assert rows['cash']['actual']=='40.00'
        assert rows['employee']['custody_remaining']=='80.00'
        assert rows['supplier']['outstanding_payable']=='420.00'
        assert result['summary']['operating_expenses_paid']=='0.00'
    run(scenario)


def test_custody_overdraw_other_employee_and_unknown_inventory_rejected():
    async def scenario(db):
        await fixture(db)
        async with client(db) as c:
            base=workspace_command(source_id='employee',source_type='employee_custody',amount='201')
            assert (await c.post(BASE + '/entries',json=base)).status_code==409
            assert (await c.post(BASE + '/entries',json={**base,'source_id':'other-employee','amount':'10','request_id':'other-custody'})).status_code==403
            assert (await c.post(BASE + '/entries',json={**base,'reason':'inventory_settlement','request_id':'unknown-stock'})).status_code==422
        assert (await read(db,'owner'))['movements']==[]
    run(scenario)


def test_concurrent_custody_payments_cannot_spend_same_balance_twice():
    async def scenario(db):
        await fixture(db)
        async with client(db) as c:
            replies=await asyncio.gather(*[c.post(BASE + '/entries',json=workspace_command(source_id='employee',source_type='employee_custody',amount='150',request_id=f'competing-{i}')) for i in range(2)])
            assert sorted(r.status_code for r in replies)==[200,409]
        assert len((await read(db,'owner'))['movements'])==1
    run(scenario)
