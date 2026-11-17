"""Approved native return/exchange permissions, real Mongo and ASGI routes."""
import asyncio
import pytest
from test_operational_balance_integration import run
from test_operational_balance_app_permissions import client, grant, WRITE, READ
from test_operational_balance_customer_returns import fixture, command as return_command
from test_operational_balance_exchanges import seed, command as exchange_command, purchase
from operational_balance_store import read, digest


@pytest.mark.parametrize('permissions', [[], [WRITE], [READ], [WRITE, READ]])
@pytest.mark.parametrize('kind', ['returns', 'exchanges'])
@pytest.mark.parametrize('actor', ['owner', 'staff'])
def test_legacy_case_owner_matrix_staff_denial_and_owner_retry(permissions, kind, actor):
    async def scenario(db):
        await (fixture(db) if kind == 'returns' else seed(db))
        await grant(db, permissions)
        path='/api/operational-balances/customer-'+kind
        allowed = actor == 'owner'
        async with client(db, actor) as c:
            assert (await c.get(path)).status_code == (200 if allowed else 403)
            lookup=await c.get(path+'/entry/1001')
            assert lookup.status_code == (200 if allowed else 403)
            if kind == 'returns': payload=return_command()
            else:
                # The owner prepares valid input so staff denial cannot pass
                # merely because the payload was invalid.
                async with client(db,actor='owner',mobile=False) as browser:
                    q=await browser.get('/api/operational-balances/customer-returns/shipping-quote/courier/carrier?order_number=1001')
                payload=exchange_command(shipping_quote_hash=q.json()['quote_hash'])
            payload['expected_session_scope'] = digest(['owner',actor])
            quote=await c.get('/api/operational-balances/customer-returns/shipping-quote/courier/carrier?order_number=1001')
            assert quote.status_code == (200 if allowed else 403)
            results=await asyncio.gather(*[c.post(path,json=payload) for _ in range(3)])
            assert all(r.status_code == (200 if allowed else 403) for r in results), [r.text for r in results]
            if allowed:
                assert len({r.json()['id'] for r in results})==1
                entry=(await c.get(path+'/entry/1001')).json()['items']
                assert len(entry)==1
                assert entry[0]['id'] == results[0].json()['id']
        state=await read(db,'owner')
        assert len(state.get('customer_'+kind,[]))==(1 if allowed else 0)
        assert await db.mezan_supplier_invoices_v2.count_documents({})==0
    run(scenario)


def test_owner_exchange_purchase_is_once_and_legacy_staff_flags_cannot_authorize_it():
    async def scenario(db):
        await seed(db); await grant(db,[WRITE])
        async with client(db, 'owner') as c:
            q=(await c.get('/api/operational-balances/customer-returns/shipping-quote/courier/carrier?order_number=1001')).json()
            created=await c.post('/api/operational-balances/customer-exchanges',json=exchange_command(expected_session_scope=digest(['owner','owner']),shipping_quote_hash=q['quote_hash']))
            assert created.status_code==200,created.text
            case=created.json(); path=f'/api/operational-balances/customer-exchanges/{case["id"]}/actions'
            payload=purchase(case)
            async with client(db) as staff:
                for permissions in ([READ], [WRITE], [READ, WRITE]):
                    await grant(db,permissions)
                    assert (await staff.post(path,json=payload)).status_code==403
            payload['expected_session_scope'] = digest(['owner','owner'])
            result=await c.post(path,json=payload)
            assert result.status_code==200,result.text
            assert len(result.json()['purchases']) == 1
            assert (await c.post(path,json=payload)).json()==result.json()
        state=await read(db,'owner')
        assert len(state['customer_exchanges'][0]['purchases'])==1
        assert await db.mezan_supplier_invoices_v2.count_documents({})==0
    run(scenario)


@pytest.mark.parametrize('revoke', [False, True])
@pytest.mark.parametrize('padding', ['', ' '])
def test_legacy_return_never_writes_with_stale_or_padded_staff_request(revoke,padding):
    async def scenario(db):
        await fixture(db); await grant(db,[WRITE])
        if revoke: await grant(db,[READ])
        if not revoke:
            await db.mezan_mobile_app_access_v1.update_one({'user_id':'staff'}, {'$set':{'operational_banks':{'bank_ids':[],'default_bank_id':None}}})
        async with client(db) as c:
            result=await c.post('/api/operational-balances/customer-returns',json=return_command(status=padding+'refunded'+padding,refund_source_type=padding+'bank'+padding,amount='50'))
            # The actor gate rejects this legacy path before payload validation.
            assert result.status_code==403,result.text
        assert (await read(db,'owner')).get('customer_returns',[])==[]
    run(scenario)
