"""Real-Mongo context retains financial identities without a salary contract."""
import os

import pytest
import pytest_asyncio

from tests import test_mz2_employee_finance as finance_fixture
from accounting_employee_finance import payroll_context


@pytest_asyncio.fixture
async def finance():
    if not os.environ.get("MZ2_TEST_MONGO_URI"):
        pytest.skip("MZ2_TEST_MONGO_URI must address a disposable replica set")
    case = finance_fixture.MZ2EmployeeFinanceTests()
    await case.asyncSetUp()
    try:
        yield case
    finally:
        await case.asyncTearDown()


@pytest.mark.asyncio
async def test_contractless_employee_retains_native_advance_and_custody(finance):
    await finance.db.mezan_employee_salary_contracts_v2.delete_many({})
    movement = await finance.import_movement(debit=100, description="Synthetic custody", reference="NO-CONTRACT")
    await finance.classify(movement["id"], "custody_grant")
    context = await payroll_context(finance.db, finance.owner)
    assert context["ledger_status"] == "available"
    assert context["employees"] == [{
        "id": finance.employee, "employee_v2_id": finance.employee,
        "contract_id": None, "name": "Synthetic employee", "monthly_amount": 0,
        "salary_payable": 3000, "advance": 500, "custody": 100,
    }]


@pytest.mark.asyncio
async def test_directory_keeps_contract_rows_and_excludes_foreign_and_archived(finance):
    before = (await payroll_context(finance.db, finance.owner))["employees"][0]
    await finance.db.mezan_employees_v2.insert_many([
        {"id": "no-contract", "user_id": finance.owner, "display_name": "A no contract", "status": "inactive"},
        {"id": "foreign", "user_id": "other-owner", "display_name": "Foreign"},
        *[{"id": flag, "user_id": finance.owner, "display_name": flag, flag: True}
          for flag in ("archived", "is_archived", "deleted", "is_deleted")],
    ])
    # Legacy salaries are never a source for the context's employee directory.
    await finance.db.operating_salaries.insert_one({"id": "legacy-only", "user_id": finance.owner, "monthly_amount": 9999})
    rows = (await payroll_context(finance.db, finance.owner))["employees"]
    assert [row["id"] for row in rows] == ["no-contract", finance.employee]
    assert rows[1] == before
    assert rows[0]["contract_id"] is None
    assert rows[0]["monthly_amount"] == 0
    assert rows[0]["advance"] == rows[0]["custody"] == 0
