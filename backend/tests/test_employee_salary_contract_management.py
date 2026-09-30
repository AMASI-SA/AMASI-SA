"""Public Employee OS salary writes against a dedicated real Mongo database."""
import asyncio
import copy
import os
from datetime import date
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import employees_v2_routes as routes
from employee_payroll_status import contract_salary_row, salary_accrual_for_period, salary_amount_on


@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI must address a disposable replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client["employee_salary_contract_test_" + uuid4().hex]
    assert (await database.command("hello")).get("setName")
    await database.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0, "writes_paused": False})
    yield database
    await client.drop_database(database.name)
    client.close()


OWNER = {"id": "owner", "role": "owner", "name": "Synthetic owner"}


def endpoint(db, method):
    router = routes.make_employees_v2_router(db, lambda: OWNER)
    path = "/employees-v2/management/employees" + ("/{employee_id}" if method == "PUT" else "")
    return next(r.endpoint for r in router.routes if r.path == path and method in r.methods)


async def create(db, **extra):
    return await endpoint(db, "POST")({
        "confirmation": routes.EMPLOYEE_CREATE_CONFIRMATION,
        "name": "المحاسب", "hire_date": "2026-10-01", "status": "active", **extra,
    }, OWNER)


async def salary(db, employee_id, amount=3000, effective="2026-10-01", version=1):
    return await endpoint(db, "PUT")(employee_id, {
        "expected_version": version, "monthly_salary": amount,
        "salary_effective_date": effective,
        "salary_confirmation": routes.EMPLOYEE_SALARY_CONFIRMATION,
        "salary_reason": "Annual review",
    }, OWNER)


@pytest.mark.asyncio
async def test_create_employee_and_salary_are_real_and_audited(db):
    result = await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    employee = await db.mezan_employees_v2.find_one({"id": result["employee_id"]})
    contract = await db.mezan_employee_salary_contracts_v2.find_one({"employee_id": employee["id"]})
    assert contract["monthly_amount"] == 3000
    assert contract["user_id"] == "owner"
    assert employee["account_user_id"] is None
    assert employee["management"]["payroll_enabled"] is True
    audit = await db.mezan_employee_events_v2.find_one({"event_type": "employee_salary_contract_created"})
    assert audit["metadata"]["previous_monthly_amount"] is None
    assert audit["metadata"]["new_effective_from"] == "2026-10-01"
    assert audit["metadata"]["changed_by"] == "owner"


@pytest.mark.asyncio
async def test_existing_without_contract_preserves_login_permissions_and_balances(db):
    result = await create(db)
    employee_id = result["employee_id"]
    await db.mezan_employees_v2.update_one({"id": employee_id}, {"$set": {"account_user_id": "login"}})
    account = {"id": "login", "created_by": "owner", "email": "unchanged@example.invalid", "password": "synthetic-hash", "role": "viewer"}
    await db.users.insert_one(copy.deepcopy(account))
    await db.ai_store_role_assignments.insert_one({"user_id": "login", "enabled": True, "warehouse_ids": ["w1"]})
    await db.general_ledger.insert_one({"user_id": "owner", "entity_type": "employee", "entity_id": employee_id, "status": "posted", "entry_type": "opening", "effective_at": "2026-09-30T21:00:00+00:00", "amount": 100})
    before = {name: await db[name].find({}).to_list(100) for name in ("users", "ai_store_role_assignments", "general_ledger")}
    # Opening is immutable, therefore first salary must begin after its covered day.
    await salary(db, employee_id, effective="2026-10-02")
    for name, rows in before.items():
        assert await db[name].find({}).to_list(100) == rows
    assert await db.mezan_employee_salary_contracts_v2.count_documents({}) == 1


@pytest.mark.asyncio
async def test_effective_history_future_schedule_and_calendar_proration(db):
    result = await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    await salary(db, result["employee_id"], 4000, "2026-10-16")
    contract = await db.mezan_employee_salary_contracts_v2.find_one({})
    history = contract["salary_revisions"]
    assert [(r["monthly_amount"], r["effective_from"], r["effective_to"]) for r in history] == [(3000, "2026-10-01", "2026-10-15"), (4000, "2026-10-16", None)]
    employee = await db.mezan_employees_v2.find_one({})
    row = contract_salary_row(contract, employee)
    assert salary_amount_on(row, date(2026, 10, 15)) == 3000
    assert salary_amount_on(row, date(2026, 10, 16)) == 4000
    assert salary_accrual_for_period(row, "2026-10") == 3516.13
    assert salary_accrual_for_period(row, "2026-10", through=date(2026, 10, 10)) == 967.74
    audit = await db.mezan_employee_events_v2.find_one({"event_type": "employee_salary_changed"})
    assert audit["metadata"]["previous_effective_from"] == "2026-10-01"
    assert audit["metadata"]["new_monthly_amount"] == 4000
    assert audit["metadata"]["reason"] == "Annual review"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_event", ["employee_created", "employee_salary_contract_created"])
