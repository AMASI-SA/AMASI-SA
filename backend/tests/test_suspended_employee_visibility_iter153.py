"""Iter-153 — Suspended employees must remain selectable for
liability/advance/payment workflows.

User feedback (Feb 2026): "عندما يكون الموظف موقف لا استطيع البحث عنه
واضافة مدوينينه او سداد اللتزامم له وهذا غلط. ابغى يكون مسموح مع
التنبيه".

These tests verify the BACKEND already returns suspended employees
in the relevant endpoints (so the frontend's filter loosening is the
only change needed). Visual "موقوف" warning badges are tested via
data-testid markers in the frontend (verified by screenshot earlier).
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
    email = f"i153-{suffix}@example.com"
    pwd = "T#153abcD"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I153"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    bank = requests.post(f"{BASE_URL}/api/accounts",
                         json={"name": "Bank", "account_type": "bank",
                               "opening_balance": 50000.0},
                         headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb(), "bank_id": bank["id"]}


def _create_employee(ctx, name, status="active"):
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/salaries",
        json={"name": name, "category": "employee", "monthly_amount": 3000.0,
              "accrual_mode": "monthly",
              "start_date": "2024-01-01", "status": status},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_salaries_endpoint_returns_both_active_and_suspended(ctx):
    """The /operating-expenses/salaries endpoint must surface all
    employees regardless of status. The frontend filter is responsible
    for visual scoping."""
    a = _create_employee(ctx, "موظف نشط", status="active")
    s = _create_employee(ctx, "موظف موقوف", status="stopped")
    r = requests.get(f"{BASE_URL}/api/operating-expenses/salaries",
                     headers=ctx["hdr"], timeout=10)
    items = r.json()["items"]
    names = {e["name"]: e for e in items}
    assert "موظف نشط" in names
    assert "موظف موقوف" in names
    assert names["موظف نشط"]["status"] == "active"
    assert names["موظف موقوف"]["status"] == "stopped"


def test_salary_accrual_summary_includes_suspended_employee(ctx):
    """The salary-accrual-summary must list suspended employees so the
    UI's accrualMap can render their open-balance hints + warning
    badges. Their accrued is still tracked through their suspension
    date."""
    _create_employee(ctx, "نشط", status="active")
    suspended = _create_employee(ctx, "موقوف للتسوية", status="stopped")
    r = requests.get(f"{BASE_URL}/api/liabilities/salary-accrual-summary",
                     headers=ctx["hdr"], timeout=10)
    body = r.json()
    suspended_ids = {e["id"] for e in body["employees"]
                     if e.get("status") != "active"}
    assert suspended["id"] in suspended_ids


def test_can_pay_existing_liability_for_suspended_employee(ctx):
    """When a salary liability already exists for a suspended employee
    (e.g. a final-settlement obligation), payment must succeed."""
    emp = _create_employee(ctx, "للسحب", status="active")
    # Generate current month liability while active
    requests.post(f"{BASE_URL}/api/liabilities/generate-salaries",
                  headers=ctx["hdr"], timeout=15)
    # Suspend the employee
    requests.put(
        f"{BASE_URL}/api/operating-expenses/salaries/{emp['id']}",
        json={"status": "stopped"},
        headers=ctx["hdr"], timeout=10,
    )
    # Find the open salary liability for this employee
    r_l = requests.get(
        f"{BASE_URL}/api/liabilities",
        params={"kind": "salary", "employee_salary_id": emp["id"],
                "status": "unpaid"},
        headers=ctx["hdr"], timeout=10,
    )
    items = r_l.json()["items"]
    assert len(items) >= 1
    liab = items[0]
    # Pay the liability
    r_pay = requests.post(
        f"{BASE_URL}/api/liabilities/{liab['id']}/pay",
        json={"amount": liab["expected_amount"],
              "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat()},
        headers=ctx["hdr"], timeout=10,
    )
    assert r_pay.status_code == 200, r_pay.text


def test_can_create_salary_advance_for_suspended_employee(ctx):
    """Salary advances must be allowed for suspended employees so the
    merchant can record a final-cash payment that will offset future
    settlements."""
    emp = _create_employee(ctx, "بحاجة سلفة بعد التوقف", status="active")
    # Suspend
    requests.put(
        f"{BASE_URL}/api/operating-expenses/salaries/{emp['id']}",
        json={"status": "stopped"},
        headers=ctx["hdr"], timeout=10,
    )
    # Create the advance via /api/liabilities (kind=salary_advance)
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={"kind": "salary_advance",
              "employee_salary_id": emp["id"],
              "expected_amount": 500.0,
              "paid_from_account_id": ctx["bank_id"],
              "due_date": dt.date.today().isoformat(),
              "description": "تسوية نهائية للموظف الموقوف"},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "salary_advance"
    assert body["employee_salary_id"] == emp["id"]
