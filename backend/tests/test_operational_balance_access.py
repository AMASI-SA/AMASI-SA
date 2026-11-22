"""HTTP authorization coverage without importing the production server.

Default to an isolated in-memory Mongo double. Set OPERATIONAL_TEST_MONGO_URI
to a dedicated localhost Mongo to exercise the identical HTTP/CAS scenarios.
No environment file or remote database is used by this test module.
"""
import asyncio
from copy import deepcopy
import os
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
import httpx
import pytest

from operational_balance_access import (
    OPERATION_LABELS, POLICY_FIELD, guard_access, load_access, normalized_policy,
)
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import AUTHORIZATION_GUARD, STATES, digest, initial, read


BASE = "/api/operational-balances"
DAY = "2026-10-07"
STAMP = DAY + "T12:00:00+00:00"


def grant(kind, identity, *, use=True, movements="none", balance=False):
    return {"kind": kind, "id": identity, "use": use, "movements": movements, "balance": balance}


def config(operations=(), resources=(), *, version=1, enabled=True):
    return {"version": version, "owner_id": "owner", "enabled": enabled,
            "operations": list(operations), "resources": list(resources)}


def entry(*, actor="staff", **changes):
    return {"request_id": "acl-entry-0001", "expected_session_scope": digest(["owner", actor]),
            "party_type": "supplier", "party_id": "supplier", "side": "for_us",
            "amount": "20.00", "business_date": DAY, "reason": "payment",
            "source_type": "bank", "source_id": "bank", "note": "دفعة مصرح بها", **changes}


async def save_policy(db, operations=(), resources=(), *, actor="staff", version=1, enabled=True):
    policy = config(operations, resources, version=version, enabled=enabled)
    await db.users.update_one({"id": actor}, {"$set": {POLICY_FIELD: policy}})
    return policy


def run(scenario):
    async def execute():
        uri = os.environ.get("OPERATIONAL_TEST_MONGO_URI")
        if uri:
            assert uri.startswith("mongodb://127.0.0.1:"), "Only isolated local Mongo is allowed"
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
            await mongo.admin.command("ping")
        else:
            from mongomock_motor import AsyncMongoMockClient
            mongo = AsyncMongoMockClient()
        db = mongo["operational_access_test_" + uuid4().hex]
        try:
            await db.users.insert_many([
                {"id": "owner", "name": "Owner", "role": "owner", "is_active": True},
                {"id": "other", "name": "Other owner", "role": "owner", "is_active": True},
                {"id": "staff", "name": "Recorder A", "role": "employee", "created_by": "owner",
                 "operational_balance_permissions": ["view", "move", "reports", "manage"]},
                {"id": "viewer", "name": "Recorder B", "role": "employee", "created_by": "owner"},
                {"id": "foreign", "name": "Foreign staff", "role": "employee", "created_by": "other"},
            ])
            await db.mz2_financial_accounts.insert_many([
                {"id": "bank", "user_id": "owner", "name": "Private bank", "account_type": "bank", "currency": "SAR", "status": "active"},
                {"id": "bank2", "user_id": "owner", "name": "Second private bank", "account_type": "bank", "currency": "SAR", "status": "active"},
                {"id": "cash", "user_id": "owner", "name": "Cash", "account_type": "cash", "currency": "SAR", "status": "active"},
                {"id": "foreign-bank", "user_id": "other", "name": "Foreign bank", "account_type": "bank", "currency": "SAR", "status": "active"},
            ])
            await db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": "owner", "name": "Supplier", "status": "active"})
            await db.mezan_employees_v2.insert_one({"id": "employee", "user_id": "owner", "name": "Employee", "status": "active"})
            await db.mz2_external_persons_v2.insert_one({"id": "external", "user_id": "owner", "name": "External", "currency": "SAR", "status": "active"})
            state = initial("owner")
            state.update(status="active", started_at=STAMP, revision=1)
            for kind, identity, amount, direction in [
                ("bank", "bank", "1000.00", "for_us"), ("bank", "bank2", "500.00", "for_us"),
                ("cash", "cash", "500.00", "for_us"), ("supplier", "supplier", "500.00", "for_party"),
                ("employee", "employee", "1000.00", "for_party"), ("employee_custody", "employee", "200.00", "for_us"),
                ("external_person", "external", "400.00", "for_party"),
            ]:
                key = digest([kind, identity, "SAR"])
                state["openings"][key] = {"id": key, "party_type": kind, "party_id": identity,
                    "name": identity, "currency": "SAR", "amount": amount, "direction": direction,
                    "created_at": STAMP, "approved_at": STAMP, "actor_id": "owner", "baseline": True}
            await db[STATES].insert_one(state)
            app = FastAPI()

            async def principal(x_test_user: str = Header(default="owner"), x_test_mobile: str = Header(default="no")):
                # Deliberately stale/overprivileged token fields: the router
                # must recover the actual user and ignore these role claims.
                if x_test_mobile == "yes":
                    return {"id": "owner", "role": "owner", "_mobile_actor_id": x_test_user,
                            "_session_client": "amasi_mobile"}
                return {"id": x_test_user, "role": "owner"}

            app.include_router(make_operational_balance_router(db, principal), prefix="/api")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
                await scenario(db, client)
        finally:
            await mongo.drop_database(db.name)
            mongo.close()
    asyncio.run(execute())