async def test_any_create_audit_failure_aborts_employee_and_contract(db, monkeypatch, failure_event):
    original = routes._record_employee_event
    async def fail(*args, **kwargs):
        if kwargs["event_type"] == failure_event:
            raise RuntimeError("injected persistence failure")
        return await original(*args, **kwargs)
    monkeypatch.setattr(routes, "_record_employee_event", fail)
    with pytest.raises(RuntimeError):
        await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    for name in (routes.EMPLOYEES, routes.SALARY_CONTRACTS, routes.EMPLOYEE_EVENTS):
        assert await db[name].count_documents({}) == 0


@pytest.mark.asyncio
async def test_contract_insert_failure_rolls_back_employee(db, monkeypatch):
    from accounting_atomic import SessionCollection
    original = SessionCollection.__getattr__
    def fail(self, name):
        if self._collection.name == routes.SALARY_CONTRACTS and name == "insert_one":
            async def insert(*args, **kwargs):
                raise RuntimeError("salary storage unavailable")
            return insert
        return original(self, name)
    monkeypatch.setattr(SessionCollection, "__getattr__", fail)
    with pytest.raises(RuntimeError):
        await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    assert await db.mezan_employees_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_update_audit_failure_rolls_back_contract_and_employee_version(db, monkeypatch):
    result = await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    before = {name: await db[name].find({}).to_list(100) for name in (routes.EMPLOYEES, routes.SALARY_CONTRACTS, routes.EMPLOYEE_EVENTS)}
    async def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(routes, "_record_employee_event", fail)
    with pytest.raises(RuntimeError):
        await salary(db, result["employee_id"], 4000, "2026-10-16")
    for name, rows in before.items():
        assert await db[name].find({}).to_list(100) == rows


@pytest.mark.asyncio
async def test_posted_backdate_and_same_day_fail_closed(db):
    result = await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    employee_id = result["employee_id"]
    await db.general_ledger.insert_one({"user_id": "owner", "entity_type": "employee", "entity_id": employee_id, "status": "posted", "metadata": {"period": "2026-10"}})
    for effective in ("2026-10-01", "2026-10-16"):
        with pytest.raises(HTTPException) as exc:
            await salary(db, employee_id, 4000, effective)
        assert exc.value.status_code in (409, 422)
    assert (await db.mezan_employee_salary_contracts_v2.find_one({}))["monthly_amount"] == 3000
    assert (await db.mezan_employees_v2.find_one({}))["version"] == 1


@pytest.mark.asyncio
async def test_concurrent_salary_changes_only_one_wins(db):
    result = await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    outcomes = await asyncio.gather(salary(db, result["employee_id"], 4000, "2026-10-16"), salary(db, result["employee_id"], 5000, "2026-10-17"), return_exceptions=True)
    assert sum(isinstance(item, HTTPException) and item.status_code == 409 for item in outcomes) == 1
    assert len((await db.mezan_employee_salary_contracts_v2.find_one({}))["salary_revisions"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,effective", [(0, "2026-10-01"), (-1, "2026-10-01"), ("NaN", "2026-10-01"), ("Infinity", "2026-10-01"), (True, "2026-10-01"), (3000, "20261001"), (3000, "2026-02-30"), (3000, "2026-09-30")])
async def test_invalid_salary_rejected_without_partial_identity(db, amount, effective):
    with pytest.raises(HTTPException):
        await create(db, monthly_salary=amount, salary_effective_date=effective)
    assert await db.mezan_employees_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_paused_writes_and_non_owner_remain_denied(db):
    await db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"writes_paused": True}})
    with pytest.raises(HTTPException) as exc:
        await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    assert exc.value.status_code == 423
    with pytest.raises(HTTPException) as exc:
        await endpoint(db, "POST")({}, {"id": "other", "role": "viewer"})
    assert exc.value.status_code == 403
    assert await db.mezan_employees_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_leave_return_and_salary_revision_preserve_unpaid_days(db, monkeypatch):
    monkeypatch.setattr(routes, "riyadh_today", lambda: date(2026, 10, 31))
    result = await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    employee_id = result["employee_id"]
    for version, status, effective in [(1, "unpaid_leave", "2026-10-10"), (2, "active", "2026-10-20")]:
        await endpoint(db, "PUT")(employee_id, {
            "expected_version": version, "status": status, "status_effective_date": effective,
            "confirmation": routes.EMPLOYEE_PAYROLL_STATUS_CONFIRMATION,
        }, OWNER)
    await salary(db, employee_id, 4000, "2026-10-16", version=3)
    contract = await db.mezan_employee_salary_contracts_v2.find_one({})
    employee = await db.mezan_employees_v2.find_one({})
    row = contract_salary_row(contract, employee)
    # Nine days at 3000; Oct 10-19 unpaid; twelve days at 4000.
    assert salary_accrual_for_period(row, "2026-10") == 2419.35
    assert contract["suspension_periods"][0]["returned_on"] == "2026-10-20"


