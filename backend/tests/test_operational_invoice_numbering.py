"""Automatic purchase references with real persistence, retries and legacy API."""
import asyncio
import pytest
from fastapi import HTTPException
from test_operational_balance_integration import run, NOW, movement
from test_operational_balance_inventory import seed, command
from test_operational_balance_app_permissions import client, grant, WRITE
from operational_balance_inventory import save_purchase, assert_no_source_collision
from operational_balance_store import read
from operational_balance_service import report, create_movement
from operational_supplier_adjustments import lookup


def automatic(**patch):
    return command(invoice_number='', supplier_invoice_number='', **patch)


def test_concurrent_new_invoices_and_retries_assign_once():
    async def scenario(db):
        await seed(db)
        payloads = [automatic(request_id=f'auto-{i}') for i in range(4)]
        replies = await asyncio.gather(*[save_purchase(db,'owner','staff',p,clock=NOW) for p in payloads])
        assert {r['invoice_number'] for r in replies} == {f'MZ-PI-{i:06d}' for i in range(1,5)}
        assert len({r['id'] for r in replies}) == 4
        again = await asyncio.gather(*[save_purchase(db,'owner','staff',payloads[0],clock=NOW) for _ in range(4)])
        assert all(r == replies[0] for r in again)
        state = await read(db,'owner')
        assert len(state['inventory_purchases']) == 4
        assert report(state)['summary']['payable'] == '322.00'
        payment = {**movement(), 'request_id':'auto-number-payment', 'amount':'30',
                   'allocations':[{'obligation_id':replies[0]['obligation_id'],'amount':'30'}]}
        await create_movement(db,'owner','staff',payment,clock=NOW)
        await create_movement(db,'owner','staff',payment,clock=NOW)
        view = lookup(await read(db,'owner'),'supplier',replies[0]['invoice_number'])
        assert view['settled'] == '30.00' and view['outstanding'] == '50.50'
    run(scenario)


def test_supplier_reference_lookup_duplicates_and_later_source_collision():
    async def scenario(db):
        await seed(db); await grant(db,[WRITE])
        payload = {**automatic(), 'supplier_invoice_number':'SUP-42'}
        payload.pop('invoice_number')  # Native creation sends no internal number.
        async with client(db) as c:
            result = await c.post('/api/operational-balances/inventory-purchases',json=payload)
            assert result.status_code == 200, result.text
            saved = result.json()
            assert saved['invoice_number'] == 'MZ-PI-000001'
            assert saved['supplier_invoice_number'] == 'SUP-42'
            for number in ('MZ-PI-000001','SUP-42'):
                response = await c.get('/api/operational-balances/inventory-purchases/entry/supplier/'+number)
                assert response.status_code == 200
                assert response.json()['id'] == saved['id']
                assert lookup(await read(db,'owner'),'supplier',number)['id'] == saved['id']
            for duplicate in ({**payload,'request_id':'duplicate-auto'},command(request_id='duplicate-old',invoice_number='SUP-42')):
                response = await c.post('/api/operational-balances/inventory-purchases',json=duplicate)
                assert response.status_code == 409
        await db.mezan_supplier_invoices_v2.insert_one({'user_id':'owner','supplier_id':'supplier','invoice_number':'SUP-42'})
        with pytest.raises(HTTPException) as exc:
            await assert_no_source_collision(db,'owner',await read(db,'owner'))
        assert exc.value.detail['code'] == 'inventory_invoice_source_conflict'
    run(scenario)


def test_parallel_same_supplier_reference_creates_only_one_invoice():
    async def scenario(db):
        await seed(db)
        results = await asyncio.gather(*[
            save_purchase(db,'owner','staff',{**automatic(request_id=f'same-ref-{i}'),
                          'supplier_invoice_number':'SAME'},clock=NOW)
            for i in range(3)], return_exceptions=True)
        assert sum(isinstance(r,HTTPException) for r in results) == 2
        state = await read(db,'owner')
        assert len(state['inventory_purchases']) == 1
        assert report(state)['summary']['payable'] == '80.50'
    run(scenario)


def test_legacy_retry_and_reserved_number_collision():
    async def scenario(db):
        await seed(db); await grant(db,[WRITE])
        old = command(invoice_number='MZ-PI-000001')
        original = await save_purchase(db,'owner','staff',old,clock=NOW)
        async with client(db) as c:
            reply = await c.post('/api/operational-balances/inventory-purchases',json=old)
            assert reply.status_code == 200, reply.text
            assert reply.json() == original
            reply = await c.post('/api/operational-balances/inventory-purchases',json=automatic(request_id='next-auto'))
            assert reply.status_code == 200, reply.text
            assert reply.json()['invoice_number'] == 'MZ-PI-000002'
            assert reply.json()['supplier_invoice_number'] == ''
        assert original['invoice_number'] == 'MZ-PI-000001'
        assert 'supplier_invoice_number' not in original
    run(scenario)


@pytest.mark.parametrize('patch',[{'invoice_number':'manual','supplier_invoice_number':''},{'invoice_number':''}])
def test_invalid_number_modes_leave_state_unchanged(patch):
    async def scenario(db):
        await seed(db)
        before = await read(db,'owner')
        with pytest.raises(HTTPException):
            await save_purchase(db,'owner','staff',command(**patch),clock=NOW)
        assert await read(db,'owner') == before
    run(scenario)
