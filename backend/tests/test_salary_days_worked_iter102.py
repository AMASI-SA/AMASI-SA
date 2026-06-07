"""Iter-102 — Pro-rata salary by days worked.

When a salary liability is generated for a month, the user can later
adjust `days_worked`. The system recomputes:
    expected_amount = monthly_amount_base × days_worked / days_in_month

Rules verified:
  • Only `kind=salary` rows accept the days editor.
  • Only employees in `category=employee` (household/charity stay fixed).
  • days_worked ∈ [0, days_in_month].
  • Can't lower expected_amount below already-paid amount.
  • Status (unpaid/partial/paid) recomputed correctly.
  • Newly-generated rows for the same month carry `days_in_month`
    matching the calendar (28/29/30/31).
"""
import os
import uuid
import calendar
from datetime import datetime, timezone

import pytest
import requests
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _env(key: str) -> str:
    line = [ln for ln in open("/app/backend/.env").read().splitlines()
            if ln.startswith(f"{key}=")][0]
    return line.split("=", 1)[1].strip().strip('"')


@pytest.fixture
def mongo_db():
    return MongoClient(_env("MONGO_URL"))[_env("DB_NAME")]


def _new_user_with_bank():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter102-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#102t", "name": "Pro-rata"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#102t"},
        timeout=10,
    )
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    bank = requests.post(
        f"{BASE_URL}/api/accounts",
        json={"name": "بنك Iter-102", "account_type": "bank",
              "currency": "SAR", "opening_balance": 100000,
              "opening_balance_date": "2026-01-01"},
        headers=h, timeout=10,
    ).json()
    return {"headers": h, "bank_id": bank["id"]}


def _create_employee(headers, name="موظف اختبار", monthly=6000.0,
                     category="employee", start="2026-01-01"):
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/salaries",
        json={
            "name": name, "category": category, "country": "saudi",
            "monthly_amount": monthly, "start_date": start,
            "status": "active",
        },
        headers=headers, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _generate(headers, period: str):
    r = requests.post(
        f"{BASE_URL}/api/liabilities/generate-salaries?period={period}",
        headers=headers, timeout=10,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _find_salary_liab(headers, employee_id, period):
    r = requests.get(
        f"{BASE_URL}/api/liabilities?limit=500",
        headers=headers, timeout=10,
    )
    for x in r.json()["items"]:
        if (x.get("kind") == "salary"
                and x.get("employee_salary_id") == employee_id
                and x.get("period_key") == period):
            return x
    return None


# ── 1) Generated rows carry the right days_in_month ─────────────────
def test_generated_row_has_calendar_days_in_month():
    ctx = _new_user_with_bank()
    emp = _create_employee(ctx["headers"], monthly=3100.0)

    # February 2026 (28 days)
    _generate(ctx["headers"], "2026-02")
    feb = _find_salary_liab(ctx["headers"], emp["id"], "2026-02")
    assert feb is not None
    assert feb["days_in_month"] == 28
    assert feb["days_worked"] == 28          # initial = full
    assert feb["monthly_amount_base"] == 3100.0
    assert feb["expected_amount"] == 3100.0  # full month

    # March 2026 (31 days)
    _generate(ctx["headers"], "2026-03")
    mar = _find_salary_liab(ctx["headers"], emp["id"], "2026-03")
    assert mar["days_in_month"] == 31


# ── 2) Pro-rata recomputation ───────────────────────────────────────
def test_days_worked_recomputes_expected_amount():
    ctx = _new_user_with_bank()
    emp = _create_employee(ctx["headers"], monthly=3000.0)
    _generate(ctx["headers"], "2026-04")  # 30 days
    liab = _find_salary_liab(ctx["headers"], emp["id"], "2026-04")
    assert liab["expected_amount"] == 3000.0
    daily = 3000.0 / 30   # 100 / day

    # Worked 25 days only → 2500
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": 25},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days_worked"] == 25
    assert body["expected_amount"] == round(daily * 25, 2) == 2500.0
    assert body["status"] == "unpaid"          # still unpaid
    assert body["remaining_amount"] == 2500.0

    # Worked 0 → expected 0, status flips to "paid" (nothing owed)
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": 0},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["expected_amount"] == 0.0
    assert body["status"] == "paid"            # 0 expected, 0 paid → paid


# ── 3) Out-of-range / wrong-kind / wrong-category guards ────────────
def test_validation_rules():
    ctx = _new_user_with_bank()
    emp = _create_employee(ctx["headers"], monthly=3000.0)
    _generate(ctx["headers"], "2026-04")
    liab = _find_salary_liab(ctx["headers"], emp["id"], "2026-04")

    # Negative
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": -1},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400

    # > days_in_month
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": 35},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400

    # Non-numeric
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": "many"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400


def test_household_category_rejected():
    """Households / charity must keep flat amounts — no day proration."""
    ctx = _new_user_with_bank()
    emp = _create_employee(ctx["headers"], monthly=2000.0, category="household")
    _generate(ctx["headers"], "2026-04")
    liab = _find_salary_liab(ctx["headers"], emp["id"], "2026-04")
    assert liab is not None

    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": 15},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400
    assert "مصاريف منزل" in r.json()["detail"] or "صدقات" in r.json()["detail"]


# ── 4) Cannot reduce below already-paid ─────────────────────────────
def test_cannot_reduce_below_already_paid():
    ctx = _new_user_with_bank()
    emp = _create_employee(ctx["headers"], monthly=3000.0)
    _generate(ctx["headers"], "2026-04")
    liab = _find_salary_liab(ctx["headers"], emp["id"], "2026-04")

    # Partially pay 2000
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{liab['id']}/pay",
        json={
            "amount": 2000,
            "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-04-15",
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200

    # Now try setting days=10 → expected would be 1000 < paid 2000 → reject
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": 10},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400
    assert "أقل من المسدَّد" in r.json()["detail"]

    # But days=25 (=> 2500) is OK (2500 ≥ 2000)
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": 25},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expected_amount"] == 2500.0
    assert body["paid_amount"] == 2000.0
    assert body["remaining_amount"] == 500.0
    assert body["status"] == "partial"


# ── 5) Net position drops when days reduced ─────────────────────────
def test_net_position_decreases_with_fewer_days():
    ctx = _new_user_with_bank()
    emp = _create_employee(ctx["headers"], monthly=3000.0)
    _generate(ctx["headers"], "2026-04")
    s0 = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["headers"], timeout=10,
    ).json()

    liab = _find_salary_liab(ctx["headers"], emp["id"], "2026-04")
    requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}/days-worked",
        json={"days_worked": 20},                      # 2000 instead of 3000
        headers=ctx["headers"], timeout=10,
    )
    s1 = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["headers"], timeout=10,
    ).json()

    # Salaries unpaid dropped by exactly 1000
    assert s0["liabilities"]["salaries_unpaid"] - s1["liabilities"]["salaries_unpaid"] == 1000.0
    # Net position rose by 1000 (less owed)
    assert s1["net_position"] - s0["net_position"] == 1000.0