def headers(actor="staff", mobile=False):
    return {"x-test-user": actor, "x-test-mobile": "yes" if mobile else "no"}


def test_default_deny_and_all_legacy_channels_are_closed_for_staff():
    async def scenario(db, client):
        await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": "owner", "user_id": "staff", "enabled": True,
            "permissions": ["operational_balance_movements_write", "operational_balance_reports_read"]})
        for mobile in (False, True):
            current = await client.get(BASE + "/access/current", headers=headers(mobile=mobile))
            assert current.status_code == 200
            assert current.json()["actor"]["id"] == "staff"
            assert current.json()["owner_override"] is False
            assert current.json()["enabled"] is False
            assert current.json()["resources"] == current.json()["operations"] == []
            assert (await client.get(BASE + "/workspace/context", headers=headers(mobile=mobile))).status_code == 403
        await save_policy(db, OPERATION_LABELS, [grant("bank", "bank", movements="all", balance=True)])
        for method, path in [
            ("GET", "/context"), ("GET", "/reports"), ("GET", "/entities/bank"),
            ("GET", "/accounts/bank/bank"), ("GET", "/audit"), ("GET", "/obligations"),
            ("GET", "/movements"), ("GET", "/financial-position"), ("GET", "/cost-exposure"),
            ("GET", "/daily-financial-movements"), ("GET", "/receipts/guessed-id"),
            ("POST", "/movements"), ("POST", "/receipts"), ("POST", "/refresh"),
            ("POST", "/openings"), ("POST", "/customer-returns"),
        ]:
            response = await client.request(method, BASE + path, headers=headers(), json={})
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["detail"]["code"] == "operational_workspace_route_required"
        assert (await read(db, "owner"))["movements"] == []
        assert (await client.get(BASE + "/reports")).status_code == 200
    run(scenario)


def test_owner_administration_is_tenant_bound_and_versioned():
    async def scenario(db, client):
        for path in ("/access/catalog", "/access/users", "/access/users/staff"):
            assert (await client.get(BASE + path, headers=headers())).status_code == 403
            assert (await client.get(BASE + path, headers=headers(mobile=True))).status_code == 403
        assert (await client.get(BASE + "/access/users/foreign")).status_code == 404
        catalog = await client.get(BASE + "/access/catalog", headers=headers("owner", mobile=True))
        assert catalog.status_code == 200, catalog.text
        assert "Foreign bank" not in catalog.text
        assert all(set(row) == {"kind", "id", "name", "currency"} for row in catalog.json()["resources"])
        users = (await client.get(BASE + "/access/users")).json()["items"]
        assert {row["id"] for row in users} == {"owner", "staff", "viewer"}
        body = {"expected_version": 0, "enabled": True, "operations": ["supplier.payment"],
                "resources": [grant("supplier", "supplier", movements="own"), grant("bank", "bank")]}
        created = await client.put(BASE + "/access/users/staff", json=body)
        assert created.status_code == 200, created.text
        assert created.json()["access"]["version"] == 1
        assert (await client.put(BASE + "/access/users/staff", json=body)).status_code == 409
        races = await asyncio.gather(*[
            client.put(BASE + "/access/users/staff", json={**body, "expected_version": 1, "enabled": enabled})
            for enabled in (False, True)
        ])
        assert sorted(response.status_code for response in races) == [200, 409]
        loser = next(response for response in races if response.status_code == 409)
        assert loser.json()["detail"]["code"] == "operational_access_changed"
        saved = await db.users.find_one({"id": "staff"})
        assert saved[POLICY_FIELD]["version"] == 2
        assert saved[POLICY_FIELD]["updated_by"] == "owner"
        assert (await client.put(BASE + "/access/users/owner", json=body)).status_code == 409
        assert (await client.put(BASE + "/access/users/foreign", json=body)).status_code == 404
        assert (await client.put(BASE + "/access/users/viewer", headers=headers(), json=body)).status_code == 403
        assert (await read(db, "owner"))["revision"] == 1
    run(scenario)


