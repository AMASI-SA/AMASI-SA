"""Approved native return/exchange permissions, real Mongo and ASGI routes."""
import asyncio
import pytest
from test_operational_balance_integration import run
from test_operational_balance_app_permissions import client, grant, WRITE, READ
from test_operational_balance_customer_returns import fixture, command as return_command
from test_operational_balance_exchanges import seed, command as exchange_command, purchase
from operational_balance_store import read


@pytest.mark.parametrize('permissions', [[], [WRITE], [READ], [WRITE, READ]])
@pytest.mark.parametrize('kind', ['returns', 'exchanges'])
def test_native_case_permission_matrix_and_retry(permissions, kind):
    async def scenario(db):
        await (fixture(db) if kind == 'returns' else seed(db))
        await grant(db, permissions)
        path='/api/operational-balances/customer-'+kind
        async with client(db) as c:
            assert (await c.get(path)).status_code == (200 if READ in permissions else 403)
            lookup=await c.get(path+'/entry/1001')
            assert lookup.status_code == (200 if WRITE in permissions else 403)
            if kind == 'returns': payload=return_command()
            else:
                # Trusted browser quote only prepares the test input; native quote
                # independently requires the write permission below.
                async with client(db,mobile=False) as browser:
                    q=await browser.get('/api/operational-balances/customer-returns/shipping-quote/courier/carrier?order_number=1001')
                payload=exchange_command(shipping_quote_hash=q.json()['quote_hash'])
            quote=await c.get('/api/operational-balances/customer-returns/shipping-quote/courier/carrier?order_number=1001')
            assert quote.status_code == (200 if WRITE in permissions else 403)
            results=await asyncio.gather(*[c.post(path,json=payload) for _ in range(3)])
            assert all(r.status_code == (200 if WRITE in permissions else 403) for r in results), [r.text for r in results]
            if WRITE in permissions:
                assert len({r.json()['id'] for r in results})==1
                data=results[0].json()
                if READ not in permissions:
                    assert not {'summary','purchases','contributions','amount','refund_reference'} & data.keys()
                entry=(await c.get(path+'/entry/1001')).json()['items']
                assert len(entry)==1
                assert not {'summary','purchases','contributions','amount','refund_reference'} & entry[0].keys()
        state=await read(db,'owner')
        assert len(state.get('customer_'+kind,[]))==(1 if WRITE in permissions else 0)
        assert await db.mezan_supplier_invoices_v2.count_documents({})==0
    run(scenario)


def test_native_exchange_purchase_requires_write_and_does_not_grant_reports():
    async def scenario(db):
        await seed(db); await grant(db,[WRITE])
        async with client(db) as c:
            q=(await c.get('/api/operational-balances/customer-returns/shipping-quote/courier/carrier?order_number=1001')).json()
            created=await c.post('/api/operational-balances/customer-exchanges',json=exchange_command(shipping_quote_hash=q['quote_hash']))
            assert created.status_code==200,created.text
            case=created.json(); path=f'/api/operational-balances/customer-exchanges/{case["id"]}/actions'
            payload=purchase(case)
            await grant(db,[READ])
            assert (await c.post(path,json=payload)).status_code==403
            await grant(db,[WRITE])
            result=await c.post(path,json=payload)
            assert result.status_code==200,result.text
            assert 'purchases' not in result.json() and 'summary' not in result.json()
            assert (await c.post(path,json=payload)).json()==result.json()
        state=await read(db,'owner')
        assert len(state['customer_exchanges'][0]['purchases'])==1
        assert await db.mezan_supplier_invoices_v2.count_documents({})==0
    run(scenario)


@pytest.mark.parametrize('revoke', [False, True])
@pytest.mark.parametrize('padding', ['', ' '])
def test_return_bank_assignment_is_rechecked_at_persistence(monkeypatch,revoke,padding):
    import operational_customer_returns as returns
    original=returns.save_case
    async def wrapped(db,*args,**kwargs):
        if revoke: await grant(db,[READ])
        return await original(db,*args,**kwargs)
    monkeypatch.setattr(returns,'save_case',wrapped)
    async def scenario(db):
        await fixture(db); await grant(db,[WRITE])
        if not revoke:
            await db.mezan_mobile_app_access_v1.update_one({'user_id':'staff'}, {'$set':{'operational_banks':{'bank_ids':[],'default_bank_id':None}}})
        async with client(db) as c:
            result=await c.post('/api/operational-balances/customer-returns',json=return_command(status=padding+'refunded'+padding,refund_source_type=padding+'bank'+padding,amount='50'))
            # Literal fields reject whitespace at validation before persistence.
            assert result.status_code==(422 if padding else 403),result.text
        assert (await read(db,'owner')).get('customer_returns',[])==[]
    run(scenario)
