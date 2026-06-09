"""Iter-115 — Cross-month dynamic salary accrual + suspension + advances.

Tests GET /api/liabilities/salary-accrual-summary and GET /api/liabilities/summary
to verify the days-worked based accrual + frozen accrual on suspension +
separate reporting of advances (not netted into accrued).
"""
import os
import uuid
import calendar
import time
from datetime import date, datetime, timezone

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
).rstrip("/")


def _mdb():
    load_dotenv("/app/backend/.env")
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _today():
    return date.today()


def _days_in(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _expected_accrual(start: date, end: date, monthly: float) -> float:
    """Replicate backend per-month daily-rate accrual."""
    if start > end:
        return 0.0
    total = 0.0
    cursor = start
    while cursor <= end:
        dim = _days_in(cursor.year, cursor.month)
        month_last = date(cursor.year, cursor.month, dim)
        eff_end = min(end, month_last)
        seg_days = (eff_end - cursor).days + 1
        daily = monthly / dim if dim > 0 else 0.0
        total += daily * seg_days
        # advance to first of next month
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return round(total, 2)


@pytest.fixture
def ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"sal115-{suffix}@example.com"
    pwd = "T#aaa1"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": pwd, "name": "Sal115"},
        timeout=15,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": pwd},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.text}"
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=15).json()
    db = _mdb()
    yield {"hdr": hdr, "uid": me["id"], "db": db, "email": email}
    # Teardown
    db.operating_salaries.delete_many({"user_id": me["id"]})
    db.liabilities.delete_many({"user_id": me["id"]})


def _create_salary(hdr, name, monthly, start_date, category="employee"):
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/salaries",
        headers=hdr,
        json={
            "name": name,
            "category": category,
            "country": "saudi",
            "monthly_amount": monthly,
            "start_date": start_date,
            "status": "active",
        },
        timeout=15,
    )
    assert r.status_code in (200, 201), f"create salary failed: {r.text}"
    return r.json()


# ── Test 1: Empty tenant returns zeroes ──────────────────────────────────
def test_empty_tenant_summary(ctx):
    r = requests.get(
        f"{BASE_URL}/api/liabilities/salary-accrual-summary",
        headers=ctx["hdr"], timeout=15,
    )
    assert r.status_code == 200
    body = r.json()
    for k in ("accrued_total", "advances_total", "paid_total", "net_due",
              "active_count", "suspended_count", "employees"):
        assert k in body, f"missing key {k}"
    assert body["accrued_total"] == 0
    assert body["active_count"] == 0
    assert body["suspended_count"] == 0
    assert body["employees"] == []


# ── Test 2: Cross-month accrual math ─────────────────────────────────────
def test_cross_month_accrual(ctx):
    today = _today()
    # Start one full prior month ago so we exercise the cross-month math.
    if today.month == 1:
        prior_year, prior_month = today.year - 1, 12
    else:
        prior_year, prior_month = today.year, today.month - 1
    start_date = date(prior_year, prior_month, 1)
    monthly = 6000.0

    _create_salary(
        ctx["hdr"], "CrossMonthEmp", monthly, start_date.isoformat(),
    )

    r = requests.get(
        f"{BASE_URL}/api/liabilities/salary-accrual-summary",
        headers=ctx["hdr"], timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["employees"]) == 1
    emp = body["employees"][0]
    expected = _expected_accrual(start_date, today, monthly)
    assert abs(emp["accrued"] - expected) < 0.05, (
        f"expected {expected} got {emp['accrued']}"
    )
    # days_worked = full prior month + days_today
    expected_days = _days_in(prior_year, prior_month) + today.day
    assert emp["days_worked"] == expected_days
    assert body["active_count"] == 1
    assert body["suspended_count"] == 0
    # accrued_total = sum
    assert abs(body["accrued_total"] - expected) < 0.05


