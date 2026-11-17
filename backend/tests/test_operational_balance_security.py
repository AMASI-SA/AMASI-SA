"""Local-only authorization race regression; no production server import."""
import httpx
from fastapi import FastAPI

from test_operational_balance_integration import run, started, movement
import operational_balance_routes as routes
import operational_balance_workspace as workspace


async def workspace_grant(db, *, resources=None, operations=None, version=1):
    await db.users.update_one({"id": "staff"}, {"$set": {"operational_balance_access": {
        "owner_id": "owner", "enabled": True, "version": version,
        "operations": ["supplier.payment"] if operations is None else operations,
        "resources": ([{"kind": kind, "id": identity, "use": True, "movements": "own", "balance": False}
                       for kind, identity in [("supplier", "supplier"), ("bank", "bank")]]
                      if resources is None else resources)}}})


def workspace_payment():
    return {"request_id": "scoped-security-payment", "expected_session_scope": routes.digest(["owner", "staff"]),
            "party_type": "supplier", "party_id": "supplier", "side": "for_us", "reason": "payment",
            "amount": "300", "business_date": "2026-10-07", "source_type": "bank", "source_id": "bank"}


def test_revoked_actor_cannot_persist_after_endpoint_authorization(monkeypatch):
    """Simulate revocation during a source read after endpoint scope succeeds."""
    original = workspace.create_movement
    called = []

    async def revoke_then_create(db, owner, actor, payload, **kwargs):
        called.append(True)
        await db.users.update_one({"id": actor}, {"$set": {"disabled": True, "operational_balance_permissions": []}})
        return await original(db, owner, actor, payload, **kwargs)

    monkeypatch.setattr(workspace, "create_movement", revoke_then_create)

    async def scenario(db):
        await started(db)
        await workspace_grant(db)
        app = FastAPI()
        async def principal():
            return {"id": "staff"}
        app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/operational-balances/workspace/entries", json=workspace_payment())
        assert response.status_code == 403, response.text
        assert called == [True]
        state = await routes.read(db, "owner")
        assert state["movements"] == []
    run(scenario)

async def mobile_client(db, *, browser=False):
    await db.mezan_mobile_app_access_v1.insert_one({
        "owner_user_id": "owner", "user_id": "staff", "enabled": True,
        "permissions": ["operational_balance_movements_write", "operational_balance_reports_read"],
    })
    app = FastAPI()
    async def principal():
        return {"id": "staff"} if browser else {"id": "owner", "_mobile_actor_id": "staff", "_session_client": "amasi_mobile"}
    app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_active_membership_never_grants_unscoped_reports_or_choices():
    async def scenario(db):
        await started(db)
        async with await mobile_client(db, browser=True) as client:
            for path in ("reports", "audit", "obligations?party_type=employee&party_id=employee"):
                response = await client.get("/api/operational-balances/" + path)
                assert response.status_code == 403, response.text
            assert (await client.get("/api/operational-balances/context")).status_code == 403
            assert (await client.get("/api/operational-balances/workspace/context")).status_code == 403
            choices = await client.get("/api/operational-balances/obligations?party_type=supplier&party_id=supplier")
            assert choices.status_code == 403 and "items" not in choices.json()
    run(scenario)


def test_explicit_all_movements_grant_shares_only_assigned_bank_and_revokes_live():
    async def scenario(db):
        await started(db)
        await routes.create_movement(db, "owner", "owner", movement())
        await routes.create_movement(db, "owner", "staff", {**movement(), "request_id": "staffmovement", "amount": "100"})
        recorded = (await routes.read(db, "owner"))["movements"]
        assert {row["actor_id"] for row in recorded} == {"owner", "staff"}
        await db[routes.RECEIPTS].insert_many([
            {"_id": "ownerreceipt", "owner_id": "owner", "actor_id": "owner", "content": b"%PDF-safe", "mime": "application/pdf"},
            {"_id": "staffreceipt", "owner_id": "owner", "actor_id": "staff", "content": b"%PDF-safe", "mime": "application/pdf"},
        ])
        await workspace_grant(db, resources=[], operations=[])
        async with await mobile_client(db) as client:
            before_assignment = await client.get("/api/operational-balances/workspace/movements")
            assert before_assignment.status_code == 200, before_assignment.text
            assert before_assignment.json()["items"] == []
            await workspace_grant(db, operations=[], version=2, resources=[{
                "kind": "bank", "id": "bank", "use": False, "movements": "all", "balance": False}])
            response = await client.get("/api/operational-balances/workspace/ledger/bank/bank")
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            money_rows = [item for item in items if item["reason"] != "opening"]
            assert {m["id"] for m in recorded}.issubset({m["id"] for m in money_rows})
            assert response.json()["resource"]["balance"] is None
            assert all(item["party_id"] == "bank" and "receipt_id" not in item for item in items)
            assert (await client.get("/api/operational-balances/receipts/ownerreceipt")).status_code == 403
            assert (await client.get("/api/operational-balances/receipts/staffreceipt")).status_code == 403
            await workspace_grant(db, operations=[], resources=[], version=3)
            assert (await client.get("/api/operational-balances/workspace/movements")).json()["items"] == []
            assert (await client.get("/api/operational-balances/workspace/ledger/bank/bank")).status_code == 404
    run(scenario)


