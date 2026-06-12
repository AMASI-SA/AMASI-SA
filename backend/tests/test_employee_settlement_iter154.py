"""Iter-154 — Unified employee settlement endpoint tests.

POST /api/liabilities/employee-settlement intelligently splits an
amount between settling accrued salary and recording excess as a
salary advance.
"""
import os
import uuid
import datetime as dt

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _mdb():
    load_dotenv("/app/backend/.env")
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
def ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"i154-{suffix}@example.com"
    pwd = "T#154abcD"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I154"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    bank = requests.post(f"{BASE_URL}/api/accounts",
                         json={"name": "Bank", "account_type": "bank",
                               "opening_balance": 100000.0},
                         headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb(), "bank_id": bank["id"]}


def _create_employee(ctx, name, monthly=3000.0,
                     accrual_mode="daily",
                     start_offset_days=400):
    start = (dt.date.today() - dt.timedelta(days=start_offset_days)).isoformat()
    r = requests.post(f"{BASE_URL}/api/operating-expenses/salaries",
                      json={"name": name, "category": "employee",
                            "monthly_amount": monthly,
                            "accrual_mode": accrual_mode,
                            "start_date": start, "status": "active"},
                      headers=ctx["hdr"], timeout=10)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _net_due(ctx, emp_id):
    r = requests.get(f"{BASE_URL}/api/liabilities/salary-accrual-summary",
                     headers=ctx["hdr"], timeout=10)
    for e in r.json()["employees"]:
        if e["id"] == emp_id:
            return float(e.get("net_due", 0))
    return 0.0


def test_pays_exactly_net_due_no_advance_created(ctx):
    emp = _create_employee(ctx, "نجيب", monthly=3000.0, start_offset_days=400)
    nd = _net_due(ctx, emp["id"])
    assert nd > 0
    r = requests.post(
        f"{BASE_URL}/api/liabilities/employee-settlement",
        json={"employee_salary_id": emp["id"],
              "amount": nd,
              "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat(),
              "notes": "تسوية كاملة"},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["salary_part"] == round(nd, 2)
    assert body["advance_part"] == 0.0
    assert body["advance_liability_id"] is None
    assert body["paid_liability_id"] is not None
    # Verify net_due is now 0
    nd_after = _net_due(ctx, emp["id"])
    assert nd_after < 0.5


def test_pays_partial_net_due_no_advance(ctx):
    emp = _create_employee(ctx, "خالد", monthly=3000.0, start_offset_days=400)
    nd = _net_due(ctx, emp["id"])
    half = round(nd / 2, 2)
    r = requests.post(
        f"{BASE_URL}/api/liabilities/employee-settlement",
        json={"employee_salary_id": emp["id"],
              "amount": half,
              "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat()},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["salary_part"] == half
    assert body["advance_part"] == 0.0


def test_overpay_splits_into_settlement_plus_advance(ctx):
    """The core feature — amount > net_due splits into payment + advance."""
    emp = _create_employee(ctx, "علي", monthly=3000.0, start_offset_days=400)
    nd = _net_due(ctx, emp["id"])
    over_amount = round(nd + 1000.0, 2)
    r = requests.post(
        f"{BASE_URL}/api/liabilities/employee-settlement",
        json={"employee_salary_id": emp["id"],
              "amount": over_amount,
              "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat(),
              "notes": "تسوية + سلفة دفعة واحدة"},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["salary_part"] == round(nd, 2)
    assert body["advance_part"] == 1000.0
    assert body["paid_liability_id"] is not None
    assert body["advance_liability_id"] is not None
    # An open advance row must exist for this employee
    advs = list(ctx["db"].liabilities.find({
        "user_id": ctx["uid"], "kind": "salary_advance",
        "employee_salary_id": emp["id"], "advance_status": "open",
    }))
    assert any(a["expected_amount"] == 1000.0 for a in advs)


def test_pure_advance_when_no_accrual(ctx):
    """If the employee has no net_due (just started), the entire amount
    is recorded as an advance.  Use a future start_date so accrual is 0."""
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    r = requests.post(f"{BASE_URL}/api/operating-expenses/salaries",
                      json={"name": "محمد المستقبل", "category": "employee",
                            "monthly_amount": 3000.0,
                            "accrual_mode": "daily",
                            "start_date": future, "status": "active"},
                      headers=ctx["hdr"], timeout=10)
    emp = r.json()
    nd = _net_due(ctx, emp["id"])
    assert nd <= 0.5, f"Future start should yield 0 accrual, got {nd}"
    r = requests.post(
        f"{BASE_URL}/api/liabilities/employee-settlement",
        json={"employee_salary_id": emp["id"],
              "amount": 500.0,
              "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat()},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["salary_part"] == 0.0
    assert body["advance_part"] == 500.0
    assert body["paid_liability_id"] is None
    assert body["advance_liability_id"] is not None


def test_rejects_insufficient_bank_balance(ctx):
    emp = _create_employee(ctx, "زيد", monthly=3000.0, start_offset_days=400)
    r = requests.post(
        f"{BASE_URL}/api/liabilities/employee-settlement",
        json={"employee_salary_id": emp["id"],
              "amount": 999999.0,
              "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat()},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 400
    assert "غير كافٍ" in r.json()["detail"]


def test_works_for_suspended_employee(ctx):
    """Iter-153 alignment: suspended employees must be settle-able."""
    emp = _create_employee(ctx, "ام جمال", monthly=3000.0, start_offset_days=400)
    requests.put(
        f"{BASE_URL}/api/operating-expenses/salaries/{emp['id']}",
        json={"status": "stopped"},
        headers=ctx["hdr"], timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/liabilities/employee-settlement",
        json={"employee_salary_id": emp["id"],
              "amount": 200.0,
              "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat(),
              "notes": "تسوية نهائية للموقوف"},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text


def test_rejects_non_employee(ctx):
    """Charity / household rows cannot be settled via this endpoint."""
    r = requests.post(f"{BASE_URL}/api/operating-expenses/salaries",
                      json={"name": "ام محمد", "category": "household",
                            "monthly_amount": 500.0,
                            "accrual_mode": "monthly",
                            "start_date": "2024-01-01", "status": "active"},
                      headers=ctx["hdr"], timeout=10)
    if r.status_code not in (200, 201):
        pytest.skip(f"household category may not be supported")
    house = r.json()
    r2 = requests.post(
        f"{BASE_URL}/api/liabilities/employee-settlement",
        json={"employee_salary_id": house["id"], "amount": 100.0,
              "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat()},
        headers=ctx["hdr"], timeout=10,
    )
    assert r2.status_code == 404