# ── Test 3: Suspended employee FREEZES accrual ───────────────────────────
def test_suspension_freezes_accrual(ctx):
    today = _today()
    # Start ~10 days ago.
    if today.day > 10:
        start = date(today.year, today.month, today.day - 10)
    else:
        # safe fallback: 1st of this month
        start = date(today.year, today.month, 1)
    monthly = 3000.0
    sal = _create_salary(
        ctx["hdr"], "SuspendMe", monthly, start.isoformat(),
    )
    sid = sal["id"]

    # Capture initial accrued
    r1 = requests.get(
        f"{BASE_URL}/api/liabilities/salary-accrual-summary",
        headers=ctx["hdr"], timeout=15,
    )
    a1 = r1.json()
    accrued_before = next(
        e["accrued"] for e in a1["employees"] if e["id"] == sid
    )
    assert a1["active_count"] == 1
    assert a1["suspended_count"] == 0

    # Flip to stopped
    r2 = requests.put(
        f"{BASE_URL}/api/operating-expenses/salaries/{sid}",
        headers=ctx["hdr"], json={"status": "stopped"}, timeout=15,
    )
    assert r2.status_code == 200, r2.text
    # Verify stopped_at set to today
    stopped_doc = ctx["db"].operating_salaries.find_one({"id": sid})
    assert stopped_doc.get("stopped_at"), "stopped_at not stamped"
    assert stopped_doc["stopped_at"].startswith(today.isoformat()[:10])

    # Sleep briefly then re-fetch — accrued must not exceed frozen value
    time.sleep(1.5)
    r3 = requests.get(
        f"{BASE_URL}/api/liabilities/salary-accrual-summary",
        headers=ctx["hdr"], timeout=15,
    )
    a2 = r3.json()
    emp = next(e for e in a2["employees"] if e["id"] == sid)
    # Frozen — should equal accrued_before (give 1-day tolerance because
    # the test could roll across midnight).
    assert emp["accrued"] <= accrued_before + 0.05, (
        f"accrual did not freeze: before={accrued_before} after={emp['accrued']}"
    )
    assert emp["status"] == "stopped"
    assert a2["active_count"] == 0
    assert a2["suspended_count"] == 1


# ── Test 4: Salary advance shows up separately, NOT subtracted from accrued
def test_advance_separate(ctx):
    today = _today()
    start = date(today.year, today.month, 1)
    monthly = 4000.0
    sal = _create_salary(
        ctx["hdr"], "AdvanceEmp", monthly, start.isoformat(),
    )
    sid = sal["id"]

    # Insert a salary_advance liability directly.
    adv_amount = 500.0
    ctx["db"].liabilities.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": ctx["uid"],
        "kind": "salary_advance",
        "employee_salary_id": sid,
        "expected_amount": adv_amount,
        "consumed_amount": 0.0,
        "paid_amount": 0.0,
        "advance_status": "open",
        "status": "unpaid",
        "description": "Test advance",
        "due_date": today.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    r = requests.get(
        f"{BASE_URL}/api/liabilities/salary-accrual-summary",
        headers=ctx["hdr"], timeout=15,
    )
    body = r.json()
    emp = next(e for e in body["employees"] if e["id"] == sid)

    raw_accrued = _expected_accrual(start, today, monthly)
    assert abs(emp["accrued"] - raw_accrued) < 0.05, (
        f"accrued was reduced by advance: got {emp['accrued']} want {raw_accrued}"
    )
    assert body["advances_total"] >= adv_amount - 0.01
    assert emp["outstanding_advance"] >= adv_amount - 0.01
    # net_due = accrued - paid (advances NOT subtracted)
    assert abs(body["net_due"] - (body["accrued_total"] - body["paid_total"])) < 0.05


# ── Test 5: Household-category row is ignored ─────────────────────────────
def test_household_category_excluded(ctx):
    today = _today()
    start = date(today.year, today.month, 1)
    _create_salary(
        ctx["hdr"], "MaidHousehold", 1500.0, start.isoformat(),
        category="household",
    )
    _create_salary(
        ctx["hdr"], "RealEmp", 2000.0, start.isoformat(),
        category="employee",
    )
    r = requests.get(
        f"{BASE_URL}/api/liabilities/salary-accrual-summary",
        headers=ctx["hdr"], timeout=15,
    )
    body = r.json()
    assert len(body["employees"]) == 1
    assert body["employees"][0]["name"] == "RealEmp"
    assert body["active_count"] == 1


# ── Test 6: /summary surfaces salary_breakdown + matches net_due ─────────
def test_summary_includes_salary_breakdown(ctx):
    today = _today()
    start = date(today.year, today.month, 1)
    _create_salary(ctx["hdr"], "SumEmp", 3000.0, start.isoformat())

    s_acc = requests.get(
        f"{BASE_URL}/api/liabilities/salary-accrual-summary",
        headers=ctx["hdr"], timeout=15,
    ).json()
    s_sum = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["hdr"], timeout=15,
    ).json()

    assert "salary_breakdown" in s_sum
    sb = s_sum["salary_breakdown"]
    for k in ("accrued_total", "advances_total", "paid_total",
              "net_due", "active_count", "suspended_count", "employees"):
        assert k in sb, f"salary_breakdown missing {k}"

    assert abs(sb["net_due"] - s_acc["net_due"]) < 0.05
    assert abs(sb["accrued_total"] - s_acc["accrued_total"]) < 0.05
    # liabilities.salaries_unpaid == salary_breakdown.net_due
    assert abs(
        s_sum["liabilities"]["salaries_unpaid"] - sb["net_due"]
    ) < 0.05
    # net_position math
    assert abs(
        s_sum["net_position"]
        - (s_sum["assets"]["total"] - s_sum["liabilities"]["total"])
    ) < 0.05