@pytest.mark.asyncio
async def test_future_salary_is_not_displayed_as_current(db, monkeypatch):
    monkeypatch.setattr(routes, "riyadh_today", lambda: date(2026, 10, 10))
    result = await create(db, monthly_salary=3000, salary_effective_date="2026-10-01")
    result = await salary(db, result["employee_id"], 4000, "2026-10-16")
    displayed = result["management"]["employees"][0]["salary_contract"]
    assert displayed["current_monthly_amount"] == 3000
    assert displayed["current_effective_from"] == "2026-10-01"
    assert displayed["salary_revisions"][-1]["monthly_amount"] == 4000


@pytest.mark.asyncio
async def test_native_inactive_employee_and_cross_tenant_salary_access(db):
    result = await create(db, monthly_salary=3000, salary_effective_date="2026-10-01", status="inactive")
    contract = await db.mezan_employee_salary_contracts_v2.find_one({})
    employee = await db.mezan_employees_v2.find_one({})
    assert salary_accrual_for_period(contract_salary_row(contract, employee), "2026-10") == 0
    await db.mz2_atomic_owners.insert_one({"_id": "other-owner", "revision": 0, "writes_paused": False})
    with pytest.raises(HTTPException) as exc:
        await endpoint(db, "PUT")(result["employee_id"], {
            "expected_version": 1, "monthly_salary": 4000, "salary_effective_date": "2026-10-16",
            "salary_confirmation": routes.EMPLOYEE_SALARY_CONFIRMATION,
        }, {"id": "other-owner", "role": "owner"})
    assert exc.value.status_code == 404
    assert (await db.mezan_employee_salary_contracts_v2.find_one({}))["monthly_amount"] == 3000


def test_invalid_revision_history_fails_closed():
    from employee_payroll_status import normalized_salary_revisions
    first = {"monthly_amount": 3000, "effective_from": "2026-10-01", "effective_to": "2026-10-18"}
    second = {"monthly_amount": 4000, "effective_from": "2026-10-16"}
    with pytest.raises(ValueError, match="overlap_or_gap"):
        normalized_salary_revisions({"salary_revisions": [first, second]})
    with pytest.raises(ValueError, match="invalid"):
        normalized_salary_revisions({"salary_revisions": [second, second]})


@pytest.mark.asyncio
async def test_http_create_add_and_change_salary_responses_are_json(db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    app = FastAPI()
    app.include_router(routes.make_employees_v2_router(db, lambda: OWNER), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/employees-v2/management/employees", json={
            "confirmation": routes.EMPLOYEE_CREATE_CONFIRMATION, "name": "المحاسب", "hire_date": "2026-10-01",
        })
        assert response.status_code == 200
        employee_id = response.json()["employee_id"]
        for version, amount, effective in [(1, 3000, "2026-10-01"), (2, 4000, "2026-10-16")]:
            response = await client.put(f"/api/employees-v2/management/employees/{employee_id}", json={
                "expected_version": version, "monthly_salary": amount,
                "salary_effective_date": effective, "salary_confirmation": routes.EMPLOYEE_SALARY_CONFIRMATION,
            })
            assert response.status_code == 200
            assert response.json()["management"]["employees"][0]["salary_contract"]["monthly_amount"] == amount
