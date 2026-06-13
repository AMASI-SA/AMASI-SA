"""Iter-162 — Migration legacy balance fidelity.

Ensures `migration_routes._legacy_employee_balances` and
`_legacy_supplier_balances` exactly mirror the figures shown by the
legacy `/api/liabilities/summary` / `salary-accrual-summary` endpoints.

Production discrepancy reproduced (Feb 2026):
  • Employee salary off by 5,800 → caused by ignoring dynamic accrual.
  • Open advance off by 2,895     → caused by using paid_amount instead
    of (expected_amount − consumed_amount).
  • Supplier off by 1,239         → caused by missing the supplier_name
    fallback linkage and the `is_pre_accounting != True` filter.
"""
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from migration_routes import (  # noqa: E402
    _legacy_employee_balances,
    _legacy_supplier_balances,
    _legacy_external_balances,
)
from liabilities_routes import _aggregate_salary_accrual  # noqa: E402


def _today():
    return datetime.now(timezone.utc).date()


def _iso(d):
    return d.isoformat()


@pytest.mark.asyncio
async def test_dynamic_salary_accrual_matches_legacy_summary():
    """Migration's salary_payable must equal max(0, accrued − cash_paid),
    matching the per-employee `net_due` in `_aggregate_salary_accrual`.
    """
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        # Employee started 60 days ago, monthly_amount 3000 → ~ 2 months
        # ≈ accrued ~ 6000 (depends on month boundaries, but the legacy
        # aggregator is the source of truth).
        emp_id = str(uuid.uuid4())
        start = _today() - timedelta(days=60)
        await db.operating_salaries.insert_one({
            "id": emp_id, "user_id": uid, "name": "موظف اختبار",
            "category": "employee", "status": "active",
            "monthly_amount": 3000, "start_date": _iso(start),
        })
        # No salary payments yet → cash_paid = 0

        # Open advance: expected 1000, consumed 100 → remaining = 900
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "kind": "salary_advance", "employee_salary_id": emp_id,
            "expected_amount": 1000, "paid_amount": 1000,
            "consumed_amount": 100,
            "status": "paid", "advance_status": "open",
        })

        # Compare against legacy aggregator
        legacy_summary = await _aggregate_salary_accrual(db, uid)
        emp_legacy = next(
            e for e in legacy_summary["employees"] if e["id"] == emp_id)

        # Run migration's snapshot
        mig_rows = await _legacy_employee_balances(db, uid)
        mig_emp = next(r for r in mig_rows if r["employee_id"] == emp_id)

        # 1) salary_payable must equal net_due
        assert mig_emp["salary_payable"] == emp_legacy["net_due"], (
            f"salary_payable mismatch: migration={mig_emp['salary_payable']} "
            f"vs legacy net_due={emp_legacy['net_due']}")

        # 2) advance must equal (expected − consumed) = 900 (not 1000)
        assert mig_emp["advance"] == 900.0, (
            f"advance must be 900 (1000-100), got {mig_emp['advance']}")

        # 3) Migration accrued matches legacy aggregator
        assert mig_emp["_accrued"] == emp_legacy["accrued"]
        assert mig_emp["_days_worked"] == emp_legacy["days_worked"]

    finally:
        await db.operating_salaries.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_salary_payable_after_partial_payment():
    """If the employee accrued 6000 and the merchant paid 2000 cash,
    salary_payable must equal 4000 — matching the legacy net_due.
    """
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        emp_id = str(uuid.uuid4())
        start = _today() - timedelta(days=60)
        await db.operating_salaries.insert_one({
            "id": emp_id, "user_id": uid, "name": "موظف",
            "category": "employee", "status": "active",
            "monthly_amount": 3000, "start_date": _iso(start),
        })
        # Salary liability with cash paid of 2000
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "kind": "salary", "employee_salary_id": emp_id,
            "expected_amount": 3000, "paid_amount": 2000,
            "advance_deducted": 0, "status": "partial",
        })

        legacy_summary = await _aggregate_salary_accrual(db, uid)
        emp_legacy = next(
            e for e in legacy_summary["employees"] if e["id"] == emp_id)
        mig_emp = next(
            r for r in await _legacy_employee_balances(db, uid)
            if r["employee_id"] == emp_id)

        assert mig_emp["salary_payable"] == emp_legacy["net_due"]
        assert mig_emp["_cash_paid"] == 2000.0
    finally:
        await db.operating_salaries.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_supplier_balance_with_supplier_name_fallback():
    """Supplier liabilities may link via `supplier_name` only (older rows).
    Migration must match these via the counterparty's name.
    """
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        sup_id = str(uuid.uuid4())
        await db.counterparties.insert_one({
            "id": sup_id, "user_id": uid, "kind": "supplier",
            "name": "شركة العنزي", "name_lower": "شركة العنزي",
        })
        # Row 1: linked by counterparty_id, remaining = 500
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "kind": "supplier",
            "counterparty_id": sup_id, "supplier_name": "شركة العنزي",
            "expected_amount": 500, "paid_amount": 0,
            "status": "unpaid",
        })
        # Row 2: legacy row linked ONLY by supplier_name, remaining = 739
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "kind": "supplier",
            "counterparty_id": None, "supplier_name": "شركة العنزي",
            "expected_amount": 1000, "paid_amount": 261,
            "status": "partial",
        })
        # Row 3: paid in full → must be EXCLUDED
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "kind": "supplier",
            "counterparty_id": sup_id,
            "expected_amount": 200, "paid_amount": 200,
            "status": "paid",
        })
        # Row 4: pre-accounting historical → must be EXCLUDED
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "kind": "supplier",
            "counterparty_id": sup_id,
            "expected_amount": 9999, "paid_amount": 0,
            "status": "unpaid", "is_pre_accounting": True,
        })

        rows = await _legacy_supplier_balances(db, uid)
        sup = next(r for r in rows if r["supplier_id"] == sup_id)
        # 500 (id linked) + 739 (name linked) = 1239 — matches the
        # exact discrepancy the merchant reported on production.
        assert sup["payable"] == 1239.0, (
            f"Expected 1239.0 (500+739), got {sup['payable']}")
    finally:
        await db.counterparties.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_advance_uses_consumed_not_paid():
    """The classic bug: advance was computed from paid_amount instead of
    (expected_amount − consumed_amount). Verify the fix.
    """
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        emp_id = str(uuid.uuid4())
        await db.operating_salaries.insert_one({
            "id": emp_id, "user_id": uid, "name": "شهاب",
            "category": "employee", "status": "active",
            "monthly_amount": 4000,
        })
        # 3 advances: 2 open, 1 closed
        await db.liabilities.insert_many([
            # Open, full 1000 remaining
            {"id": str(uuid.uuid4()), "user_id": uid,
             "kind": "salary_advance", "employee_salary_id": emp_id,
             "expected_amount": 1000, "paid_amount": 1000,
             "consumed_amount": 0,
             "status": "paid", "advance_status": "open"},
            # Open, 2000 − 105 consumed = 1895 remaining
            {"id": str(uuid.uuid4()), "user_id": uid,
             "kind": "salary_advance", "employee_salary_id": emp_id,
             "expected_amount": 2000, "paid_amount": 2000,
             "consumed_amount": 105,
             "status": "paid", "advance_status": "open"},
            # Closed: must be excluded
            {"id": str(uuid.uuid4()), "user_id": uid,
             "kind": "salary_advance", "employee_salary_id": emp_id,
             "expected_amount": 500, "paid_amount": 500,
             "consumed_amount": 500,
             "status": "paid", "advance_status": "consumed"},
        ])

        # 1000 + 1895 = 2895 — matches the discrepancy the user reported.
        rows = await _legacy_employee_balances(db, uid)
        emp = next(r for r in rows if r["employee_id"] == emp_id)
        assert emp["advance"] == 2895.0, (
            f"Expected 2895 (1000+1895), got {emp['advance']}")
    finally:
        await db.operating_salaries.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})
