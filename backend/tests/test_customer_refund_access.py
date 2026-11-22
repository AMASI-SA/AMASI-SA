"""Shared employee refund grants never grant general financial authority."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request

from customer_refund_access import (
    CUSTOMER_REFUNDS_APP_PERMISSION,
    customer_refund_access_signature,
    install_customer_refund_access_routes,
    load_customer_refund_access,
    bind_customer_refund_request, refund_scope_active,
    EMPLOYEES,
)
from mobile_app_permissions import MOBILE_APP_ACCESS, mobile_app_access_for_user, validate_mobile_app_permissions
from operational_balance_access import POLICY_FIELD, guard_access, load_access, fresh_identity
from operational_balance_store import AUTHORIZATION_GUARD


def matches(row, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(row, item) for item in value):
                return False
        elif isinstance(value, dict) and any(k.startswith("$") for k in value):
            if "$exists" in value and (key in row) != value["$exists"]:
                return False
            if "$ne" in value and row.get(key) == value["$ne"]:
                return False
            if "$in" in value and row.get(key) not in value["$in"]:
                return False
        elif row.get(key) != value:
            return False
    return True


class Collection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))
        self.before_update = None

    async def find_one(self, query, projection=None):
        return next((deepcopy(row) for row in self.rows if matches(row, query)), None)

    async def update_one(self, query, update, **kwargs):
        if self.before_update:
            hook, self.before_update = self.before_update, None
            hook(self.rows)
        for row in self.rows:
            if matches(row, query):
                row.update(deepcopy(update.get("$set", {})))
                return type("Result", (), {"matched_count": 1})()
        return type("Result", (), {"matched_count": 0})()


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, key):
        return self.collections.setdefault(key, Collection())

    def __getattr__(self, key):
        return self[key]


SOURCE = {"kind": "bank", "id": "refund-bank"}
PROVIDER = {"kind": "provider", "id": "salla"}
PATH = "/employees-v2/management/employees/employee/customer-refund-access"


def run(monkeypatch, scenario, *, existing=None):
    async def execute():
        db = DB()
        staff = {"id": "staff", "created_by": "owner", "name": "الموظف", "role": "employee", "is_active": True}
        if existing is not None:
            staff[POLICY_FIELD] = deepcopy(existing)
        staff["web_page_access"] = {"owner_id": "owner", "revision": 4,
                                    "grants": [{"id": "products.catalog", "mode": "read"}]}
        db["users"].rows = [{"id": "owner", "role": "owner", "name": "المالك"}, staff,
                            {"id": "foreign-owner", "role": "owner"}]
        db["mezan_employees_v2"].rows = [{"user_id": "owner", "id": "employee", "account_user_id": "staff"}]
        async def catalogue(_db, owner):
            assert owner == "owner"
            return [{**SOURCE, "name": "بنك الرد", "currency": "SAR"},
                    {**PROVIDER, "name": "سلة", "currency": "SAR"}]
        monkeypatch.setattr("customer_refund_access._source_catalog", catalogue)
        async def user(request: Request):
            identity = request.headers.get("X-Actor", "owner")
            value = await db["users"].find_one({"id": identity})
            if request.headers.get("X-Mobile"):
                value["_session_client"] = "amasi_mobile"
            if request.headers.get("X-Impersonate"):
                value = {"id": "owner", "role": "owner", "_mobile_actor_id": identity}
            return value
        router = APIRouter(prefix="/employees-v2")
        install_customer_refund_access_routes(router, db, user)
        app = FastAPI()
        app.include_router(router)
        from operational_balance_access_routes import install_access_routes
        access_router = APIRouter(prefix="/operational-balances")
        async def scope(_db, principal, permission):
            actor, owner = await fresh_identity(_db, principal)
            return actor, owner, "mezan2"
        install_access_routes(access_router, db, user, scope)
        app.include_router(access_router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await scenario(db, client)
    asyncio.run(execute())


async def save(client, mode="write", sources=None, **overrides):
    response = await client.get(PATH)
    assert response.status_code == 200, response.text
    current = response.json()["access"]
    payload = {"mode": mode, "sources": [SOURCE] if sources is None else sources,
               "expected_policy_version": current["policy_version"],
               "expected_page_revision": current["page_revision"],
               "expected_session_scope": current["session_scope"], **overrides}
    return await client.put(PATH, json=payload), payload


def test_shared_grant_scopes_sources_and_preserves_unrelated_authority(monkeypatch):
    existing = {"version": 7, "owner_id": "owner", "enabled": True, "operations": ["employee.accrual"],
                "resources": [{"kind": "employee", "id": "someone", "use": True, "movements": "own", "balance": False, "report": False, "print": False}]}
    async def scenario(db, client):
        response, _ = await save(client)
        assert response.status_code == 200, response.text
        current = await db.users.find_one({"id": "staff"})
        policy = current[POLICY_FIELD]
        assert policy["resources"] == existing["resources"]
        assert "employee.accrual" in policy["operations"]
        assert policy["version"] == 8
        assert current["web_page_access"]["revision"] == 5
        assert {tuple(row.items()) for row in current["web_page_access"]["grants"]} >= {
            (("id", "balances.refund"), ("mode", "write")), (("id", "products.catalog"), ("mode", "read"))}
        refund = await load_customer_refund_access(db, {"id": "staff"})
        assert refund.can_operation("customer.refund_execute")
        assert refund.resource("bank", "refund-bank")["use"]
        assert not refund.resource("provider", "salla")["use"]
        assert not refund.can_operation("employee.accrual")
        assert not refund.resource("bank", "refund-bank")["balance"]
        general = await load_access(db, {"id": "staff"}, "owner")
        assert general.can_operation("employee.accrual")
        assert not general.resource("bank", "refund-bank")["use"]
        async with guard_access(db, {"id": "staff"}, "owner", "customer.refund_execute", [("bank", "refund-bank")]):
            pass
        with pytest.raises(HTTPException):
            async with guard_access(db, {"id": "staff"}, "owner", "employee.accrual", [("bank", "refund-bank")]):
                pass
    run(monkeypatch, scenario, existing=existing)


def test_read_only_revocation_and_manager_never_gain_financial_action(monkeypatch):
    async def scenario(db, client):
        response, _ = await save(client, "read")
        assert response.status_code == 200, response.text
        policy = await load_customer_refund_access(db, {"id": "staff"})
        assert policy.can_operation("customer.refund_read")
        assert not policy.can_operation("customer.refund_request")
        assert not policy.can_operation("customer.refund_execute")
        with pytest.raises(HTTPException):
            async with guard_access(db, {"id": "staff"}, "owner", "customer.refund_execute", [("bank", "refund-bank")]):
                pass
        staff = await db.users.find_one({"id": "staff"})
        access = await mobile_app_access_for_user(db, staff)
        assert CUSTOMER_REFUNDS_APP_PERMISSION in access["permissions"]
        assert not set(access["permissions"]) & {"operational_balance_reports_read", "operational_balance_movements_write"}
        response, _ = await save(client, "none")
        assert response.status_code == 200
        staff = await db.users.find_one({"id": "staff"})
        assert not (await load_customer_refund_access(db, staff)).can_operation("customer.refund_read")
        assert CUSTOMER_REFUNDS_APP_PERMISSION not in (await mobile_app_access_for_user(db, staff))["permissions"]
        db[MOBILE_APP_ACCESS].rows = [{"owner_user_id": "owner", "user_id": "staff", "enabled": True, "permissions": ["app.role.manager"]}]
        assert CUSTOMER_REFUNDS_APP_PERMISSION not in (await mobile_app_access_for_user(db, staff))["permissions"]
        assert (await client.get(PATH, headers={"X-Actor": "staff"})).status_code == 403
        assert (await client.get(PATH, headers={"X-Actor": "staff", "X-Impersonate": "1"})).status_code == 403
    run(monkeypatch, scenario)


def test_stale_revisions_nonce_sources_and_account_binding_are_rejected(monkeypatch):
    async def scenario(db, client):
        response, payload = await save(client)
        assert response.status_code == 200
        assert (await client.put(PATH, json=payload)).status_code == 409
        response, _ = await save(client, expected_session_scope="0" * 64)
        assert response.status_code == 409
        response, _ = await save(client, sources=[{"kind": "bank", "id": "foreign-bank"}])
        assert response.status_code == 422
        response, _ = await save(client, sources=[{"kind": "employee", "id": "someone"}])
        assert response.status_code == 422
        current = (await client.get(PATH)).json()["access"]
        db.users.before_update = lambda rows: rows[1]["web_page_access"].update(revision=99)
        response = await client.put(PATH, json={**payload,
            "expected_policy_version": current["policy_version"], "expected_page_revision": current["page_revision"]})
        assert response.status_code == 409
        db["mezan_employees_v2"].rows[0]["account_user_id"] = "another-account"
        assert not (await load_customer_refund_access(db, {"id": "staff"})).can_operation("customer.refund_read")
    run(monkeypatch, scenario)


def test_web_revocation_and_workspace_disable_apply_to_native_immediately(monkeypatch):
    async def scenario(db, client):
        response, _ = await save(client)
        assert response.status_code == 200
        before = await load_customer_refund_access(db, {"id": "staff"})
        staff = db.users.rows[1]
        staff["web_page_access"]["grants"] = [{"id": "balances.refund", "mode": "read"}]
        staff["web_page_access"]["revision"] += 1
        after = await load_customer_refund_access(db, {"id": "staff"})
        assert after.can_operation("customer.refund_read") and not after.can_operation("customer.refund_execute")
        assert customer_refund_access_signature(before) != customer_refund_access_signature(after)
        staff[POLICY_FIELD]["enabled"] = False
        assert not (await load_customer_refund_access(db, {"id": "staff"})).can_operation("customer.refund_read")
        assert CUSTOMER_REFUNDS_APP_PERMISSION not in (await mobile_app_access_for_user(db, staff))["permissions"]
    run(monkeypatch, scenario)


def test_disabled_nonrefund_policy_is_not_reactivated(monkeypatch):
    existing = {"version": 3, "owner_id": "owner", "enabled": False, "operations": ["expense.pay"], "resources": []}
    async def scenario(db, client):
        before = deepcopy(db.users.rows[1])
        response, _ = await save(client)
        assert response.status_code == 409
        assert db.users.rows[1] == before
    run(monkeypatch, scenario, existing=existing)


@pytest.mark.parametrize("field,value", [("active", False), ("is_active", False), ("disabled", True),
                                         ("status", "inactive"), ("status", "unpaid_leave"), ("deleted_at", "2026-11-22")])
def test_inactive_employee_binding_revokes_refund_capability(monkeypatch, field, value):
    async def scenario(db, client):
        response, _ = await save(client)
        assert response.status_code == 200
        db["mezan_employees_v2"].rows[0][field] = value
        assert not (await load_customer_refund_access(db, {"id": "staff"})).can_operation("customer.refund_read")
        assert CUSTOMER_REFUNDS_APP_PERMISSION not in (await mobile_app_access_for_user(db, db.users.rows[1]))["permissions"]
    run(monkeypatch, scenario)


def test_native_only_catalogue_cannot_assign_shared_refund_permission():
    with pytest.raises(ValueError, match="unknown_mobile_app_permission"):
        validate_mobile_app_permissions([CUSTOMER_REFUNDS_APP_PERMISSION])


def test_merchant_shaped_native_principal_does_not_imply_refund_capability(monkeypatch):
    async def scenario(db, client):
        for user in ({'id':'staff','role':'employee','created_by':'owner','is_owner':True},
                     {'id':'owner','role':'owner','_mobile_actor_id':'staff'},
                     {'id':'owner','role':'owner','active':False}):
            assert CUSTOMER_REFUNDS_APP_PERMISSION not in (await mobile_app_access_for_user(db,user))['permissions']
    run(monkeypatch, scenario)


@pytest.mark.parametrize('malformed', [None, {}, {'mode': []}, {'mode':'write','employee_id':'employee','sources':[{'kind':[],'id':'bank'}]}])
def test_explicit_malformed_shared_grant_never_falls_back_to_legacy_access(monkeypatch, malformed):
    async def scenario(db, client):
        response, _ = await save(client)
        assert response.status_code == 200
        db.users.rows[1][POLICY_FIELD]['customer_refunds'] = malformed
        assert not (await load_customer_refund_access(db, {'id':'staff'})).can_operation('customer.refund_execute')
        assert CUSTOMER_REFUNDS_APP_PERMISSION not in (await mobile_app_access_for_user(db, db.users.rows[1]))['permissions']
    run(monkeypatch, scenario)


@pytest.mark.parametrize("field,value", [("active", False), ("is_active", False), ("disabled", True), ("is_owner", True)])
def test_target_lifecycle_race_is_fenced_by_atomic_grant_selector(monkeypatch, field, value):
    async def scenario(db, client):
        db.users.before_update = lambda rows: rows[1].update({field: value})
        response, _ = await save(client)
        assert response.status_code == 409
        assert POLICY_FIELD not in db.users.rows[1]
        assert db.users.rows[1]["web_page_access"]["revision"] == 4
    run(monkeypatch, scenario)


@pytest.mark.parametrize("path,allowed", [
    ("refunds", True), ("refunds/order/1001", True), ("mobile-refunds", True),
    ("mobile-refunds/order/1001", True), ("refunds-admin", False),
    ("mobile-refunds-export", False), ("ledger/bank/refund-bank", False),
])
def test_request_scope_has_an_exact_route_boundary(path, allowed):
    request = Request({"type": "http", "method": "GET", "path": "/api/operational-balances/workspace/" + path,
                       "headers": [], "query_string": b"", "server": ("test", 80), "scheme": "http"})
    with bind_customer_refund_request(request):
        assert refund_scope_active() is allowed
    assert not refund_scope_active()


def test_general_access_editor_preserves_refund_scope_and_cannot_add_bank_power(monkeypatch):
    async def scenario(db, client):
        response, _ = await save(client)
        assert response.status_code == 200
        stored = deepcopy(db.users.rows[1][POLICY_FIELD]["customer_refunds"])
        response = await client.put('/operational-balances/access/users/staff', json={
            'expected_version': 1, 'enabled': True,
            'operations': ['expense.pay', 'customer.refund_read'], 'resources': [],
        })
        assert response.status_code == 200, response.text
        assert db.users.rows[1][POLICY_FIELD]["customer_refunds"] == stored
        general = await load_access(db, {"id": "staff"}, "owner")
        assert general.can_operation("expense.pay") and not general.resource("bank", "refund-bank")["use"]
        refund = await load_customer_refund_access(db, {"id": "staff"})
        assert refund.can_operation("customer.refund_read") and not refund.can_operation("customer.refund_execute")
    run(monkeypatch, scenario)


@pytest.mark.parametrize("change", ['source', 'mode', 'employee', 'account', 'owner'])
def test_existing_money_authorization_guard_rechecks_shared_grant_before_commit(monkeypatch, change):
    async def scenario(db, client):
        response, _ = await save(client)
        assert response.status_code == 200
        async with guard_access(db, {"id": "staff"}, "owner", "customer.refund_execute", [("bank", "refund-bank")]):
            if change == 'source':
                db.users.rows[1][POLICY_FIELD]['customer_refunds']['sources'] = []
            elif change == 'mode':
                db.users.rows[1]['web_page_access']['grants'] = [{'id':'balances.refund','mode':'read'}]
            elif change == 'employee':
                db[EMPLOYEES].rows[0]['active'] = False
            elif change == 'account':
                db.users.rows[1]['created_by'] = 'foreign-owner'
            else:
                db.users.rows[0]['active'] = False
            with pytest.raises(HTTPException):
                await AUTHORIZATION_GUARD.get()()
        assert AUTHORIZATION_GUARD.get() is None
    run(monkeypatch, scenario)


def test_legacy_refund_policy_remains_explicit_and_supported(monkeypatch):
    existing = {"version": 2, "owner_id": "owner", "enabled": True,
                "operations": ["customer.refund_execute", "customer.collection"],
                "resources": [{**SOURCE, "use": True, "movements": "own", "balance": False, "report": False, "print": False}]}
    async def scenario(db, client):
        policy = await load_customer_refund_access(db, {"id": "staff"})
        assert policy.can_operation('customer.refund_read') and policy.can_operation('customer.refund_execute')
        assert not policy.can_operation('customer.collection')
        assert policy.resource('bank', 'refund-bank')['use']
        assert policy.resource('bank', 'refund-bank')['movements'] == 'own'
        assert db.users.rows[1][POLICY_FIELD] == existing
    run(monkeypatch, scenario, existing=existing)


def test_shared_grant_actual_catalogue_http_and_competing_cas():
    from fastapi import Header
    from employees_v2_routes import make_employees_v2_router
    from operational_balance_store import STATES
    from test_operational_balance_access import run as run_real
    async def scenario(db, operational_client):
        await db.mezan_employees_v2.update_one({'id':'employee','user_id':'owner'}, {'$set':{'account_user_id':'staff'}})
        before_money = await db[STATES].find_one({'_id':'owner'})
        app = FastAPI()
        async def principal(x_test_user: str = Header(default='owner')):
            return {'id':x_test_user,'_session_client':'amasi_mobile'}
        app.include_router(make_employees_v2_router(db, principal), prefix='/api')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            path = '/api' + PATH
            response = await client.get(path)
            assert response.status_code == 200, response.text
            current = response.json()['access']
            banks = [row['id'] for row in response.json()['sources'] if row['kind']=='bank']
            assert set(banks) == {'bank','bank2'} and 'foreign-bank' not in banks
            body = {'expected_policy_version':current['policy_version'],'expected_page_revision':current['page_revision'],
                    'expected_session_scope':current['session_scope'],'mode':'write','sources':[{'kind':'bank','id':'bank'}]}
            replies = await asyncio.gather(client.put(path,json=body), client.put(path,json={**body,'mode':'read'}))
            assert sorted(reply.status_code for reply in replies) == [200,409], [reply.text for reply in replies]
            stored = await db.users.find_one({'id':'staff'})
            assert stored[POLICY_FIELD]['version'] == 1 and stored['web_page_access']['revision'] == 1
            assert stored[POLICY_FIELD]['resources'] == []
            assert (await client.get(path,headers={'X-Test-User':'staff'})).status_code == 403
            assert await db[STATES].find_one({'_id':'owner'}) == before_money
            # Neither page-only source use nor shared refund access opens the bank ledger.
            general = await load_access(db, {'id':'staff'}, 'owner')
            assert not general.resource('bank','bank')['use']
    run_real(scenario)
