"""Iter-92 Phase 1 — Liabilities Center.

Covers:
  - status computation helpers (unit)
  - apply_open_advances_to_salary helper (DB)
  - end-to-end flow against the running backend via REACT_APP_BACKEND_URL:
      * create ad bill → partial pay → full pay → bank balance verified
      * pay cannot exceed remaining
      * generate-salaries is idempotent
      * advance auto-deducts from next salary
      * summary returns assets − liabilities = net_position
      * delete restricted, update restricted by paid_amount
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import requests

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient

from liabilities_routes import (
    _compute_status,
    _enrich,
    _last_day_of_month,
    _apply_open_advances_to_salary,
)


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _mongo_url() -> str:
    return (
        open("/app/backend/.env").read()
        .split("MONGO_URL=")[1].split("\n")[0].strip('"')
    )


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(_mongo_url())
    yield client["test_database"]


# ── Unit tests ────────────────────────────────────────────────────────────
def test_compute_status_unpaid():
    assert _compute_status(1000, 0) == "unpaid"


def test_compute_status_partial():
    assert _compute_status(1000, 400) == "partial"


def test_compute_status_paid_exact():
    assert _compute_status(1000, 1000) == "paid"


def test_compute_status_paid_with_rounding():
    assert _compute_status(999.99, 1000) == "paid"


def test_compute_status_paid_overpaid():
    assert _compute_status(1000, 1001) == "paid"


def test_enrich_adds_remaining_and_overdue():
    yesterday = "1999-01-01"
    row = {
        "expected_amount": 1000, "paid_amount": 300,
        "status": "partial", "due_date": yesterday,
    }
    out = _enrich(row)
    assert out["remaining_amount"] == 700
    assert out["is_overdue"] is True


def test_enrich_paid_is_never_overdue():
    yesterday = "1999-01-01"
    row = {
        "expected_amount": 1000, "paid_amount": 1000,
        "status": "paid", "due_date": yesterday,
    }
    out = _enrich(row)
    assert out["is_overdue"] is False


def test_last_day_of_month():
    assert _last_day_of_month(2026, 2) == "2026-02-28"
    assert _last_day_of_month(2024, 2) == "2024-02-29"
    assert _last_day_of_month(2026, 12) == "2026-12-31"


# ── DB-level advance logic ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_apply_open_advances_reduces_salary(db):
    uid = f"iter92-adv-{uuid.uuid4().hex[:6]}"
    emp_id = "emp-1"
    await db.liabilities.insert_one({
        "id": "adv-1", "user_id": uid, "kind": "salary_advance",
        "employee_salary_id": emp_id, "expected_amount": 500,
        "paid_amount": 500, "consumed_amount": 0,
        "advance_status": "open", "status": "paid",
        "due_date": "2026-02-01", "created_at": "2026-02-01T00:00:00Z",
    })
    await db.liabilities.insert_one({
        "id": "adv-2", "user_id": uid, "kind": "salary_advance",
        "employee_salary_id": emp_id, "expected_amount": 200,
        "paid_amount": 200, "consumed_amount": 0,
        "advance_status": "open", "status": "paid",
        "due_date": "2026-02-05", "created_at": "2026-02-05T00:00:00Z",
    })

    salary = {
        "id": "sal-1", "user_id": uid, "kind": "salary",
        "employee_salary_id": emp_id, "period_key": "2026-02",
        "expected_amount": 5000, "paid_amount": 0,
        "advance_deducted": 0, "status": "unpaid",
        "due_date": "2026-02-28",
    }
    await db.liabilities.insert_one({**salary})

    updated = await _apply_open_advances_to_salary(db, uid, emp_id, salary)
    assert updated["paid_amount"] == 700
    assert updated["status"] == "partial"
    assert updated["advance_deducted"] == 700

    adv1 = await db.liabilities.find_one(
        {"id": "adv-1", "user_id": uid}, {"_id": 0}
    )
    assert adv1["advance_status"] == "fully_consumed"
    assert adv1["consumed_amount"] == 500
    await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_apply_advance_partial_when_advance_exceeds_salary(db):
    uid = f"iter92-adv2-{uuid.uuid4().hex[:6]}"
    emp_id = "emp-2"
    await db.liabilities.insert_one({
        "id": "adv-big", "user_id": uid, "kind": "salary_advance",
        "employee_salary_id": emp_id, "expected_amount": 8000,
        "paid_amount": 8000, "consumed_amount": 0,
        "advance_status": "open", "status": "paid",
        "due_date": "2026-02-01", "created_at": "2026-02-01T00:00:00Z",
    })
    salary = {
        "id": "sal-small", "user_id": uid, "kind": "salary",
        "employee_salary_id": emp_id, "period_key": "2026-02",
        "expected_amount": 5000, "paid_amount": 0,
        "advance_deducted": 0, "status": "unpaid",
    }
    await db.liabilities.insert_one({**salary})

    updated = await _apply_open_advances_to_salary(db, uid, emp_id, salary)
    assert updated["paid_amount"] == 5000
    assert updated["status"] == "paid"
    adv = await db.liabilities.find_one(
        {"id": "adv-big", "user_id": uid}, {"_id": 0}
    )
    assert adv["consumed_amount"] == 5000
    assert adv["advance_status"] == "open"
    await db.liabilities.delete_many({"user_id": uid})


# ── HTTP E2E against live backend ────────────────────────────────────
def _make_user_and_token(db_sync_helper=None):
    """Create an isolated test user via the running backend's auth API."""
    suffix = uuid.uuid4().hex[:8]
    email = f"liab92-{suffix}@example.com"
    password = "Test#92Liab"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password, "name": "Liab Test"},
        timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    token = body.get("access_token") or body.get("token")
    return email, token


