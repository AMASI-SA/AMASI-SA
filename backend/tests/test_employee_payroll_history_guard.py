"""Payroll history protection through real Employee OS writes and Mongo rollback."""
import asyncio
from datetime import date

import pytest
from fastapi import HTTPException

import employees_v2_routes as routes
from employee_payroll_status import contract_salary_row, salary_active_on
from tests.test_employee_salary_contract_management import OWNER, create, db, endpoint


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(routes, "riyadh_today", lambda: date(2026, 10, 16))


async def setup_employee(db):
    result = await create(db, monthly_salary=3100, salary_effective_date="2026-10-01")
    return result["employee_id"]


def change(kind):
    return {
        "hire": {"hire_date": "2026-10-15"},
        "leave": {"status": "unpaid_leave", "status_effective_date": "2026-10-08",
                  "confirmation": routes.EMPLOYEE_PAYROLL_STATUS_CONFIRMATION},
        "salary": {"monthly_salary": 6200, "salary_effective_date": "2026-10-08",
                   "salary_confirmation": routes.EMPLOYEE_SALARY_CONFIRMATION},
    }[kind]


async def state(db):
    return {name: await db[name].find({}).to_list(1000) for name in (
        routes.EMPLOYEES, routes.SALARY_CONTRACTS, routes.EMPLOYEE_EVENTS,
        "users", "operational_balance_states_v1", "mezan_role_assignments_v2")}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["hire", "leave", "salary"])
@pytest.mark.parametrize("history", ["operational", "journal"])
async def test_recorded_days_cannot_be_silently_changed(db, kind, history):
    employee_id = await setup_employee(db)
    if history == "operational":
        await db.operational_balance_states_v1.insert_one({
            "_id": "owner", "owner_id": "owner", "revision": 7, "status": "active",
            "engine": {"salary_days": {f"salary:{employee_id}:2026-10-10": {"amount": "100.00"}}},
        })
    else:
        await db.mz2_employee_financial_events.insert_one({
            "user_id": "owner", "employee_id": employee_id, "kind": "salary_accrual",
            "period": "2026-10", "accrued_through": "2026-10-10", "status": "posted",
        })
    before = await state(db)
    with pytest.raises(HTTPException) as exc:
        await endpoint(db, "PUT")(employee_id, {"expected_version": 1, **change(kind)}, OWNER)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "employee_salary_adjustment_required"
    assert await state(db) == before


@pytest.mark.asyncio
async def test_forward_salary_edit_invalidates_operational_cas_without_changing_balances(db):
    employee_id = await setup_employee(db)
    original = {"_id": "owner", "owner_id": "owner", "revision": 7, "status": "active",
                "openings": {"unchanged": "100"}, "movements": [],
                "engine": {"salary_days": {f"salary:{employee_id}:2026-10-10": {"amount": "100.00"}}}}
    await db.operational_balance_states_v1.insert_one(original)
    result = await endpoint(db, "PUT")(employee_id, {
        "expected_version": 1, **change("salary"), "salary_effective_date": "2026-10-17",
    }, OWNER)
    current = await db.operational_balance_states_v1.find_one({"_id": "owner"})
    assert current == {**original, "revision": 8, "salary_contract_revision": 1}
    assert result["management"]["employees"][0]["salary_contract"]["scheduled_salary_revisions"][0]["monthly_amount"] == 6200
    stale = await db.operational_balance_states_v1.replace_one({"_id": "owner", "revision": 7}, original)
    assert stale.matched_count == 0


