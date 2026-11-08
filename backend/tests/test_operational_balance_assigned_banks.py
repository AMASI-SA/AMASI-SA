import asyncio
import pytest
from fastapi import HTTPException
from test_operational_balance_integration import run, started, movement
from test_operational_balance_app_permissions import grant, client, WRITE, READ
from operational_balance_store import read, digest
from operational_app_banks import validate_assignment


def command(**changes):
    return {**movement(), "source_account_type": "bank", "expected_session_scope": digest(["owner", "staff"]), **changes}


@pytest.mark.parametrize("config", [{}, {"bank_ids": []}, {"bank_ids": ["cash"]}, {"bank_ids": ["foreign"]}, {"bank_ids": ["bank", "bank"]}])
def test_assignment_rejects_invalid_mz2_banks(config):
    async def scenario(db):
        with pytest.raises(HTTPException):
            await validate_assignment(db, "owner", [WRITE], True, config)
    run(scenario)


def test_multiple_banks_require_explicit_default_and_single_is_automatic():
    async def scenario(db):
        await db.mz2_financial_accounts.insert_one({"id":"bank2","user_id":"owner","name":"Bank 2","account_type":"bank","currency":"SAR","status":"active"})
        assert (await validate_assignment(db,"owner",[WRITE],True,{"bank_ids":["bank"]}))["default_bank_id"] == "bank"
        with pytest.raises(HTTPException): await validate_assignment(db,"owner",[WRITE],True,{"bank_ids":["bank","bank2"]})
        config={"bank_ids":["bank","bank2"],"default_bank_id":"bank2"}
        assert await validate_assignment(db,"owner",[WRITE],True,config) == config
        assert (await validate_assignment(db,"owner",[READ],True,config))["bank_ids"] == []
    run(scenario)


@pytest.mark.parametrize("bank,kind", [("cash","cash"),("foreign","bank"),("bank2","bank")])
def test_direct_api_rejects_unassigned_bank_without_partial_write(bank,kind):
    async def scenario(db):
        await started(db); await grant(db,[WRITE])
        await db.mz2_financial_accounts.insert_one({"id":"bank2","user_id":"owner","name":"Other","account_type":"bank","currency":"SAR","status":"active"})
        async with client(db) as c:
            rows=(await c.get('/api/operational-balances/entities/bank')).json()['items']
            assert [r['id'] for r in rows] == ['bank']
            result=await c.post('/api/operational-balances/movements',json=command(bank_id=bank,source_account_type=kind))
            assert result.status_code==403, result.text
        assert (await read(db,'owner'))['movements']==[]
    run(scenario)


def test_bank_revocation_rechecked_at_persistence(monkeypatch):
    import operational_balance_routes as routes
    original=routes.create_movement
    async def revoke(db,*args,**kwargs):
        await db.mezan_mobile_app_access_v1.update_one({'user_id':'staff'},{'$set':{'operational_banks':{'bank_ids':[]}}})
        return await original(db,*args,**kwargs)
    monkeypatch.setattr(routes,'create_movement',revoke)
    async def scenario(db):
        await started(db); await grant(db,[WRITE])
        async with client(db) as c:
            assert (await c.post('/api/operational-balances/movements',json=command())).status_code==403
        assert (await read(db,'owner'))['movements']==[]
    run(scenario)


def test_date_note_and_concurrent_retry_are_preserved():
    async def scenario(db):
        await started(db); await grant(db,[WRITE])
        payload=command(business_date='2026-10-06',note='رسالة البنك الاختبارية',receipt_id=None)
        async with client(db) as c:
            replies=await asyncio.gather(*[c.post('/api/operational-balances/movements',json=payload) for _ in range(5)])
            assert all(r.status_code==200 for r in replies), [r.text for r in replies]
            assert (await c.post('/api/operational-balances/movements',json={**payload,'business_date':'2026-02-30'})).status_code==422
            assert (await c.post('/api/operational-balances/movements',json={**payload,'business_date':'2026-10-07'})).status_code==409
        rows=(await read(db,'owner'))['movements']
        assert len(rows)==1
        assert rows[0]['business_date']=='2026-10-06' and rows[0]['note']==payload['note']
        assert rows[0]['receipt_id'] is None
    run(scenario)