def _new_user_with_bank():
    """Create a fresh user + a bank account with 50000 SAR opening balance."""
    email, token = _make_user_and_token()
    h = {"Authorization": f"Bearer {token}"}

    r = requests.post(
        f"{BASE_URL}/api/accounts",
        json={
            "name": "بنك اختبار", "account_type": "bank",
            "currency": "SAR", "opening_balance": 50000.0,
            "opening_balance_date": "2026-01-01",
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    bank_id = r.json()["id"]
    return {"email": email, "token": token, "headers": h, "bank_id": bank_id}


def _bank_balance(headers, bank_id):
    r = requests.get(
        f"{BASE_URL}/api/accounts/{bank_id}", headers=headers, timeout=10
    )
    assert r.status_code == 200, r.text
    return r.json().get("current_balance")


def test_e2e_create_ad_bill_pay_partial_then_full():
    ctx = _new_user_with_bank()
    h = ctx["headers"]

    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "ad_account", "ad_provider": "snapchat",
            "ad_account_label": "حساب التايجر سناب",
            "expected_amount": 1200, "due_date": "2026-02-28",
            "description": "فاتورة سناب فبراير",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    liab = r.json()
    assert liab["kind"] == "ad_account"
    assert liab["ad_provider"] == "snapchat"
    assert liab["status"] == "unpaid"
    assert liab["remaining_amount"] == 1200
    liab_id = liab["id"]

    # Partial 400
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{liab_id}/pay",
        json={
            "amount": 400, "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-02-15",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["liability"]["paid_amount"] == 400
    assert body["liability"]["status"] == "partial"
    assert body["liability"]["remaining_amount"] == 800
    assert _bank_balance(h, ctx["bank_id"]) == 49600.0

    # Final 800 → paid
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{liab_id}/pay",
        json={
            "amount": 800, "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-02-25",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["liability"]["status"] == "paid"
    assert body["liability"]["remaining_amount"] == 0
    assert _bank_balance(h, ctx["bank_id"]) == 48800.0


def test_e2e_pay_cannot_exceed_remaining():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "ad_account", "ad_provider": "meta",
            "expected_amount": 500, "due_date": "2026-03-15",
        },
        headers=h, timeout=10,
    )
    liab_id = r.json()["id"]

    r = requests.post(
        f"{BASE_URL}/api/liabilities/{liab_id}/pay",
        json={
            "amount": 600, "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-03-01",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 400
    assert "exceeds remaining" in r.text


def test_e2e_summary_reflects_assets_minus_liabilities():
    ctx = _new_user_with_bank()
    h = ctx["headers"]

    for prov, amt in [("meta", 2000), ("tiktok", 1500)]:
        requests.post(
            f"{BASE_URL}/api/liabilities",
            json={
                "kind": "ad_account", "ad_provider": prov,
                "expected_amount": amt, "due_date": "2026-03-30",
            },
            headers=h, timeout=10,
        )

    r = requests.get(f"{BASE_URL}/api/liabilities/summary", headers=h, timeout=10)
    assert r.status_code == 200
    s = r.json()
    assert s["assets"]["banks"] == 50000.0
    assert s["liabilities"]["ad_accounts_unpaid"] == 3500.0
    assert s["liabilities"]["by_ad_provider"]["meta"] == 2000.0
    assert s["liabilities"]["by_ad_provider"]["tiktok"] == 1500.0
    assert s["net_position"] == 46500.0


def test_e2e_generate_salaries_idempotent():
    ctx = _new_user_with_bank()
    h = ctx["headers"]

    # Seed an active salary record via the existing operating-expenses API
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/salaries",
        json={
            "name": "أحمد المختبَر", "category": "employee",
            "country": "saudi", "monthly_amount": 4500,
            "start_date": "2026-01-01", "status": "active",
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text

    r1 = requests.post(
        f"{BASE_URL}/api/liabilities/generate-salaries?period=2026-02",
        headers=h, timeout=10,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["created"] == 1

    r2 = requests.post(
        f"{BASE_URL}/api/liabilities/generate-salaries?period=2026-02",
        headers=h, timeout=10,
    )
    assert r2.json()["created"] == 0
    assert r2.json()["skipped"] == 1

    r = requests.get(
        f"{BASE_URL}/api/liabilities?kind=salary&period_key=2026-02",
        headers=h, timeout=10,
    )
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["expected_amount"] == 4500
    assert items[0]["due_date"] == "2026-02-28"


def test_e2e_advance_then_salary_auto_deducts():
    ctx = _new_user_with_bank()
    h = ctx["headers"]

    # Seed employee salary 5000/month
    r = requests.post(
        f"{BASE_URL}/api/operating-expenses/salaries",
        json={
            "name": "موظف اختبار", "category": "employee",
            "country": "saudi", "monthly_amount": 5000,
            "start_date": "2026-01-01", "status": "active",
        },
        headers=h, timeout=10,
    )
    sal_def = r.json()
    sal_def_id = sal_def["id"]

    # Grant 700 advance
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "salary_advance",
            "employee_salary_id": sal_def_id,
            "expected_amount": 700,
            "due_date": "2026-02-05",
            "paid_from_account_id": ctx["bank_id"],
            "description": "سلفة فبراير",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid"
    assert _bank_balance(h, ctx["bank_id"]) == 49300.0

    # Generate salary → should auto-apply 700
    r = requests.post(
        f"{BASE_URL}/api/liabilities/generate-salaries?period=2026-02",
        headers=h, timeout=10,
    )
    assert r.status_code == 200

    items = requests.get(
        f"{BASE_URL}/api/liabilities?kind=salary&period_key=2026-02",
        headers=h, timeout=10,
    ).json()["items"]
    assert len(items) == 1
    salary = items[0]
    assert salary["expected_amount"] == 5000
    assert salary["paid_amount"] == 700
    assert salary["advance_deducted"] == 700
    assert salary["status"] == "partial"

    # Pay remaining 4300
    r = requests.post(
        f"{BASE_URL}/api/liabilities/{salary['id']}/pay",
        json={
            "amount": 4300, "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-02-28",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["liability"]["status"] == "paid"
    assert _bank_balance(h, ctx["bank_id"]) == 45000.0


def test_e2e_delete_unpaid_only():
    ctx = _new_user_with_bank()
    h = ctx["headers"]

    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "ad_account", "ad_provider": "snapchat",
            "expected_amount": 100, "due_date": "2026-03-01",
        },
        headers=h, timeout=10,
    )
    liab_id = r.json()["id"]
    r = requests.delete(
        f"{BASE_URL}/api/liabilities/{liab_id}", headers=h, timeout=10
    )
    assert r.status_code == 200

    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "ad_account", "ad_provider": "snapchat",
            "expected_amount": 100, "due_date": "2026-03-01",
        },
        headers=h, timeout=10,
    )
    liab_id = r.json()["id"]
    requests.post(
        f"{BASE_URL}/api/liabilities/{liab_id}/pay",
        json={
            "amount": 50, "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-02-01",
        },
        headers=h, timeout=10,
    )
    r = requests.delete(
        f"{BASE_URL}/api/liabilities/{liab_id}", headers=h, timeout=10
    )
    assert r.status_code == 400


def test_e2e_update_liability():
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "ad_account", "ad_provider": "tiktok",
            "expected_amount": 500, "due_date": "2026-03-15",
        },
        headers=h, timeout=10,
    )
    liab_id = r.json()["id"]

    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab_id}",
        json={"expected_amount": 750, "notes": "زادت الفاتورة"},
        headers=h, timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["expected_amount"] == 750
    assert body["notes"] == "زادت الفاتورة"

    requests.post(
        f"{BASE_URL}/api/liabilities/{liab_id}/pay",
        json={
            "amount": 600, "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-02-01",
        },
        headers=h, timeout=10,
    )
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab_id}",
        json={"expected_amount": 400},
        headers=h, timeout=10,
    )
    assert r.status_code == 400
