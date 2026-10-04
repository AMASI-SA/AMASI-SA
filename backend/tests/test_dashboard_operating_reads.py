"""Dashboard input parity with the unchanged operating/payroll calculators."""
import os
import sys
import uuid
from datetime import date

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from dashboard_operating_reads import load_dashboard_operating_inputs
from dashboard_spill import DashboardSpill
from employee_payroll_status import EMPLOYEES_COLLECTION, SALARY_CONTRACTS_COLLECTION
from expenses_routes import compute_operating_expenses_for_range


@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("DASHBOARD_TEST_MONGO_URI")
    if not uri:
        pytest.skip("isolated Mongo URI required")
    assert uri.startswith("mongodb://127.0.0.1:")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    database = client["dashboard_operating_" + uuid.uuid4().hex]
    try:
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


def employee(identity, **extra):
    return dict(user_id="owner", id=identity, status="active", hire_date="2026-01-01", **extra)


def contract(identity, **extra):
    return dict(user_id="owner", id="contract-" + str(identity), employee_id=identity,
                effective_from="2026-01-01", monthly_amount=100.005, **extra)


async def bounded(db, start=date(2026, 9, 29), end=date(2026, 10, 3)):
    store = DashboardSpill()
    try:
        inputs = await load_dashboard_operating_inputs(db, "owner", store)
        result = await compute_operating_expenses_for_range(db, "owner", start, end, dashboard_inputs=inputs)
        return result, inputs.metrics
    finally:
        store.close(flush=False)


@pytest.mark.asyncio
async def test_rich_totals_match_default_across_month_and_salary_revisions(db):
    await db[EMPLOYEES_COLLECTION].insert_many([employee("a"), employee("b"), employee("archived", archived=True)])
    await db[SALARY_CONTRACTS_COLLECTION].insert_many([
        contract("a", salary_revisions=[
            dict(effective_from="2026-01-01", monthly_amount=123.45),
            dict(effective_from="2026-10-01", monthly_amount=234.56),
        ], suspension_periods=[dict(started_on="2026-09-30", returned_on="2026-10-02")]),
        contract("b"), contract("archived"), contract("missing"),
    ])
    await db.operating_salaries.insert_many([
        dict(user_id="owner", category="household", monthly_amount=31.005, start_date="bad"),
        dict(user_id="owner", category="charity", monthly_amount=60.01, start_date="2026-10-01"),
        dict(user_id="owner", category="employee", monthly_amount=999999),
        dict(user_id="other", category="household", monthly_amount=999999),
    ])
    await db.operating_rentals.insert_many([
        dict(user_id="owner", annual_amount=365.5, start_date="2026-01-01", end_date="2026-09-30"),
        dict(user_id="owner", annual_amount=731.11, start_date="2026-10-01", end_date="2026-12-31"),
    ])
    await db.operating_prepaid_expenses.insert_many([
        dict(user_id="owner", amount=20.005, start_date="2026-09-29", end_date="2026-10-03", expense_type="other"),
        dict(user_id="owner", amount=5.67, start_date="bad", end_date="bad", expense_type="insurance"),
    ])
    await db.operating_daily_expenses.insert_many([
        dict(user_id="owner", date="2026-10-01", amount=1.005),
        dict(user_id="owner", date="2026-10-02", amount=-0.015),
        dict(user_id="owner", date="2026-11-01", amount=999999),
    ])
    expected = await compute_operating_expenses_for_range(db, "owner", date(2026, 9, 29), date(2026, 10, 3))
    actual, metrics = await bounded(db)
    assert actual == expected
    assert actual["salaries_employee"] > 0
    assert actual["salaries_household"] > 0
    assert actual["salaries_charity"] > 0
    assert actual["rentals_total"] > 0
    assert actual["prepaid_total"] > 0
    assert metrics["max_mongo_batch"] <= 128


@pytest.mark.asyncio
async def test_empty_and_reverse_range_preserve_default(db):
    start, end = date(2026, 10, 3), date(2026, 9, 29)
    actual, _ = await bounded(db, start, end)
    assert actual == await compute_operating_expenses_for_range(db, "owner", start, end)


@pytest.mark.asyncio
async def test_duplicate_contract_error_precedes_invalid_revision(db):
    await db[EMPLOYEES_COLLECTION].insert_one(employee("a"))
    await db[SALARY_CONTRACTS_COLLECTION].insert_many([
        contract("a", salary_revisions="invalid"), contract("a"),
    ])
    with pytest.raises(ValueError, match="employee_salary_multiple_contracts"):
        await compute_operating_expenses_for_range(db, "owner", date(2026, 10, 1), date(2026, 10, 1))
    with pytest.raises(ValueError, match="employee_salary_multiple_contracts"):
        await bounded(db)


@pytest.mark.asyncio
async def test_employee_exact_identity_and_last_eligible_duplicate_match_default(db):
    await db[EMPLOYEES_COLLECTION].insert_many([
        employee("a"), employee("a", effective_to="2026-09-30"),
        employee("a", archived=True), employee(" spaced "), employee(42),
    ])
    await db[SALARY_CONTRACTS_COLLECTION].insert_many([
        contract("a"), contract("spaced"), contract("42"),
    ])
    actual, _ = await bounded(db)
    assert actual == await compute_operating_expenses_for_range(db, "owner", date(2026, 9, 29), date(2026, 10, 3))