@pytest.mark.parametrize("change", [
    {"enabled": "true"}, {"expected_version": False}, {"operations": ["admin.all"]},
    {"operations": ["supplier.payment", "supplier.payment"]},
    {"resources": [grant("bank", "foreign-bank")]},
    {"resources": [grant("bank", "bank"), grant("bank", "bank")]},
    {"resources": [grant("unknown", "bank")]},
    {"resources": [{**grant("bank", "bank"), "use": "true"}]},
    {"resources": [{**grant("bank", "bank"), "movements": "everything"}]},
])
def test_invalid_grants_never_change_policy(change):
    async def scenario(db, client):
        body = {"expected_version": 0, "enabled": True, "operations": [], "resources": [], **change}
        response = await client.put(BASE + "/access/users/staff", json=body)
        assert response.status_code == 422, response.text
        assert POLICY_FIELD not in await db.users.find_one({"id": "staff"})
    run(scenario)


def test_disabled_policy_retains_admin_configuration_but_hides_effective_grants():
    async def scenario(db, client):
        await save_policy(db, ["supplier.payment"], [grant("supplier", "supplier", movements="all", balance=True)], enabled=False)
        admin = (await client.get(BASE + "/access/users/staff")).json()["access"]
        assert admin["enabled"] is False and admin["operations"] == ["supplier.payment"]
        current = (await client.get(BASE + "/access/current", headers=headers())).json()
        assert current["operations"] == current["resources"] == []
        assert (await client.get(BASE + "/workspace/context", headers=headers())).status_code == 403
        assert (await client.post(BASE + "/workspace/entries", json=entry(), headers=headers())).status_code == 403
    run(scenario)


def test_operation_and_both_transfer_resources_are_required():
    async def scenario(db, client):
        allowed = [grant("bank", "bank"), grant("bank", "bank2")]
        transfer = entry(party_type="bank", party_id="bank2", reason="transfer")
        for operations, resources in [([], allowed), (["accounts.transfer"], allowed[:1]),
                                      (["accounts.transfer"], allowed[1:])]:
            await save_policy(db, operations, resources)
            denied = await client.post(BASE + "/workspace/entries", json=transfer, headers=headers())
            assert denied.status_code == 403, denied.text
            assert (await read(db, "owner"))["movements"] == []
        await save_policy(db, ["accounts.transfer"], allowed)
        for mobile in (False, True):
            result = await client.post(BASE + "/workspace/entries", json=transfer, headers=headers(mobile=mobile))
            assert result.status_code == 200, result.text
            assert set(result.json()) == {"saved", "movement_id", "party_type", "party_id"}
        assert len((await read(db, "owner"))["movements"]) == 1
        assert (await client.get(BASE + "/workspace/ledger/bank/bank", headers=headers())).status_code == 404
        context = (await client.get(BASE + "/workspace/context", headers=headers())).json()
        assert all(row["balance"] is None for row in context["resources"])
        assert "1000.00" not in str(context)
    run(scenario)


@pytest.mark.parametrize("reason,operation,target", [
    ("salary_settlement", "external.salary", {"employee_id": "employee"}),
    ("personal", "external.personal", {"owner_id": "owner"}),
    ("household", "external.household", {"owner_id": "owner"}),
    ("charity_owner", "external.charity_owner", {"owner_id": "owner"}),
    ("charity_company", "external.charity_company", {"expense_id": "other"}),
])
def test_linked_external_flows_require_counterpart_use(reason, operation, target):
    async def scenario(db, client):
        await save_policy(db, [operation], [grant("external_person", "external")])
        payload = entry(party_type="external_person", party_id="external", side="for_party", reason=reason,
                        source_type=None, source_id=None, **target)
        response = await client.post(BASE + "/workspace/entries", json=payload, headers=headers())
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "operational_resource_forbidden"
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


