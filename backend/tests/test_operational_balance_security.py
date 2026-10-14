"""Local-only authorization race regression; no production server import."""
import httpx
from fastapi import FastAPI

from test_operational_balance_integration import run, started, movement
import operational_balance_routes as routes


def test_revoked_actor_cannot_persist_after_endpoint_authorization(monkeypatch):
    """Simulate revocation during a source read after endpoint scope succeeds."""
    original = routes.create_movement

    async def revoke_then_create(db, owner, actor, payload, **kwargs):
        await db.users.update_one({"id": actor}, {"$set": {"disabled": True, "operational_balance_permissions": []}})
        return await original(db, owner, actor, payload, **kwargs)

    monkeypatch.setattr(routes, "create_movement", revoke_then_create)

    async def scenario(db):
        await started(db)
        app = FastAPI()
        async def principal():
            return {"id": "staff"}
        app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/operational-balances/movements", json={**movement(), "expected_session_scope": routes.digest(["owner", "staff"])})
        assert response.status_code == 403, response.text
        state = await routes.read(db, "owner")
        assert state["movements"] == []
    run(scenario)

async def mobile_client(db):
    await db.mezan_mobile_app_access_v1.insert_one({
        "owner_user_id": "owner", "user_id": "staff", "enabled": True,
        "permissions": ["app.page.operational_movements", "app.action.operational_movements.create"],
    })
    app = FastAPI()
    async def principal():
        return {"id": "owner", "_mobile_actor_id": "staff", "_session_client": "amasi_mobile"}
    app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_active_member_needs_no_operational_grants_for_reports_or_choices():
    async def scenario(db):
        await started(db)
        async with await mobile_client(db) as client:
            for path in ("reports", "audit", "obligations?party_type=employee&party_id=employee"):
                response = await client.get("/api/operational-balances/" + path)
                assert response.status_code == 200, response.text
            context = (await client.get("/api/operational-balances/context")).json()
            assert context["permissions"]["reports"] is True
            assert context["issues"] == []
            assert context["session_scope"] == routes.digest(["owner", "staff"])
            assert context["session_scope"] != routes.digest(["different-owner", "staff"])
            assert context["session_scope"] != routes.digest(["owner", "other-staff"])
            choices = await client.get("/api/operational-balances/obligations?party_type=supplier&party_id=supplier")
            assert choices.status_code == 200
            assert choices.json()["items"]
            # Settlement choices expose payable/estimated portions, not source
            # facts, audit payloads, employee salaries or unrelated parties.
            assert set(choices.json()["items"][0]) <= {"id", "kind", "party_type", "party_id", "currency", "direction", "outstanding", "expected", "available_to_pay", "pending_confirmation", "label", "business_date"}
            assert all(row["party_type"] == "supplier" and row["party_id"] == "supplier"
                       for row in choices.json()["items"])
    run(scenario)


def test_active_members_share_operational_history_within_same_owner():
    async def scenario(db):
        await started(db)
        await routes.create_movement(db, "owner", "owner", movement())
        await routes.create_movement(db, "owner", "staff", {**movement(), "request_id": "staffmovement", "amount": "100"})
        await db[routes.RECEIPTS].insert_many([
            {"_id": "ownerreceipt", "owner_id": "owner", "actor_id": "owner", "content": b"%PDF-safe", "mime": "application/pdf"},
            {"_id": "staffreceipt", "owner_id": "owner", "actor_id": "staff", "content": b"%PDF-safe", "mime": "application/pdf"},
        ])
        async with await mobile_client(db) as client:
            items = (await client.get("/api/operational-balances/movements")).json()["items"]
            assert len(items) == 2 and {m["actor_id"] for m in items} == {"owner", "staff"}
            assert (await client.get("/api/operational-balances/receipts/ownerreceipt")).status_code == 200
            assert (await client.get("/api/operational-balances/receipts/staffreceipt")).status_code == 200
    run(scenario)


def test_guard_checks_again_after_source_reads_and_resets_after_failure(monkeypatch):
    import operational_balance_service as service
    from operational_balance_store import AUTHORIZATION_GUARD
    original = service.entity
    async def revoke_during_source(db, owner, kind, identity, currency):
        row = await original(db, owner, kind, identity, currency)
        await db.users.update_one({"id": "staff"}, {"$set": {"disabled": True}})
        return row
    async def scenario(db):
        await started(db)
        monkeypatch.setattr(service, "entity", revoke_during_source)
        app = FastAPI()
        async def principal():
            return {"id": "staff"}
        app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/operational-balances/movements", json={**movement(), "expected_session_scope": routes.digest(["owner", "staff"])})
            assert response.status_code == 403
        assert AUTHORIZATION_GUARD.get() is None
        assert (await routes.read(db, "owner"))["movements"] == []
    run(scenario)


def test_inactive_owner_blocks_staff_and_browser_view_still_reports():
    async def scenario(db):
        await started(db)
        app = FastAPI()
        async def principal():
            return {"id": "staff"}
        app.include_router(routes.make_operational_balance_router(db, principal), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/operational-balances/reports")).status_code == 200
            await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
            assert (await client.post("/api/operational-balances/movements", json={**movement(), "expected_session_scope": routes.digest(["owner", "staff"])})).status_code == 403
    run(scenario)

def test_independent_mobile_report_grant_allows_reports():
    async def scenario(db):
        await started(db)
        async with await mobile_client(db) as client:
            await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"}, {"$push": {"permissions": "app.page.operational_reports"}})
            response = await client.get("/api/operational-balances/reports")
            assert response.status_code == 200, response.text
            assert (await client.get("/api/operational-balances/context")).json()["permissions"]["reports"] is True
    run(scenario)


def test_active_member_without_any_operational_grants_has_all_functions():
    async def scenario(db):
        await started(db)
        await db.users.update_one({'id':'staff'},{'$unset':{'operational_balance_permissions':''}})
        async with await mobile_client(db) as client:
            await db.mezan_mobile_app_access_v1.update_one({'user_id':'staff'},{'$set':{'permissions':[]}})
            context=(await client.get('/api/operational-balances/context')).json()
            assert all(context['permissions'].values())
            response=await client.post('/api/operational-balances/movements',json={**movement(),'expected_session_scope':routes.digest(['owner','staff'])})
            assert response.status_code==200, response.text
            assert (await routes.read(db,'owner'))['movements'][0]['actor_id']=='staff'
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
