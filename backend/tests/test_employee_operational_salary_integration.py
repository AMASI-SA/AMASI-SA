"""Employee OS and operational salary reconciliation on disposable real Mongo."""
from copy import deepcopy
from datetime import date

import pytest
from fastapi import HTTPException

import employees_v2_routes as routes
from operational_balance_service import create_movement, refresh, report
from operational_balance_store import initial, read
from test_employee_salary_contract_management import db, create, endpoint, OWNER


START = "2026-10-05T09:00:00+00:00"
NOW = "2026-10-08T12:00:00+00:00"


async def seed(db):
    employee_id = (await create(db, monthly_salary=3100, salary_effective_date="2026-10-01"))["employee_id"]
    await db.mz2_financial_accounts.insert_one({"id": "bank", "user_id": "owner", "name": "Synthetic bank",
                                               "account_type": "bank", "currency": "SAR", "status": "active"})
    state = initial("owner")
    state.update(revision=1, status="active", started_at=START)
    await db.operational_balance_states_v1.insert_one(state)
    await refresh(db, "owner", clock=NOW)
    saved = await read(db, "owner")
    assert len(saved["engine"]["salary_days"]) == 3
    assert report(saved)["summary"]["payable"] == "300.00"
    return employee_id, saved


@pytest.mark.asyncio
async def test_source_change_persists_conflict_and_rejects_employee_payment_without_money_writes(db):
    employee_id, original = await seed(db)
    contract = await db.mezan_employee_salary_contracts_v2.find_one({"employee_id": employee_id})
    revisions = deepcopy(contract["salary_revisions"])
    revisions[0]["effective_to"] = "2026-10-06"
    revisions.append({"id": "synthetic-correction", "effective_from": "2026-10-07", "effective_to": None,
                      "monthly_amount": 6200})
    # Simulate legacy/import corruption outside the guarded Employee OS writer.
    await db.mezan_employee_salary_contracts_v2.update_one({"employee_id": employee_id},
        {"$set": {"salary_revisions": revisions, "monthly_amount": 6200}})
    await refresh(db, "owner", clock=NOW)
    conflicted = await read(db, "owner")
    assert set(conflicted["engine"]["salary_conflicts"]) == {
        f"salary:{employee_id}:2026-10-07", f"salary:{employee_id}:2026-10-08"}
    assert conflicted["engine"]["salary_days"] == original["engine"]["salary_days"]
    assert report(conflicted)["summary"]["payable"] == "300.00"
    financial_collections = ["accounting_journal_groups_v2", "accounting_general_ledger_v2",
                             "general_ledger", "mz2_employee_financial_events", "mz2_daily_movements",
                             "mz2_financial_accounts"]
    before = {name: await db[name].find({}).to_list(None) for name in financial_collections}
    payment = {"request_id": "salary-conflict-payment", "party_type": "employee", "party_id": employee_id,
               "bank_id": "bank", "amount": "100", "currency": "SAR", "direction": "outgoing",
               "kind": "payment", "note": "Synthetic payroll", "receipt_id": None, "order_number": None,
               "reference": "", "allocations": []}
    with pytest.raises(HTTPException) as caught:
        await create_movement(db, "owner", "owner", payment, clock=NOW)
    assert caught.value.detail["code"] == "operational_salary_adjustment_required"
    after = await read(db, "owner")
    assert after["movements"] == conflicted["movements"] == []
    assert after["openings"] == conflicted["openings"]
    assert after["engine"]["obligations"] == conflicted["engine"]["obligations"]
    for name in financial_collections:
        assert await db[name].find({}).to_list(None) == before[name]


@pytest.mark.asyncio
async def test_future_employee_salary_edit_fences_metadata_without_false_operational_conflict(db, monkeypatch):
    monkeypatch.setattr(routes, "riyadh_today", lambda: date(2026, 10, 16))
    employee_id, original = await seed(db)
    await endpoint(db, "PUT")(employee_id, {
        "expected_version": 1, "monthly_salary": 6200, "salary_effective_date": "2026-10-17",
        "salary_confirmation": routes.EMPLOYEE_SALARY_CONFIRMATION, "salary_reason": "Future salary review",
    }, OWNER)
    fenced = await read(db, "owner")
    expected = deepcopy(original)
    expected["revision"] += 1
    expected["salary_contract_revision"] = 1
    assert fenced == expected
    await refresh(db, "owner", clock=NOW)
    refreshed = await read(db, "owner")
    assert not refreshed["engine"].get("salary_conflicts")
    assert refreshed["engine"]["salary_days"] == original["engine"]["salary_days"]
    assert refreshed["engine"]["obligations"] == original["engine"]["obligations"]
    assert refreshed["movements"] == []
    assert report(refreshed)["summary"]["payable"] == "300.00"