def test_custody_source_requires_its_own_grant_and_operation():
    async def scenario(db, client):
        payload = entry(party_type="external_person", party_id="external", reason=None,
                        source_type="employee_custody", source_id="employee")
        resources = [grant("external_person", "external"), grant("employee_custody", "employee")]
        for operations, selected in [(["external.pay"], resources),
                                     (["external.pay", "custody.spend"], resources[:1])]:
            await save_policy(db, operations, selected)
            response = await client.post(BASE + "/workspace/entries", json=payload, headers=headers())
            assert response.status_code == 403, response.text
        await save_policy(db, ["external.pay", "custody.spend"], resources)
        response = await client.post(BASE + "/workspace/entries", json=payload, headers=headers())
        assert response.status_code == 200, response.text
        assert len((await read(db, "owner"))["movements"]) == 1
    run(scenario)


def test_own_movements_balance_and_counterpart_identity_are_independent():
    async def scenario(db, client):
        from operational_balance_service import create_movement
        for actor, amount in [("owner", "12.00"), ("staff", "15.00"), ("viewer", "18.00")]:
            await create_movement(db, "owner", actor, {
                "request_id": "fixture-" + actor, "party_type": "supplier", "party_id": "supplier",
                "bank_id": "bank", "source_account_type": "bank", "amount": amount, "currency": "SAR",
                "direction": "outgoing", "kind": "payment", "note": "Private bank confidential note",
                "reference": "hidden-reference-" + actor, "receipt_id": None, "allocations": [],
            }, clock=STAMP)
        await save_policy(db, [], [grant("supplier", "supplier", use=False, movements="own")])
        own = await client.get(BASE + "/workspace/ledger/supplier/supplier", headers=headers())
        assert own.status_code == 200, own.text
        assert own.json()["total"] == 1
        assert own.json()["items"][0]["amount"] == "15.00"
        assert own.json()["resource"]["balance"] is None
        assert "Private bank" not in own.text and "hidden-reference" not in own.text
        assert "500.00" not in own.text
        search = await client.get(BASE + "/workspace/parties?q=Private", headers=headers())
        assert search.json()["items"] == []
        assert (await client.get(BASE + "/workspace/ledger/bank/bank", headers=headers())).status_code == 404
        await save_policy(db, [], [grant("supplier", "supplier", use=False, movements="all")], version=2)
        all_rows = await client.get(BASE + "/workspace/ledger/supplier/supplier", headers=headers())
        assert all_rows.status_code == 200 and all_rows.json()["total"] == 3
        assert all_rows.json()["resource"]["balance"] is None
        # Balance permission alone must not expose any ledger row.
        await save_policy(db, [], [grant("supplier", "supplier", use=False, balance=True)], version=3)
        assert (await client.get(BASE + "/workspace/ledger/supplier/supplier", headers=headers())).status_code == 404
        visible = (await client.get(BASE + "/workspace/context", headers=headers())).json()["resources"]
        assert len(visible) == 1 and visible[0]["balance"]["visible"] is True
    run(scenario)


def test_live_revocation_before_money_cas_rolls_back_and_resets_guard(monkeypatch):
    async def scenario(db, client):
        import operational_balance_service as service
        resources = [grant("supplier", "supplier"), grant("bank", "bank")]
        await save_policy(db, ["supplier.payment"], resources)
        original = service.entity

        async def revoke_after_entity(*args, **kwargs):
            found = await original(*args, **kwargs)
            await save_policy(db, ["supplier.payment"], resources[:1], version=2)
            return found

        monkeypatch.setattr(service, "entity", revoke_after_entity)
        denied = await client.post(BASE + "/workspace/entries", json=entry(), headers=headers())
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] == "operational_resource_forbidden"
        state = await read(db, "owner")
        assert state["revision"] == 1 and state["movements"] == []
        assert AUTHORIZATION_GUARD.get() is None
    run(scenario)


