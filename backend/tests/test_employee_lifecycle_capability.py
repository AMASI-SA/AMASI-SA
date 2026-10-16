"""Pure capability checks; transaction rollback is covered by Mongo route tests."""
from copy import deepcopy
from types import SimpleNamespace
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException

from ai_store_access_contract import effective_permissions, write_role_assignment
from operational_atomic import OperationalDatabase
from operational_atomic import employee_lifecycle_atomic_owner
from operational_atomic import operational_owner


class Collection:
    def __init__(self, name, row=None):
        self.name, self.row, self.writes = name, row, []

    async def find_one(self, query, *args, **kwargs):
        return deepcopy(self.row)

    async def update_one(self, query, update, **kwargs):
        self.writes.append((deepcopy(query), deepcopy(update)))
        if self.row is not None:
            self.row.update(update.get("$set", {}))
        return SimpleNamespace(matched_count=int(self.row is not None))


def capability(profile="employee_lifecycle", legacy=False):
    assignment = {"user_id": "login", "role_key": "warehouse_operator", "enabled": True,
                  "extra_permissions": ["audit.read"], "denied_permissions": ["products.read"],
                  "created_by": "owner"}
    if not legacy:
        assignment["owner_user_id"] = "owner"
    root = {"users": Collection("users", {"id": "login"}),
            "mezan_role_assignments_v2": Collection("mezan_role_assignments_v2", assignment)}
    for name in ("general_ledger", "mz2_atomic_owners", "mezan_employee_events_v2", "order_review_auth_sessions_v2"):
        root[name] = Collection(name)
    root["operational_balance_states_v1"] = Collection("operational_balance_states_v1", {"_id": "owner"})
    state = {"profile": profile, "failed": False}
    db = OperationalDatabase(root, object(), state, "owner")
    return db, root, state


@pytest.mark.asyncio
async def test_employee_lifecycle_rejects_review_session_binding_before_database_access():
    async def unreachable(_):
        pytest.fail("callback must not run")

    with pytest.raises(HTTPException) as error:
        await operational_owner(object(), "owner", unreachable, profile="employee_lifecycle",
                                review_session={"actor_id": "actor", "origin_session_ref": "a" * 64,
                                                "origin_session_epoch": 1})
    assert error.value.detail["code"] == "operational_review_session_scope_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,collection", [
    ("employee_lifecycle", "order_review_auth_sessions_v2"),
    ("fulfillment", "users"),
    ("fulfillment", "mezan_role_assignments_v2"),
    ("fulfillment", "operational_balance_states_v1"),
])
async def test_review_and_employee_write_capabilities_remain_separate(profile, collection):
    db, root, state = capability(profile)
    with pytest.raises(HTTPException):
        await db[collection].update_one({"_id": "owner", "owner_id": "owner", "revision": 4},
                                        {"$inc": {"revision": 1, "salary_contract_revision": 1}})
    assert state["failed"]
    assert all(not row.writes for row in root.values())


def account_change(active):
    return {"disabled": not active, "is_active": active,
            "employee_access_state": "active" if active else "disabled",
            "employee_access_reason": "employee status", "employee_access_updated_at": "2026-10-16T00:00:00Z",
            "employee_access_updated_by": "owner"}


async def account(db, active=False):
    await db.users.update_one({"id": "login", "created_by": "owner", "role": {"$ne": "owner"}},
                              {"$set": account_change(active)})


def role_change(before, active):
    enabled = bool(before.get("enabled_before_employee_suspension", False)) if before.get(
        "suspended_by_employee_v2") else bool(before.get("enabled", False))
    result = {"enabled": enabled if active else False, "suspended_by_employee_v2": not active,
              "effective_permissions": effective_permissions({**before, "enabled": enabled}) if active else [],
              "updated_by": "owner", "updated_at": "2026-10-16T00:00:00Z"}
    if not active:
        result["enabled_before_employee_suspension"] = enabled
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("original_enabled", [False, True])
async def test_suspend_restore_preserves_existing_authorization_and_login_scope(legacy, original_enabled):
    db, root, state = capability(legacy=legacy)
    roles = root["mezan_role_assignments_v2"]
    roles.row["enabled"] = original_enabled
    original_permissions = effective_permissions(roles.row)
    for active in (False, False, True):
        await account(db, active)
        before = deepcopy(roles.row)
        await write_role_assignment(db, owner_user_id="owner", user_id="login", existing=before,
                                    values=role_change(before, active))
    assert roles.row["enabled"] is original_enabled
    assert roles.row["owner_user_id"] == "owner"
    assert roles.row["effective_permissions"] == original_permissions
    if original_enabled:
        assert "audit.read" in original_permissions
        assert "fulfillment.ready.read" in original_permissions
        assert "products.read" not in original_permissions
    else:
        assert original_permissions == []
    assert all(query["user_id"] == "login" for query, _ in roles.writes)
    assert all("user_id" not in query for query, _ in root["users"].writes)
    assert not state["failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["password", "role", "owner_query", "foreign_query", "upsert", "many",
                                       "inconsistent_flags", "financial", "control", "audit_update", "setup"])