@pytest.mark.asyncio
async def test_employee_lookup_retains_original_matching_document_bound(db):
    first = employee("a")
    second = employee("a")
    second["hire_date"] = "2099-01-01"
    await db[EMPLOYEES_COLLECTION].insert_many([first, second])
    await db[SALARY_CONTRACTS_COLLECTION].insert_one(contract("a"))
    actual, _ = await bounded(db)
    expected = await compute_operating_expenses_for_range(db, "owner", date(2026, 9, 29), date(2026, 10, 3))
    assert actual == expected
    assert actual["salaries_employee"] > 0


@pytest.mark.asyncio
async def test_complete_employee_cohort_above_legacy_10000_contract_cap(db):
    for offset in range(0, 10001, 128):
        indexes = range(offset, min(offset + 128, 10001))
        await db[EMPLOYEES_COLLECTION].insert_many([employee(str(index)) for index in indexes])
        contracts = [contract(str(index)) for index in indexes]
        for item in contracts:
            item["monthly_amount"] = 31
        await db[SALARY_CONTRACTS_COLLECTION].insert_many(contracts)
    target = date(2026, 10, 1)
    legacy = await compute_operating_expenses_for_range(db, "owner", target, target)
    actual, metrics = await bounded(db, target, target)
    assert legacy["salaries_employee"] == 10000.0
    assert actual["salaries_employee"] == actual["operating_total"] == 10001.0
    assert metrics["documents_loaded"][SALARY_CONTRACTS_COLLECTION] == 10001
    assert metrics["max_mongo_batch"] <= 128


@pytest.mark.asyncio
async def test_adapter_read_failure_closes_cursor_and_propagates():
    class Cursor:
        closed = False
        def batch_size(self, size):
            assert size == 128
            return self
        async def to_list(self, length):
            raise RuntimeError("injected operating read failure")
        async def close(self):
            self.closed = True
    class Collection:
        def __init__(self, cursor):
            self.cursor = cursor
        def find(self, query, fields):
            return self.cursor
    cursor = Cursor()
    store = DashboardSpill()
    directory = store.directory
    try:
        with pytest.raises(RuntimeError, match="injected operating read failure"):
            await load_dashboard_operating_inputs({"operating_salaries": Collection(cursor)}, "owner", store)
        assert cursor.closed
    finally:
        store.close(flush=False)
    assert not directory.exists()


@pytest.mark.asyncio
async def test_totals_only_avoids_historical_distinct_type_buffer(db):
    for offset in range(0, 2049, 128):
        await db.operating_prepaid_expenses.insert_many([
            dict(user_id="owner", amount=1.005 + index % 3, expense_type=f"historical-type-{index}",
                 start_date="2026-10-01", end_date="2026-10-03", status="active")
            for index in range(offset, min(offset + 128, 2049))
        ])
    start, end = date(2026, 10, 1), date(2026, 10, 3)
    expected = await compute_operating_expenses_for_range(db, "owner", start, end)
    store = DashboardSpill()
    observed = []
    old_profile = sys.getprofile()
    def profile(frame, event, result):
        if (frame.f_code is compute_operating_expenses_for_range.__code__
                and event == "return" and isinstance(result, dict)):
            details = frame.f_locals["prepaid_by_type"]
            observed.append(None if details is None else len(details))
    try:
        inputs = await load_dashboard_operating_inputs(db, "owner", store, collect_details=False)
        sys.setprofile(profile)
        actual = await compute_operating_expenses_for_range(db, "owner", start, end, dashboard_inputs=inputs)
    finally:
        sys.setprofile(old_profile)
        store.close(flush=False)
    assert observed == [None]  # No distinct-type map was allocated by the reducer.
    assert len(expected["prepaid_by_type"]) == 2049
    assert actual["prepaid_by_type"] == {}
    assert {key: value for key, value in actual.items() if key != "prepaid_by_type"} == {
        key: value for key, value in expected.items() if key != "prepaid_by_type"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("collection,amount_field,total_field,category", [
    ("operating_salaries", "monthly_amount", "salaries_household", "household"),
    ("operating_rentals", "annual_amount", "rentals_total", None),
    ("operating_prepaid_expenses", "amount", "prepaid_total", None),
])
async def test_complete_cohort_above_legacy_5000_cap(db, collection, amount_field, total_field, category):
    for offset in range(0, 5001, 128):
        await db[collection].insert_many([
            dict(user_id="owner", category=category, **{amount_field: 31 if category else 365 if amount_field == "annual_amount" else 1},
                 start_date="2026-10-01", end_date="2026-10-01", status="active", expense_type="other")
            for _ in range(offset, min(offset + 128, 5001))
        ])
    target = date(2026, 10, 1)
    legacy = await compute_operating_expenses_for_range(db, "owner", target, target)
    actual, metrics = await bounded(db, target, target)
    assert legacy[total_field] == 5000.0
    assert actual[total_field] == 5001.0
    assert metrics["max_mongo_batch"] <= 128