def test_fresh_identity_rejects_disabled_and_reassigned_accounts_and_composes_guard():
    async def scenario(db, client):
        resources = [grant("supplier", "supplier"), grant("bank", "bank")]
        await save_policy(db, ["supplier.payment"], resources)
        stale = await db.users.find_one({"id": "staff"})
        called = []
        async def previous():
            called.append("previous")
        token = AUTHORIZATION_GUARD.set(previous)
        try:
            async with guard_access(db, stale, "owner", "supplier.payment", [("bank", "bank")]):
                assert await AUTHORIZATION_GUARD.get()()
            assert AUTHORIZATION_GUARD.get() is previous and len(called) == 2
        finally:
            AUTHORIZATION_GUARD.reset(token)
        await db.users.update_one({"id": "staff"}, {"$set": {"disabled": True}})
        assert (await client.get(BASE + "/access/current", headers=headers())).status_code == 403
        with pytest.raises(HTTPException) as inactive:
            await load_access(db, stale, "owner")
        assert inactive.value.detail["code"] == "operational_actor_inactive"
        await db.users.update_one({"id": "staff"}, {"$set": {"disabled": False, "created_by": "other"}})
        with pytest.raises(HTTPException) as reassigned:
            async with guard_access(db, stale, "owner", "supplier.payment", [("bank", "bank")]):
                pytest.fail("stale tenant was admitted")
        assert reassigned.value.detail["code"] == "operational_actor_scope_changed"
        # Even a fresh request in the new tenant cannot reuse the old owner's
        # grant when the two tenants happen to have identical resource IDs.
        moved = (await client.get(BASE + "/access/current", headers=headers())).json()
        assert moved["owner_id"] == "other" and moved["enabled"] is False
        assert moved["resources"] == moved["operations"] == []
        from mobile_app_permissions import mobile_app_access_for_user, OPERATIONAL_APP_PERMISSIONS
        navigation = await mobile_app_access_for_user(db, await db.users.find_one({"id": "staff"}))
        assert not set(navigation["permissions"]).intersection(OPERATIONAL_APP_PERMISSIONS)
        assert AUTHORIZATION_GUARD.get() is None
        await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
        assert (await client.get(BASE + "/access/catalog")).status_code == 403
    run(scenario)


def test_new_policy_is_the_only_native_balances_navigation_grant():
    async def scenario(db, client):
        from mobile_app_permissions import mobile_app_access_for_user, OPERATIONAL_APP_READ, OPERATIONAL_APP_WRITE, EMPLOYEE_BALANCE_PERMISSION
        actor = await db.users.find_one({"id": "staff"})
        empty = await mobile_app_access_for_user(db, actor)
        assert OPERATIONAL_APP_READ not in empty["permissions"]
        await save_policy(db, ["supplier.payment"], [grant("supplier", "supplier"), grant("bank", "bank")])
        actor = await db.users.find_one({"id": "staff"})
        assigned = await mobile_app_access_for_user(db, actor)
        assert assigned["enabled"] is True
        assert set(assigned["permissions"]) == {OPERATIONAL_APP_READ, OPERATIONAL_APP_WRITE, EMPLOYEE_BALANCE_PERMISSION}
        assert (await client.get(BASE + "/workspace/context", headers=headers(mobile=True))).status_code == 200
        await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": "owner", "user_id": "staff", "enabled": True,
            "permissions": [OPERATIONAL_APP_READ, OPERATIONAL_APP_WRITE, "app.page.orders"]})
        await save_policy(db, [], [], enabled=False, version=2)
        revoked = await mobile_app_access_for_user(db, await db.users.find_one({"id": "staff"}))
        assert set(revoked["permissions"]) == {"app.page.orders", EMPLOYEE_BALANCE_PERMISSION}
        assert (await client.get(BASE + "/workspace/context", headers=headers(mobile=True))).status_code == 403
    run(scenario)


@pytest.mark.parametrize("bad", [None, [], {"enabled": "true"},
    config(resources=[{**grant("bank", "bank"), "movements": {"all": True}}]),
    config(resources=[{**grant("bank", "bank"), "balance": "false"}]),
    config(operations=["supplier.payment", "supplier.payment"]),
])
def test_malformed_stored_policy_fails_closed(bad):
    clean = normalized_policy(deepcopy(bad))
    assert clean["enabled"] is False
    assert clean["resources"] == clean["operations"] == []