async def test_forbidden_writes_latch_failure(attack):
    db, root, state = capability("employee_setup" if attack == "setup" else "employee_lifecycle")
    query = {"id": "login", "created_by": "owner", "role": {"$ne": "owner"}}
    values = account_change(False)
    kwargs = {}
    method, collection = "update_one", "users"
    if attack in {"password", "role"}:
        values[attack] = "owner"
    elif attack == "owner_query":
        query["id"] = "owner"
    elif attack == "foreign_query":
        query["created_by"] = "other"
    elif attack == "upsert":
        kwargs["upsert"] = True
    elif attack == "many":
        method = "update_many"
    elif attack == "inconsistent_flags":
        values["disabled"] = False
    elif attack in {"financial", "control", "audit_update"}:
        collection = {"financial": "general_ledger", "control": "mz2_atomic_owners",
                      "audit_update": "mezan_employee_events_v2"}[attack]
    with pytest.raises(HTTPException):
        await getattr(db[collection], method)(query, {"$set": values}, **kwargs)
    assert state["failed"]
    assert all(not row.writes for row in root.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["role_grant", "permission_grant", "enabled_grant", "foreign_owner",
                                       "foreign_login", "without_account", "upsert"])
async def test_assignment_access_cannot_grant_authorization(attack):
    db, root, state = capability()
    if attack != "without_account":
        await account(db, False)
    before = deepcopy(root["mezan_role_assignments_v2"].row)
    values = role_change(before, False)
    if attack == "role_grant":
        values["role_key"] = "owner"
    elif attack == "permission_grant":
        values["effective_permissions"] = ["all"]
    elif attack == "enabled_grant":
        values["enabled"] = True
    with pytest.raises(HTTPException):
        await write_role_assignment(db, owner_user_id="other" if attack == "foreign_owner" else "owner",
                                    user_id="other" if attack == "foreign_login" else "login", existing=before,
                                    values=values, upsert=attack == "upsert")
    assert state["failed"]
    assert not root["mezan_role_assignments_v2"].writes


@pytest.mark.asyncio
async def test_operational_salary_fence_preserves_exact_cas_selector():
    db, root, state = capability()
    query = {"_id": "owner", "owner_id": "owner", "revision": 4}
    update = {"$inc": {"revision": 1, "salary_contract_revision": 1}}
    await db.operational_balance_states_v1.update_one(query, update)
    assert root["operational_balance_states_v1"].writes == [(query, update)]
    assert not state["failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["foreign_id", "foreign_owner", "no_revision", "bool_revision",
                                       "balance_write", "upsert", "extra_increment", "setup"])
async def test_operational_salary_fence_cannot_write_balances(attack):
    db, root, state = capability("employee_setup" if attack == "setup" else "employee_lifecycle")
    query = {"_id": "owner", "owner_id": "owner", "revision": 4}
    update = {"$inc": {"revision": 1, "salary_contract_revision": 1}}
    kwargs = {}
    if attack == "foreign_id":
        query["_id"] = "other"
    elif attack == "foreign_owner":
        query["owner_id"] = "other"
    elif attack == "no_revision":
        del query["revision"]
    elif attack == "bool_revision":
        query["revision"] = True
    elif attack == "balance_write":
        update["$set"] = {"engine": {}}
    elif attack == "upsert":
        kwargs["upsert"] = True
    elif attack == "extra_increment":
        update["$inc"]["balance"] = 1
    with pytest.raises(HTTPException):
        await db.operational_balance_states_v1.update_one(query, update, **kwargs)
    assert state["failed"]
    assert not root["operational_balance_states_v1"].writes