@pytest.mark.asyncio
async def test_status_audit_failure_rolls_back_contract_identity_login_and_role(db, monkeypatch):
    employee_id = await setup_employee(db)
    await db.mezan_employees_v2.update_one({"id": employee_id}, {"$set": {"account_user_id": "login"}})
    await db.users.insert_one({"id": "login", "created_by": "owner", "role": "viewer", "disabled": False, "is_active": True})
    await db.mezan_role_assignments_v2.insert_one({"id": "assignment", "user_id": "login", "owner_user_id": "owner", "role_key": "preparation_operator", "enabled": True, "effective_permissions": ["preparation.assigned.read"]})
    before = await state(db)
    async def fail(*args, **kwargs):
        raise RuntimeError("injected audit failure")
    monkeypatch.setattr(routes, "_record_employee_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        await endpoint(db, "PUT")(employee_id, {"expected_version": 1, **change("leave")}, OWNER)
    assert await state(db) == before


@pytest.mark.asyncio
async def test_malformed_persisted_leave_history_blocks_status_edit(db):
    employee_id = await setup_employee(db)
    await db.mezan_employee_salary_contracts_v2.update_one({"employee_id": employee_id}, {"$set": {"suspension_periods": [{"started_on": "bad-date"}]}})
    before = await state(db)
    with pytest.raises(HTTPException) as exc:
        await endpoint(db, "PUT")(employee_id, {"expected_version": 1, **change("leave")}, OWNER)
    assert exc.value.status_code == 422
    assert await state(db) == before


@pytest.mark.asyncio
async def test_concurrent_operational_accrual_forces_salary_edit_to_recheck_history(db, monkeypatch):
    employee_id = await setup_employee(db)
    await db.operational_balance_states_v1.insert_one({
        "_id": "owner", "owner_id": "owner", "revision": 1, "status": "active",
        "engine": {"salary_days": {}},
    })
    reached, release = asyncio.Event(), asyncio.Event()
    original = routes._invalidate_operational_salary_snapshot
    attempts = 0
    async def fence(tx, owner):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            reached.set()
            await asyncio.wait_for(release.wait(), 10)
        await original(tx, owner)
    monkeypatch.setattr(routes, "_invalidate_operational_salary_snapshot", fence)
    editing = asyncio.create_task(endpoint(db, "PUT")(
        employee_id, {"expected_version": 1, **change("salary")}, OWNER))
    try:
        await asyncio.wait_for(reached.wait(), 10)
        await db.operational_balance_states_v1.update_one(
            {"_id": "owner", "revision": 1},
            {"$inc": {"revision": 1}, "$set": {f"engine.salary_days.salary:{employee_id}:2026-10-10": {"amount": "100.00"}}},
        )
    finally:
        release.set()
    with pytest.raises(HTTPException) as exc:
        await editing
    assert exc.value.detail["code"] == "employee_salary_adjustment_required"
    assert (await db.mezan_employee_salary_contracts_v2.find_one({}))["monthly_amount"] == 3100
    assert (await db.mezan_employees_v2.find_one({}))["version"] == 1


async def setup_recorded_today_with_login(db, *, resuming):
    employee_id = await setup_employee(db)
    await db.mezan_employees_v2.update_one({"id": employee_id}, {"$set": {
        "account_user_id": "login", "status": "unpaid_leave" if resuming else "active",
    }})
    if resuming:
        await db.mezan_employee_salary_contracts_v2.update_one({"employee_id": employee_id}, {"$set": {
            "status": "paused", "payroll_state": "unpaid_leave",
            "suspension_periods": [{"id": "leave", "started_on": "2026-10-08", "returned_on": None}],
        }})
    await db.users.insert_one({"id": "login", "created_by": "owner", "role": "viewer",
                               "disabled": resuming, "is_active": not resuming})
    await db.mezan_role_assignments_v2.insert_one({
        "id": "assignment", "user_id": "login", "owner_user_id": "owner",
        "role_key": "preparation_operator", "enabled": not resuming,
        "effective_permissions": [] if resuming else ["preparation.assigned.read"],
        **({"suspended_by_employee_v2": True, "enabled_before_employee_suspension": True} if resuming else {}),
    })
    operational = {"_id": "owner", "owner_id": "owner", "revision": 7, "status": "active",
                   "openings": {"unchanged": "100"}, "movements": [],
                   "engine": {"salary_days": {f"salary:{employee_id}:2026-10-16": {
                       "amount": "0.00" if resuming else "100.00",
                   }}}}
    await db.operational_balance_states_v1.insert_one(operational)
    return employee_id, operational


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["unpaid_leave", "inactive", "active"])
@pytest.mark.parametrize("explicit_tomorrow", [False, True])
async def test_status_changes_login_now_and_payroll_tomorrow_preserving_recorded_today(db, target, explicit_tomorrow):
    resuming = target == "active"
    employee_id, original = await setup_recorded_today_with_login(db, resuming=resuming)
    payload = {"expected_version": 1, "status": target,
               "confirmation": routes.EMPLOYEE_PAYROLL_STATUS_CONFIRMATION}
    if explicit_tomorrow:
        payload["status_effective_date"] = "2026-10-17"
    result = await endpoint(db, "PUT")(employee_id, payload, OWNER)
    employee = await db.mezan_employees_v2.find_one({"id": employee_id})
    contract = await db.mezan_employee_salary_contracts_v2.find_one({"employee_id": employee_id})
    salary = contract_salary_row(contract, employee)
    assert salary_active_on(salary, date(2026, 10, 16)) is (not resuming)
    assert salary_active_on(salary, date(2026, 10, 17)) is resuming
    account = await db.users.find_one({"id": "login"})
    role = await db.mezan_role_assignments_v2.find_one({"id": "assignment"})
    assert account["is_active"] is resuming
    assert account["disabled"] is (not resuming)
    assert role["enabled"] is resuming
    assert bool(role["effective_permissions"]) is resuming
    assert employee["payroll_status_effective_date"] == "2026-10-17"
    assert result["management"]["employees"][0]["payroll_status_effective_date"] == "2026-10-17"
    assert await db.operational_balance_states_v1.find_one({"_id": "owner"}) == {
        **original, "revision": 8, "salary_contract_revision": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["unpaid_leave", "inactive", "active"])
async def test_status_beyond_tomorrow_rejected_without_any_changes(db, target):
    employee_id, _ = await setup_recorded_today_with_login(db, resuming=target == "active")
    before = await state(db)
    with pytest.raises(HTTPException) as exc:
        await endpoint(db, "PUT")(employee_id, {
            "expected_version": 1, "status": target, "status_effective_date": "2026-10-18",
            "confirmation": routes.EMPLOYEE_PAYROLL_STATUS_CONFIRMATION,
        }, OWNER)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "employee_payroll_status_effective_date_future"
    assert await state(db) == before
