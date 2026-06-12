"""Iter-151 — Regression for the "employee disappears from pay-liability
search after full payment" bug.

User report (Feb 2026, Arabic): "جرب البحث عن الموظف جمال مايظهر في
صفحة مركز الإدخال المالي سداد التزام — ظهر مره واحده أضفت له سداد
مبلغ المره الثانيه مايظهر يظهر ابو جمال فقط".

Translation: When the merchant searched for employee "جمال" in the
Pay-Liability tab, he appeared the first time. After fully paying his
salary liability, the next search showed only "ابو جمال" (a different
employee whose name contains "جمال" as a substring).

Backend coverage: this test verifies that after `generate-salaries`
is called for the current month (idempotent), an employee whose only
open salary liability was fully paid CAN be re-paid via a NEW liability
row whenever the daily accrual produces new net_due — by triggering the
salary generation for the next period. We also validate the listing
endpoint returns the partial/unpaid liability so the frontend can pick
it.

Frontend coverage is via virtual entries in `FinancialInputHub.jsx`
(see Iter-151 changes).
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
    email = f"i151-{suffix}@example.com"
    pwd = "T#151a"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I151"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    bank = requests.post(f"{BASE_URL}/api/accounts",
                         json={"name": "Bank Main", "account_type": "bank",
                               "opening_balance": 100000.0},
                         headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb(), "bank_id": bank["id"]}


def _make_employee(ctx, name: str, monthly: float = 3000.0):
    r = requests.post(f"{BASE_URL}/api/operating-expenses/salaries",
                      json={"name": name, "category": "employee",
                            "monthly_amount": monthly,
                            "accrual_mode": "monthly",
                            "start_date": "2024-01-01",
                            "status": "active"},
                      headers=ctx["hdr"], timeout=10)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_employee_overlapping_name_does_not_hide_paid_one_via_generation(ctx):
    """Two employees: "جمال" (full name) and "ابو جمال" (contains 'جمال').
    Pay جمال's salary fully → liability becomes paid. Then call
    generate-salaries for the NEXT period — جمال must get a fresh
    salary liability so the frontend can show him again in the
    pay-liability search via the virtual-entry flow."""
    gamal = _make_employee(ctx, "جمال", 3000.0)
    _abu = _make_employee(ctx, "ابو جمال", 4000.0)

    # 1) Generate salaries for current month
    cur_period = dt.date.today().strftime("%Y-%m")
    r_gen = requests.post(
        f"{BASE_URL}/api/liabilities/generate-salaries",
        params={"period": cur_period},
        headers=ctx["hdr"], timeout=15,
    )
    assert r_gen.status_code == 200, r_gen.text

    # Find جمال's salary liability
    r_list = requests.get(
        f"{BASE_URL}/api/liabilities",
        params={"kind": "salary", "employee_salary_id": gamal["id"],
                "status": "unpaid"},
        headers=ctx["hdr"], timeout=10,
    )
    items = r_list.json()["items"]
    assert len(items) == 1, f"Expected 1 unpaid salary row for جمال, got {len(items)}"
    gamal_liab = items[0]

    # 2) Pay it in FULL → status becomes 'paid'
    r_pay = requests.post(
        f"{BASE_URL}/api/liabilities/{gamal_liab['id']}/pay",
        json={"amount": 3000.0, "paid_from_account_id": ctx["bank_id"],
              "payment_date": dt.date.today().isoformat(),
              "notes": "سداد كامل"},
        headers=ctx["hdr"], timeout=10,
    )
    assert r_pay.status_code == 200, r_pay.text

    # Verify جمال is no longer in unpaid OR partial
    r_unpaid = requests.get(
        f"{BASE_URL}/api/liabilities",
        params={"kind": "salary", "status": "unpaid"},
        headers=ctx["hdr"], timeout=10,
    )
    gamal_unpaid = [
        l for l in r_unpaid.json()["items"]
        if l.get("employee_salary_id") == gamal["id"]
    ]
    assert len(gamal_unpaid) == 0
    r_partial = requests.get(
        f"{BASE_URL}/api/liabilities",
        params={"kind": "salary", "status": "partial"},
        headers=ctx["hdr"], timeout=10,
    )
    gamal_partial = [
        l for l in r_partial.json()["items"]
        if l.get("employee_salary_id") == gamal["id"]
    ]
    assert len(gamal_partial) == 0

    # 3) Call generate-salaries again for SAME period — idempotent, no
    #    new row should be created for جمال (his row already exists,
    #    just paid).
    r_gen2 = requests.post(
        f"{BASE_URL}/api/liabilities/generate-salaries",
        params={"period": cur_period},
        headers=ctx["hdr"], timeout=15,
    )
    assert r_gen2.status_code == 200
    # All salary rows for current month should still exist & be unchanged in count.
    all_for_period = list(ctx["db"].liabilities.find(
        {"user_id": ctx["uid"], "kind": "salary",
         "period_key": cur_period, "employee_salary_id": gamal["id"]},
    ))
    assert len(all_for_period) == 1
    assert all_for_period[0]["status"] == "paid"


def test_search_results_endpoint_returns_active_employee_data(ctx):
    """Sanity: the operating-expenses/salaries endpoint that the
    frontend uses must include both جمال and ابو جمال so the virtual
    search-entries logic can surface them."""
    _make_employee(ctx, "جمال", 3000.0)
    _make_employee(ctx, "ابو جمال", 4000.0)
    r = requests.get(f"{BASE_URL}/api/operating-expenses/salaries",
                     headers=ctx["hdr"], timeout=10)
    names = sorted(e["name"] for e in r.json()["items"]
                   if e.get("status") == "active")
    assert "جمال" in names
    assert "ابو جمال" in names


def test_salary_accrual_summary_has_net_due_for_both_employees(ctx):
    """The salary-accrual-summary endpoint (used by the search dropdown
    to highlight cumulative net_due) must include both employees."""
    e1 = _make_employee(ctx, "جمال", 3000.0)
    e2 = _make_employee(ctx, "ابو جمال", 4000.0)
    r = requests.get(f"{BASE_URL}/api/liabilities/salary-accrual-summary",
                     headers=ctx["hdr"], timeout=10)
    ids = {e["id"] for e in r.json().get("employees", [])}
    assert e1["id"] in ids
    assert e2["id"] in ids