@pytest_asyncio.fixture
async def mongo_db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI must address a disposable replica set")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client["employee_lifecycle_test_" + uuid4().hex]
    assert (await database.command("hello")).get("setName")
    await database.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0, "writes_paused": True})
    await database.users.insert_one({"id": "login", "created_by": "owner", "role": "viewer",
                                     "password": "unchanged", "disabled": False})
    await database.mezan_role_assignments_v2.insert_one({"owner_user_id": "owner", "user_id": "login",
                                                        "role_key": "warehouse_operator", "enabled": True,
                                                        "extra_permissions": ["audit.read"],
                                                        "denied_permissions": ["products.read"]})
    await database.operational_balance_states_v1.insert_one({"_id": "owner", "owner_id": "owner", "revision": 4})
    yield database
    await client.drop_database(database.name)
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "exception", "caught_forbidden"])
async def test_real_transaction_commits_or_rolls_back_all_lifecycle_effects(mongo_db, failure):
    names = ["users", "mezan_role_assignments_v2", "operational_balance_states_v1", "mz2_atomic_owners",
             "mezan_employee_events_v2", "mezan_employees_v2"]
    before = {name: await mongo_db[name].find({}).to_list(None) for name in names}

    async def change(tx):
        await tx.mezan_employees_v2.insert_one({"user_id": "owner", "id": "employee", "status": "inactive"})
        await account(tx, False)
        assignment = await tx.mezan_role_assignments_v2.find_one({"owner_user_id": "owner", "user_id": "login"})
        await write_role_assignment(tx, owner_user_id="owner", user_id="login", existing=assignment,
                                    values=role_change(assignment, False))
        await tx.operational_balance_states_v1.update_one({"_id": "owner", "owner_id": "owner", "revision": 4},
                                                          {"$inc": {"revision": 1, "salary_contract_revision": 1}})
        await tx.mezan_employee_events_v2.insert_one({"user_id": "owner", "event_type": "status_changed"})
        if failure == "exception":
            raise RuntimeError("injected failure after access changes")
        if failure == "caught_forbidden":
            try:
                await tx.users.update_one({"id": "login", "created_by": "owner", "role": {"$ne": "owner"}},
                                          {"$set": {"password": "forbidden"}})
            except HTTPException:
                pass

    if failure:
        with pytest.raises(RuntimeError if failure == "exception" else HTTPException):
            await employee_lifecycle_atomic_owner(mongo_db, "owner", change)
        for name in names:
            assert await mongo_db[name].find({}).to_list(None) == before[name]
    else:
        await employee_lifecycle_atomic_owner(mongo_db, "owner", change)
        assert (await mongo_db.users.find_one({"id": "login"}))["disabled"] is True
        assert (await mongo_db.users.find_one({"id": "login"}))["password"] == "unchanged"
        assert (await mongo_db.mezan_role_assignments_v2.find_one({"user_id": "login"}))["enabled"] is False
        assert (await mongo_db.operational_balance_states_v1.find_one({"_id": "owner"}))["revision"] == 5
        assert (await mongo_db.mz2_atomic_owners.find_one({"_id": "owner"}))["writes_paused"] is True


@pytest.mark.asyncio
async def test_real_suspend_restore_retains_nonempty_permissions(mongo_db):
    original = await mongo_db.mezan_role_assignments_v2.find_one({"user_id": "login"})
    expected = effective_permissions(original)
    assert "audit.read" in expected and "fulfillment.ready.read" in expected
    assert "products.read" not in expected

    for active in (False, True):
        async def change(tx):
            await account(tx, active)
            assignment = await tx.mezan_role_assignments_v2.find_one(
                {"owner_user_id": "owner", "user_id": "login"})
            await write_role_assignment(tx, owner_user_id="owner", user_id="login", existing=assignment,
                                        values=role_change(assignment, active))
        await employee_lifecycle_atomic_owner(mongo_db, "owner", change)
        saved = await mongo_db.mezan_role_assignments_v2.find_one({"user_id": "login"})
        assert saved["effective_permissions"] == (expected if active else [])
        assert saved["role_key"] == original["role_key"]
        assert saved["extra_permissions"] == original["extra_permissions"]
        assert saved["denied_permissions"] == original["denied_permissions"]
