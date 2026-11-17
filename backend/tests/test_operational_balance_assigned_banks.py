import asyncio
import pytest
from fastapi import HTTPException
from test_operational_balance_integration import run, started, movement
from test_operational_balance_app_permissions import grant, client, WRITE, READ
from operational_balance_store import read, digest
from operational_app_banks import validate_assignment
from test_operational_balance_workspace import BASE, grant as workspace_grant, resource


def command(**changes):
    return {**movement(), "source_account_type": "bank", "expected_session_scope": digest(["owner", "staff"]), **changes}


def workspace_command(**changes):
    """Scoped entry helper; keep command() unchanged for owner legacy tests."""
    return {"request_id": "workspace-payment", "expected_session_scope": digest(["owner", "staff"]),
            "party_type": "supplier", "party_id": "supplier", "side": "for_us", "reason": "payment",
            "amount": "300", "business_date": "2026-10-06", "note": "دفعة موثقة",
            "source_type": "bank", "source_id": "bank", **changes}


async def workspace_access(db, *, operations=None, resources=None):
    await workspace_grant(db, operations=operations if operations is not None else ["supplier.payment"],
        resources=resources if resources is not None else [resource("supplier", "supplier"), resource("bank", "bank")])


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
        await started(db); await workspace_access(db)
        await db.mz2_financial_accounts.insert_one({"id":"bank2","user_id":"owner","name":"Other","account_type":"bank","currency":"SAR","status":"active"})
        async with client(db) as c:
            rows=(await c.get(BASE + '/parties?kind=bank')).json()['items']
            assert [r['id'] for r in rows] == ['bank']
            result=await c.post(BASE + '/entries',json=workspace_command(source_id=bank,source_type=kind))
            assert result.status_code==403, result.text
        assert (await read(db,'owner'))['movements']==[]
    run(scenario)


def test_bank_revocation_rechecked_at_persistence(monkeypatch):
    import operational_balance_workspace as routes
    original=routes.create_movement
    async def revoke(db,*args,**kwargs):
        await workspace_grant(db, operations=['supplier.payment'], resources=[resource('supplier','supplier')], version=2)
        return await original(db,*args,**kwargs)
    monkeypatch.setattr(routes,'create_movement',revoke)
    async def scenario(db):
        await started(db); await workspace_access(db)
        async with client(db) as c:
            assert (await c.post(BASE + '/entries',json=workspace_command())).status_code==403
        assert (await read(db,'owner'))['movements']==[]
    run(scenario)


def test_date_note_and_concurrent_retry_are_preserved():
    async def scenario(db):
        await started(db); await workspace_access(db)
        payload=workspace_command(business_date='2026-10-06',note='رسالة البنك الاختبارية')
        async with client(db) as c:
            replies=await asyncio.gather(*[c.post(BASE + '/entries',json=payload) for _ in range(5)])
            assert all(r.status_code==200 for r in replies), [r.text for r in replies]
            assert (await c.post(BASE + '/entries',json={**payload,'business_date':'2026-02-30'})).status_code==422
            assert (await c.post(BASE + '/entries',json={**payload,'business_date':'2026-10-07'})).status_code==409
        rows=(await read(db,'owner'))['movements']
        assert len(rows)==1
        assert rows[0]['business_date']=='2026-10-06' and rows[0]['note']==payload['note']
        assert rows[0]['receipt_id'] is None
    run(scenario)
