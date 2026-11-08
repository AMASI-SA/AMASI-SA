"""Native permission matrix over real local Mongo + ASGI. No production imports."""
import httpx
import pytest
from fastapi import FastAPI, Request, HTTPException
from test_operational_balance_integration import run, started, movement
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import read, digest
from mobile_app_request_context import mobile_app_request_user
from mobile_app_permissions import (OPERATIONAL_APP_WRITE as WRITE, OPERATIONAL_APP_READ as READ,
    effective_mobile_app_permissions, validate_mobile_app_permissions)

async def grant(db, permissions):
    await db.mezan_mobile_app_access_v1.update_one({"owner_user_id":"owner", "user_id":"staff"},
        {"$set":{"enabled":True,"permissions":permissions,
                 "operational_banks":{"bank_ids":["bank"],"default_bank_id":"bank"}}}, upsert=True)

def client(db, actor="staff", mobile=True):
    app=FastAPI()
    async def principal(request: Request):
        if not actor: raise HTTPException(401)
        user=await db.users.find_one({"id":actor}, {"_id":0})
        if mobile: user["_session_client"]="amasi_mobile"
        return await mobile_app_request_user(db, user, path=request.url.path, method=request.method)
    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated")

@pytest.mark.parametrize("permissions", [[],[WRITE],[READ],[WRITE,READ]])
@pytest.mark.parametrize("direction", ["incoming","outgoing"])
def test_independent_native_permission_matrix(permissions, direction):
    async def scenario(db):
        await started(db); await grant(db, permissions)
        async with client(db) as c:
            ctx=await c.get("/api/operational-balances/context")
            assert ctx.status_code==(200 if permissions else 403)
            if permissions:
                assert ctx.json()["permissions"]=={"view":True,"move":WRITE in permissions,"manage":False,"reports":READ in permissions}
            for path in ["reports","movements","obligations?party_type=supplier&party_id=supplier"]:
                r=await c.get("/api/operational-balances/"+path)
                assert r.status_code==(200 if READ in permissions else 403), (path,r.text)
            assert (await c.get("/api/operational-balances/entities/bank")).status_code==(200 if permissions else 403)
            payload={**movement(),"expected_session_scope":digest(["owner","staff"]),"direction":direction,"kind":"collection" if direction=="incoming" else "payment"}
            r=await c.post("/api/operational-balances/movements",json=payload)
            assert r.status_code==(200 if WRITE in permissions else 403),r.text
            again=await c.post("/api/operational-balances/movements",json=payload)
            assert again.status_code==r.status_code
            if WRITE in permissions: assert again.json()==r.json()
        state=await read(db,"owner")
        assert len(state["movements"])==(1 if WRITE in permissions else 0)
        if WRITE in permissions:
            assert state["movements"][0]["actor_id"]=="staff"
            assert state["movements"][0]["receipt_id"] is None
    run(scenario)

@pytest.mark.parametrize("actor", ["staff","owner"])
@pytest.mark.parametrize("method,path", [("GET","audit"),("GET","receipts/test"),("POST","openings"),
    ("POST","finish"),("POST","freeze"),("POST","supplier-returns"),("POST","entities/cash"),("POST","entities/external_person"),("POST","receipts")])
def test_no_native_opening_audit_or_administration_even_with_both_grants(actor,method,path):
    async def scenario(db):
        await started(db);await grant(db,[WRITE,READ])
        async with client(db,actor) as c:
            r=await c.request(method,"/api/operational-balances/"+path,json={})
            assert r.status_code==(422 if actor == "owner" and path in {"entities/cash", "entities/external_person"} else 403),r.text
        assert (await read(db,"owner"))["movements"]==[]
    run(scenario)

def test_native_correction_is_not_movement_entry():
    async def scenario(db):
        await started(db);await grant(db,[WRITE,READ])
        async with client(db) as c:
            r=await c.post("/api/operational-balances/movements",json={**movement(),"kind":"correction","expected_session_scope":digest(["owner","staff"])})
            assert r.status_code==403
        assert (await read(db,"owner"))["movements"]==[]
    run(scenario)

def test_live_permission_revocation_at_persistence(monkeypatch):
    import operational_balance_routes as routes
    original=routes.create_movement
    async def revoke(db,*args,**kwargs):
        await grant(db,[READ])
        return await original(db,*args,**kwargs)
    monkeypatch.setattr(routes,"create_movement",revoke)
    async def scenario(db):
        await started(db);await grant(db,[WRITE])
        async with client(db) as c:
            r=await c.post("/api/operational-balances/movements",json={**movement(),"expected_session_scope":digest(["owner","staff"])})
            assert r.status_code==403,r.text
        assert (await read(db,"owner"))["movements"]==[]
    run(scenario)

def test_unauthenticated_and_disabled_accounts():
    async def scenario(db):
        await started(db);await grant(db,[WRITE,READ])
        async with client(db,None) as c: assert (await c.get("/api/operational-balances/reports")).status_code==401
        await db.users.update_one({"id":"staff"},{"$set":{"disabled":True}})
        async with client(db) as c: assert (await c.get("/api/operational-balances/reports")).status_code==403
    run(scenario)

def test_native_permission_admin_keeps_staff_identity():
    async def scenario(db):
        user=await db.users.find_one({"id":"staff"});user["_session_client"]="amasi_mobile"
        resolved=await mobile_app_request_user(db,user,path="/api/employees-v2/management/employees/e/mobile-app-permissions",method="PUT")
        assert resolved["id"]=="staff" and resolved["role"]!="owner"
        from employees_v2_routes import _require_owner
        with pytest.raises(HTTPException): _require_owner(resolved)
    run(scenario)

