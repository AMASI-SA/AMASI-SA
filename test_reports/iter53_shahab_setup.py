"""Iter-53 — Setup for شهاب stale-partial liability scenario.

Creates a fresh user, bank account, شهاب (monthly=3000, daily accrual,
start_date=today-42d so net_due≈4200), generates salaries, then
CORRUPTS the resulting liability row to status='partial' with
paid_amount=expected_amount (remaining=0) — simulating the stale data
condition the merchant reported on production.

Prints JSON-line context the Playwright script will consume.
"""
import os
import sys
import uuid
import json
import datetime as dt
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


def setup():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter53-{suffix}@test.com"
    pwd = "TestPass123!"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": pwd, "name": "Iter53"},
                      timeout=15)
    assert r.status_code in (200, 201), r.text
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=15)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    uid = me["id"]

    bank = requests.post(f"{BASE_URL}/api/accounts",
                         json={"name": "Test Bank", "account_type": "bank",
                               "opening_balance": 100000.0},
                         headers=hdr, timeout=10).json()

    # شهاب: 3000/month daily accrual, started 42 days ago.
    start = (dt.date.today() - dt.timedelta(days=42)).isoformat()
    shahab = requests.post(f"{BASE_URL}/api/operating-expenses/salaries",
                           json={"name": "شهاب", "category": "employee",
                                 "monthly_amount": 3000.0,
                                 "accrual_mode": "daily",
                                 "start_date": start,
                                 "status": "active"},
                           headers=hdr, timeout=10).json()

    # Generate current-month salary row
    gen = requests.post(f"{BASE_URL}/api/liabilities/generate-salaries",
                        headers=hdr, timeout=20)
    assert gen.status_code == 200, gen.text

    # Find شهاب's liability
    lr = requests.get(f"{BASE_URL}/api/liabilities",
                      params={"kind": "salary",
                              "employee_salary_id": shahab["id"]},
                      headers=hdr, timeout=10).json()
    items = lr.get("items", lr) if isinstance(lr, dict) else lr
    liab = items[0]
    liab_id = liab["id"]

    # Ensure accrual_mode=daily on liability
    requests.put(f"{BASE_URL}/api/liabilities/{liab_id}/accrual-mode",
                 json={"mode": "daily", "start_date": start},
                 headers=hdr, timeout=10)

    # Verify net_due from accrual summary
    sumr = requests.get(f"{BASE_URL}/api/liabilities/salary-accrual-summary",
                        headers=hdr, timeout=10).json()
    net_due = 0.0
    for e in sumr.get("employees", []):
        if e["id"] == shahab["id"]:
            net_due = float(e.get("net_due") or 0)

    # ── CORRUPT the row: simulate stale paid-but-not-paid state ───────
    db = _mdb()
    expected = float(liab.get("expected_amount") or 3000.0)
    res = db.liabilities.update_one(
        {"id": liab_id, "user_id": uid},
        {"$set": {"paid_amount": expected, "status": "partial",
                  "updated_at": dt.datetime.utcnow().isoformat()}},
    )
    assert res.modified_count == 1, "Failed to corrupt شهاب liability"

    # Sanity: verify still returns in open listing with remaining=0
    listing = requests.get(f"{BASE_URL}/api/liabilities",
                           params={"kind": "salary",
                                   "employee_salary_id": shahab["id"]},
                           headers=hdr, timeout=10).json()
    items2 = listing.get("items", listing) if isinstance(listing, dict) else listing
    print(f"DEBUG_LIABS: {items2}", file=sys.stderr)

    ctx = {
        "base_url": BASE_URL,
        "email": email, "password": pwd, "token": token,
        "shahab_id": shahab["id"], "liab_id": liab_id,
        "bank_id": bank["id"], "expected": expected, "net_due": net_due,
    }
    print("CTX_JSON=" + json.dumps(ctx, ensure_ascii=False))
    return ctx


if __name__ == "__main__":
    setup()