def test_guard_checks_again_after_source_reads_and_resets_after_failure(monkeypatch):
    import operational_balance_service as service
    from operational_balance_store import AUTHORIZATION_GUARD
    original = service.entity
    called = []
    async def revoke_during_source(db, owner, kind, identity, currency):
        called.append((kind, identity))
        row = await original(db, owner, kind, identity, currency)
        await db.users.update_one({"id": "staff"}, {"$set": {"disabled": True}})
        return row
    async def scenario(db):
        await started(db)
        await workspace_grant(db)
        monkeypatch.setattr(service, "entity", revoke_during_source)
        app = FastAPI()
        async def principal():
            return {"id": "staff"}
        app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/operational-balances/workspace/entries", json=workspace_payment())
            assert response.status_code == 403
        assert called
        assert AUTHORIZATION_GUARD.get() is None
        assert (await routes.read(db, "owner"))["movements"] == []
    run(scenario)


def test_inactive_owner_blocks_an_otherwise_authorized_staff_workspace():
    async def scenario(db):
        await started(db)
        await workspace_grant(db)
        app = FastAPI()
        async def principal():
            return {"id": "staff"}
        app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/operational-balances/workspace/context")).status_code == 200
            await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
            assert (await client.post("/api/operational-balances/workspace/entries", json=workspace_payment())).status_code == 403
    run(scenario)

def test_old_mobile_report_flag_does_not_grant_unscoped_reports():
    async def scenario(db):
        await started(db)
        async with await mobile_client(db) as client:
            await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"}, {"$push": {"permissions": "operational_balance_reports_read"}})
            response = await client.get("/api/operational-balances/reports")
            assert response.status_code == 403, response.text
            assert (await client.get("/api/operational-balances/context")).status_code == 403
    run(scenario)


def test_active_member_without_policy_cannot_read_or_write_financial_data():
    async def scenario(db):
        await started(db)
        await db.users.update_one({'id':'staff'},{'$unset':{'operational_balance_permissions':''}})
        async with await mobile_client(db, browser=True) as client:
            await db.mezan_mobile_app_access_v1.update_one({'user_id':'staff'},{'$set':{'permissions':[]}})
            assert (await client.get('/api/operational-balances/context')).status_code == 403
            response=await client.post('/api/operational-balances/movements',json={**movement(),'expected_session_scope':routes.digest(['owner','staff'])})
            assert response.status_code==403, response.text
            assert (await routes.read(db,'owner'))['movements'] == []
    run(scenario)


def test_freeze_remains_available_when_accounting_is_active(monkeypatch):
    from fastapi import HTTPException
    async def blocked_refresh(*args, **kwargs):
        raise HTTPException(409, detail={"code": "operational_accounting_active"})
    monkeypatch.setattr(routes, "refresh", blocked_refresh)
    async def scenario(db):
        await started(db)
        app = FastAPI()
        async def principal():
            return {"id": "owner"}
        app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/operational-balances/freeze", json={"request_id": "freeze-now-1", "reason": "Accounting transition"})
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "frozen"
        assert (await routes.read(db, "owner"))["snapshot"] is not None
    run(scenario)


def test_source_entity_ambiguity_is_actionable_http409():
    async def scenario(db):
        await db.mz2_shipping_setup_v2.insert_many([{"user_id": "owner"}, {"user_id": "owner"}])
        app = FastAPI()
        async def principal():
            return {"id": "owner"}
        app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/operational-balances/entities/courier")
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "operational_entity_setup_incomplete"
    run(scenario)