@pytest.mark.parametrize("permissions", [[],[WRITE],[READ],[WRITE,READ]])
def test_grants_have_no_parent_or_implicit_manager_expansion(permissions):
    assert validate_mobile_app_permissions(permissions)==sorted(permissions)
    granted=effective_mobile_app_permissions({"enabled":True,"permissions":["app.role.manager",*permissions]})
    assert set(granted)&{WRITE,READ}==set(permissions)

def test_browser_movement_requires_separate_explicit_accounting_write_grant():
    async def scenario(db):
        await started(db); await grant(db,[])
        async with client(db,mobile=False) as c:
            assert (await c.get("/api/operational-balances/reports")).status_code==200
            assert (await c.get("/api/operational-balances/audit")).status_code==200
            payload={**movement(),"expected_session_scope":digest(["owner","staff"])}
            assert (await c.post("/api/operational-balances/movements",json=payload)).status_code==403
            await db.users.update_one({"id":"staff"},{"$set":{"accounting_permissions":["accounting.movements.view"]}})
            assert (await c.post("/api/operational-balances/movements",json=payload)).status_code==403
            await db.users.update_one({"id":"staff"},{"$set":{"accounting_permissions":["accounting.movements.import"]}})
            assert (await c.post("/api/operational-balances/movements",json=payload)).status_code==200
    run(scenario)


def test_owner_grants_and_revokes_both_permissions_independently(monkeypatch):
    import employees_v2_routes as employees
    from mobile_app_permissions import mobile_app_access_for_user
    async def response(_db, *, owner_id): return {"owner_id":owner_id}
    monkeypatch.setattr(employees,"_employee_management_response",response)
    async def scenario(db):
        await db.mezan_employees_v2.insert_one({"id":"employee-staff","user_id":"owner","display_name":"موظف", "status":"active","account_user_id":"staff","version":1})
        async def owner(): return await db.users.find_one({"id":"owner"})
        app=FastAPI();app.include_router(employees.make_employees_v2_router(db,owner),prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated") as c:
            for permissions in [[WRITE],[READ],[WRITE,READ],[]]:
                r=await c.put("/api/employees-v2/management/employees/employee-staff/mobile-app-permissions",json={"confirmation":employees.EMPLOYEE_MOBILE_APP_PERMISSIONS_CONFIRMATION,"enabled":True,"permissions":permissions,"operational_banks":{"bank_ids":["bank"],"default_bank_id":"bank"}})
                assert r.status_code==200,r.text
                stored = await db.mezan_mobile_app_access_v1.find_one({"owner_user_id":"owner","user_id":"staff"})
                assert stored["operational_banks"] == {
                    "bank_ids": ["bank"] if WRITE in permissions or READ in permissions else [],
                    "default_bank_id": "bank" if WRITE in permissions else None,
                }
                access=await mobile_app_access_for_user(db,await db.users.find_one({"id":"staff"}))
                assert set(access["permissions"])==set(permissions)
                assert (await db.users.find_one({"id":"staff"}))["role"]=="employee"
    run(scenario)


def test_mobile_owner_adds_cash_and_external_without_creating_money():
    async def scenario(db):
        await started(db)
        await grant(db, [WRITE, READ])
        for kind in ("cash", "external_person"):
            payload = {"name": "تجربة إنشاء " + kind, "currency": "SAR", "request_id": "owner-mobile-" + kind}
            async with client(db, "staff") as c:
                denied = await c.post(f"/api/operational-balances/entities/{kind}", json=payload)
                assert denied.status_code == 403, (kind, denied.text)
            async with client(db, "owner") as c:
                ctx = (await c.get("/api/operational-balances/context")).json()
                assert ctx["can_create_cash"] is True
                assert ctx["can_create_external_person"] is True
                # General mobile "manage" remains false; owner-only creation is independent.
                assert ctx["permissions"]["manage"] is False
                created = await c.post(f"/api/operational-balances/entities/{kind}", json=payload)
                assert created.status_code == 200, (kind, created.text)
                again = await c.post(f"/api/operational-balances/entities/{kind}", json=payload)
                assert again.status_code == 200 and again.json() == created.json()
                listings = (await c.get(f"/api/operational-balances/entities/{kind}")).json()
                assert created.json()["id"] in [row["id"] for row in listings["items"]]
                changed = await c.post(f"/api/operational-balances/entities/{kind}", json={**payload, "name": "اسم مختلف"})
                assert changed.status_code == 409, changed.text
        from operational_balance_store import read
        state = await read(db, "owner")
        assert state["movements"] == []
        assert len(state["openings"]) == 2
        assert await db.mz2_financial_accounts.count_documents({"user_id": "owner", "name": "تجربة إنشاء cash"}) == 1
        assert await db.mz2_external_persons_v2.count_documents({"user_id": "owner", "name": "تجربة إنشاء external_person"}) == 1
    run(scenario)


def test_mobile_owner_creation_stops_when_accounting_cutover_active():
    async def scenario(db):
        await started(db)
        await db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"ledger_backend_state": "v2_active"}}, upsert=True)
        async with client(db, "owner") as c:
            for kind in ("cash", "external_person"):
                result = await c.post(f"/api/operational-balances/entities/{kind}", json={"name": "ممنوع بسبب القطع", "currency": "SAR", "request_id": "cutover-" + kind})
                assert result.status_code in (403, 409), result.text
        assert await db.mz2_external_persons_v2.count_documents({"name": "ممنوع بسبب القطع"}) == 0
        assert await db.mz2_financial_accounts.count_documents({"name": "ممنوع بسبب القطع"}) == 0
    run(scenario)
