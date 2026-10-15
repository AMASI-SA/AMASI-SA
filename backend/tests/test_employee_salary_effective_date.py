"""Real HTTP + disposable Mongo proofs for salary schedule corrections only."""
from datetime import date, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import employee_payroll_status as payroll
import employees_v2_routes as routes
from tests.test_employee_salary_contract_management import OWNER, db  # shared real-replica fixture


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(routes, "riyadh_today", lambda: date(2026, 10, 5))
    monkeypatch.setattr(payroll, "riyadh_today", lambda: date(2026, 10, 5))


@pytest_asyncio.fixture
async def client(db, monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorDatabase
    original = AsyncIOMotorDatabase.__getitem__

    def mz2_only(self, name):
        assert name != "operating_salaries", "Legacy salary access is forbidden"
        return original(self, name)

    monkeypatch.setattr(AsyncIOMotorDatabase, "__getitem__", mz2_only)
    app = FastAPI()
    app.include_router(routes.make_employees_v2_router(db, lambda: OWNER), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://salary-test") as http:
        yield http


async def create(client, effective="2026-10-31"):
    response = await client.post("/api/employees-v2/management/employees", json={
        "confirmation": routes.EMPLOYEE_CREATE_CONFIRMATION,
        "name": "Synthetic salary employee", "hire_date": "2026-10-01", "status": "active",
        "monthly_salary": 1500, "salary_effective_date": effective,
    })
    assert response.status_code == 200, response.text
    return response.json()["management"]["employees"][0]


async def save(client, employee, amount=1500, effective="2026-10-20", **extra):
    return await client.put(f"/api/employees-v2/management/employees/{employee['id']}", json={
        "expected_version": employee["version"], "monthly_salary": amount,
        "salary_effective_date": effective,
        "salary_revision_id": employee["salary_contract"]["editable_revision_id"],
        "salary_confirmation": routes.EMPLOYEE_SALARY_CONFIRMATION, **extra,
    })


async def reopen(client):
    response = await client.get("/api/employees-v2/management")
    assert response.status_code == 200, response.text
    return response.json()["management"]["employees"][0]


async def business_state(db):
    # The operational transaction's serialization counter is not salary state.
    return {name: await db[name].find({}).to_list(1000)
            for name in await db.list_collection_names() if name != "mz2_atomic_owners"}


@pytest.mark.asyncio
@pytest.mark.parametrize("effective,current", [("2026-10-01", 1500), ("2026-10-31", 0)])
async def test_get_saved_date_and_current_vs_future(client, effective, current):
    await create(client, effective)
    contract = (await reopen(client))["salary_contract"]
    assert contract["effective_from"] == effective
    assert contract["editable_effective_from"] == effective
    assert contract["current_monthly_amount"] == current
    assert contract["current_effective_from"] == (effective if current else None)
    assert len(contract["scheduled_salary_revisions"]) == (0 if current else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,effective", [(1500, "2026-10-20"), (1800, "2026-10-31"), (1800, "2026-10-20")])
async def test_future_date_only_amount_only_both_persist_and_reopen(client, db, amount, effective):
    employee = await create(client)
    original = await db[routes.SALARY_CONTRACTS].find_one({"employee_id": employee["id"]})
    response = await save(client, employee, amount, effective)
    assert response.status_code == 200, response.text
    after = await db[routes.SALARY_CONTRACTS].find_one({"employee_id": employee["id"]})
    assert after["id"] == original["id"]
    assert await db[routes.SALARY_CONTRACTS].count_documents({}) == 1
    assert after["monthly_amount"] == amount
    assert after["effective_from"] == effective
    assert after["salary_revisions"][0]["effective_from"] == effective
    assert after["salary_revision_corrections"][0]["before"] == original["salary_revisions"]
    assert after["salary_revision_corrections"][0]["after"] == after["salary_revisions"]
    assert "effective_start_date" not in after
    audit = await db[routes.EMPLOYEE_EVENTS].find_one({"event_type": "employee_salary_changed"})
    assert audit["before"]["effective_from"] == "2026-10-31"
    assert audit["after"]["effective_from"] == effective
    assert audit["metadata"]["operation"] == "amend_future"
    opened = await reopen(client)
    assert opened["salary_contract"]["editable_effective_from"] == effective
    assert opened["salary_contract"]["monthly_amount"] == amount
    assert opened["salary_contract"]["current_monthly_amount"] == 0
    assert opened == response.json()["management"]["employees"][0]
    row = payroll.contract_salary_row(after, await db[routes.EMPLOYEES].find_one({}))
    start = date.fromisoformat(effective)
    assert payroll.salary_accrual_for_period(row, start.strftime("%Y-%m"), through=start) == 0
    first = start + timedelta(days=1)
    expected_daily = round(amount / (30 if first.month == 11 else 31), 2)
    assert payroll.salary_accrual_for_period(row, first.strftime("%Y-%m"), through=first) == expected_daily
    for name in ("general_ledger", "liabilities", "mz2_employee_financial_events", "accounts", "banks"):
        assert await db[name].count_documents({}) == 0


@pytest.mark.asyncio
async def test_unchanged_pair_and_repeated_save_do_not_duplicate(client, db):
    employee = await create(client)
    before = await business_state(db)
    for _ in range(2):
        result = await save(client, employee, effective="2026-10-31")
        assert result.status_code == 200, result.text
    assert await business_state(db) == before
    result = await save(client, employee)
    assert result.status_code == 200
    employee = result.json()["management"]["employees"][0]
    before = await business_state(db)
    assert (await save(client, employee)).status_code == 200
    assert await business_state(db) == before
    # Retrying the old request fails closed at optimistic concurrency.
    employee["version"] -= 1
    assert (await save(client, employee)).status_code == 409
    assert await business_state(db) == before


@pytest.mark.asyncio
async def test_current_amount_change_appends_without_rewriting_history(client, db):
    employee = await create(client, "2026-10-01")
    response = await save(client, employee, 1800, "2026-10-06")
    assert response.status_code == 200, response.text
    contract = (await reopen(client))["salary_contract"]
    assert contract["effective_from"] == "2026-10-01"
    assert contract["editable_effective_from"] == "2026-10-06"
    assert contract["current_monthly_amount"] == 1500
    assert [(r["monthly_amount"], r["effective_from"], r["effective_to"]) for r in contract["salary_revisions"]] == [
        (1500, "2026-10-01", "2026-10-05"), (1800, "2026-10-06", None)]
    # Move this future revision without changing the historical salary amount.
    employee = await reopen(client)
    response = await save(client, employee, 1800, "2026-10-10")
    assert response.status_code == 200, response.text
    after = (await reopen(client))["salary_contract"]
    assert len(after["salary_revisions"]) == 2
    assert after["salary_revisions"][0]["monthly_amount"] == 1500
    assert after["salary_revisions"][0]["effective_from"] == "2026-10-01"
    assert after["salary_revision_corrections"][0]["before"] == contract["salary_revisions"]
    assert after["current_monthly_amount"] == 1500


@pytest.mark.asyncio
async def test_future_correction_cannot_backdate_or_touch_posted_period(client, db):
    employee = await create(client)
    before = await business_state(db)
    response = await save(client, employee, effective="2026-10-04")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "employee_salary_future_correction_backdated"
    assert await business_state(db) == before
    await db.mz2_employee_financial_events.insert_one({
        "user_id": "owner", "employee_id": employee["id"],
        "period": "2026-10", "accrued_through": "2026-10-31", "status": "posted",
    })
    before = await business_state(db)
    # Moving later also affects the former effective interval, so must be blocked.
    assert (await save(client, employee, effective="2026-11-01")).status_code == 409
    assert await business_state(db) == before


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_schedule_and_correction_history(client, db, monkeypatch):
    employee = await create(client)
    before = await business_state(db)
    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(routes, "_record_employee_event", fail)
    with pytest.raises(RuntimeError, match="audit failure"):
        await save(client, employee)
    assert await business_state(db) == before


@pytest.mark.asyncio
async def test_boundary_day_activates_without_save_and_prestart_accrual_zero(client, db, monkeypatch):
    await create(client)
    for day, expected in [(date(2026, 10, 30), 0), (date(2026, 10, 31), 1500)]:
        monkeypatch.setattr(routes, "riyadh_today", lambda: day)
        result = await reopen(client)
        assert result["salary_contract"]["current_monthly_amount"] == expected
    contract = await db[routes.SALARY_CONTRACTS].find_one({})
    employee = await db[routes.EMPLOYEES].find_one({})
    row = payroll.contract_salary_row(contract, employee)
    assert payroll.salary_accrual_for_period(row, "2026-10", through=date(2026, 10, 30)) == 0
    # Pure reads and setup have no daily financial-write side effects.
    assert await db.mz2_employee_financial_events.count_documents({}) == 0
    assert await db.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_stale_revision_target_denied_and_contract_unchanged(client, db):
    employee = await create(client)
    before = await business_state(db)
    result = await save(client, employee, salary_revision_id="other-revision")
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "employee_salary_revision_conflict"
    assert await business_state(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("effective", ["2026-10-05", "2026-10-06", "2026-10-20"])
async def test_next_day_policy_today_tomorrow_future_and_repeated_reads(client, db, effective):
    from liabilities_routes import _compute_employee_accrual
    await create(client, effective)
    contract = await db[routes.SALARY_CONTRACTS].find_one({})
    employee = await db[routes.EMPLOYEES].find_one({})
    row = payroll.contract_salary_row(contract, employee)
    start = date.fromisoformat(effective)
    first = start + timedelta(days=1)
    before = await business_state(db)
    for _ in range(3):
        assert payroll.salary_amount_on(contract, start) == 1500
        assert payroll.salary_accrual_for_period(row, "2026-10", through=start) == 0
        assert payroll.salary_accrual_for_period(row, "2026-10", through=first) == 48.39
        assert _compute_employee_accrual(row, today=start)["accrued"] == 0
        assert _compute_employee_accrual(row, today=first)["accrued"] == 48.39
        assert row["accrual_start_date"] == first.isoformat()
    assert await business_state(db) == before


@pytest.mark.asyncio
async def test_today_contract_can_correct_amount_same_date_before_first_accrual(client, db):
    employee = await create(client, "2026-10-05")
    response = await save(client, employee, 1800, "2026-10-05")
    assert response.status_code == 200, response.text
    contract = response.json()["management"]["employees"][0]["salary_contract"]
    assert contract["effective_from"] == "2026-10-05"
    assert contract["current_monthly_amount"] == 1800
    assert contract["first_accrual_date"] == "2026-10-06"
    assert len(contract["salary_revisions"]) == 1
    assert contract["salary_revision_corrections"][0]["before"][0]["monthly_amount"] == 1500
    assert payroll.salary_accrual_for_period(contract, "2026-10", through=date(2026, 10, 5)) == 0
    assert payroll.salary_accrual_for_period(contract, "2026-10", through=date(2026, 10, 6)) == 58.06


@pytest.mark.parametrize("start,first,expected", [
    ("2026-10-31", "2026-11-01", 50.0),
    ("2027-02-01", "2027-02-02", 53.57),
    ("2028-02-01", "2028-02-02", 51.72),
])
def test_next_day_uses_actual_accrual_month_length(start, first, expected):
    contract = {"monthly_amount": 1500, "effective_from": start, "status": "active"}
    assert payroll.salary_accrual_for_period(contract, start[:7], through=date.fromisoformat(start)) == 0
    assert payroll.salary_accrual_for_period(contract, first[:7], through=date.fromisoformat(first)) == expected
