"""Iter-101 — Shipping liability in Financial Position.

Rules being verified:
  • Only orders with delivered/completed statuses accrue shipping debt.
  • Cancelled / in-transit / refunded orders do NOT accrue debt.
  • `/api/liabilities/summary` exposes `liabilities.shipping_unpaid`
    and `by_shipping_company` breakdown.
  • Recording a `shipping_payments` row decreases the remaining debt
    (this includes the COD-net-method fee deduction from Iter-98).
  • The numbers must match `/api/shipping-accounts` (single source).
"""
import os
import uuid
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


def _new_user():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter101-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#101t", "name": "Shipping FP"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#101t"},
        timeout=10,
    )
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=h, timeout=10).json()
    return {"headers": h, "uid": me["id"]}


def _configure_deferred_courier(mongo, uid: str, name: str = "أرامكس",
                                cost: float = 18.0, vat_rate: float = 0.15):
    """Set up a deferred shipping company in the user's settings.
    NOTE: shipping_accounts reads `cost` and `vat_rate` (NOT
    `cost_per_order`/`vat_percent` — those are only on the bootstrap
    defaults in auth.py)."""
    mongo.settings.update_one(
        {"user_id": uid},
        {"$set": {
            "user_id": uid,
            "shipping_companies": [
                {"name": name, "is_deferred": True,
                 "cost": cost, "vat_rate": vat_rate},
            ],
        }},
        upsert=True,
    )


def _seed_order(mongo, uid: str, status: str, company: str = "أرامكس",
                shipping_cost: float = 0.0):
    """Insert one unified_orders row for the test user."""
    mongo.unified_orders.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "order_number": f"ORD-{uuid.uuid4().hex[:6]}",
        "order_status": status,
        "shipping_company": company,
        "shipping_cost": shipping_cost,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


# ── 1) Only delivered orders accrue debt ────────────────────────────
def test_only_delivered_orders_create_shipping_debt(mongo_db):
    ctx = _new_user()
    _configure_deferred_courier(mongo_db, ctx["uid"], cost=20.0, vat_rate=0.15)

    # Mix of statuses, only "تم التوصيل" should count.
    _seed_order(mongo_db, ctx["uid"], "تم التوصيل")        # ✓ counts
    _seed_order(mongo_db, ctx["uid"], "تم التوصيل")        # ✓ counts
    _seed_order(mongo_db, ctx["uid"], "delivered")          # ✓ counts (en)
    _seed_order(mongo_db, ctx["uid"], "completed")          # ✓ counts (en)
    _seed_order(mongo_db, ctx["uid"], "قيد التنفيذ")        # ✗ ignored
    _seed_order(mongo_db, ctx["uid"], "تم الشحن")           # ✗ ignored
    _seed_order(mongo_db, ctx["uid"], "جاري التوصيل")      # ✗ ignored
    _seed_order(mongo_db, ctx["uid"], "ملغي")              # ✗ ignored
    _seed_order(mongo_db, ctx["uid"], "مسترجع")            # ✗ ignored

    s = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["headers"], timeout=10,
    ).json()

    # 4 delivered × (20 × 1.15) = 4 × 23 = 92
    assert s["liabilities"]["shipping_unpaid"] == 92.0, s["liabilities"]
    bsc = s["liabilities"]["by_shipping_company"]
    assert "أرامكس" in bsc
    assert bsc["أرامكس"]["orders_count"] == 4
    assert bsc["أرامكس"]["owed"] == 92.0
    assert bsc["أرامكس"]["remaining"] == 92.0


# ── 2) Shipping liability appears in /financial-position summary ────
def test_shipping_unpaid_in_summary_and_total(mongo_db):
    ctx = _new_user()
    _configure_deferred_courier(mongo_db, ctx["uid"], cost=10.0, vat_rate=0.0)
    for _ in range(5):
        _seed_order(mongo_db, ctx["uid"], "تم التوصيل")

    s = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["headers"], timeout=10,
    ).json()
    # 5 × 10 = 50, no VAT
    assert s["liabilities"]["shipping_unpaid"] == 50.0
    # Total liabilities include shipping
    assert s["liabilities"]["total"] == 50.0
    # And the net position deducts it from assets
    assert s["net_position"] == s["assets"]["total"] - s["liabilities"]["total"]


# ── 3) Payment reduces liability automatically ──────────────────────
def test_shipping_payment_reduces_liability(mongo_db):
    """Posting a `shipping_payments` row (manual sada or COD-net fee)
    must reduce the `remaining` shipping debt for that company."""
    ctx = _new_user()
    _configure_deferred_courier(mongo_db, ctx["uid"], cost=25.0, vat_rate=0.0)
    for _ in range(4):
        _seed_order(mongo_db, ctx["uid"], "تم التوصيل")
    # Initial: 4 × 25 = 100 owed
    s0 = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["headers"], timeout=10,
    ).json()
    assert s0["liabilities"]["shipping_unpaid"] == 100.0

    # Insert a shipping payment of 30 (e.g. COD-net fee deduction).
    mongo_db.shipping_payments.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": ctx["uid"],
        "company_name": "أرامكس",
        "amount": 30.0,
        "payment_date": "2026-06-10",
        "invoice_number": "FEE-TEST",
        "note": "deducted from COD net",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    s1 = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["headers"], timeout=10,
    ).json()
    assert s1["liabilities"]["shipping_unpaid"] == 70.0
    assert s1["liabilities"]["by_shipping_company"]["أرامكس"]["paid"] == 30.0
    assert s1["liabilities"]["by_shipping_company"]["أرامكس"]["remaining"] == 70.0


# ── 4) Numbers match /api/shipping-accounts (single source of truth) ─
def test_matches_shipping_accounts_endpoint(mongo_db):
    ctx = _new_user()
    _configure_deferred_courier(mongo_db, ctx["uid"], cost=15.0, vat_rate=0.15)
    for _ in range(3):
        _seed_order(mongo_db, ctx["uid"], "تم التوصيل")
    # One cancelled — must be ignored by both endpoints.
    _seed_order(mongo_db, ctx["uid"], "ملغي")

    fp = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["headers"], timeout=10,
    ).json()
    sa = requests.get(
        f"{BASE_URL}/api/shipping-accounts",
        headers=ctx["headers"], timeout=10,
    ).json()

    # /api/shipping-accounts returns a list of company accounts.
    # The same company's remaining must match.
    sa_remaining = {a["name"]: a["remaining"] for a in sa.get("accounts", sa) if isinstance(a, dict)}
    fp_by = fp["liabilities"]["by_shipping_company"]

    for name, fp_data in fp_by.items():
        if fp_data["remaining"] == 0 and name not in sa_remaining:
            continue
        assert name in sa_remaining, f"{name} missing in shipping-accounts response"
        assert sa_remaining[name] == fp_data["remaining"], (
            f"{name}: FP={fp_data['remaining']} vs SA={sa_remaining[name]}"
        )


# ── 5) COD-net fee deduction (Iter-98) decreases shipping liability ──
def test_cod_net_fee_reduces_shipping_liability_via_transfer(mongo_db):
    """End-to-end: deliver 5 orders @ 20 → 100 owed. Run a COD-net
    transfer with shipping_fee_deducted=40 → liability falls to 60."""
    ctx = _new_user()
    _configure_deferred_courier(mongo_db, ctx["uid"], name="أرامكس",
                                 cost=20.0, vat_rate=0.0)
    for _ in range(5):
        _seed_order(mongo_db, ctx["uid"], "تم التوصيل")

    # Seed COD platform + bank
    cod_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    mongo_db.accounts.insert_one({
        "id": cod_id, "user_id": ctx["uid"],
        "name": "الدفع عند الاستلام",
        "account_type": "payment_platform",
        "currency": "SAR", "opening_balance": 1000.0,
        "current_balance": 1000.0, "expected_orders_balance": 1000.0,
        "status": "active", "normalized_payment_method": "cash_on_delivery",
        "created_at": now, "updated_at": now,
    })
    bank = requests.post(
        f"{BASE_URL}/api/accounts",
        json={"name": "بنك Iter-101", "account_type": "bank",
              "currency": "SAR", "opening_balance": 0,
              "opening_balance_date": "2026-01-01"},
        headers=ctx["headers"], timeout=10,
    ).json()

    # COD-net transfer: gross 200, fee 40 → net 160 to bank.
    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": cod_id,
            "to_account_id": bank["id"],
            "amount": 160,
            "transfer_date": "2026-06-10",
            "reference": "COD-NET-101",
            "cod_gross_collected": 200,
            "shipping_fee_deducted": 40,
            "shipping_fee_settles_against": "shipping_payable",
            "shipping_company": "أرامكس",
        },
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text

    s = requests.get(
        f"{BASE_URL}/api/liabilities/summary",
        headers=ctx["headers"], timeout=10,
    ).json()
    # 5 × 20 = 100 owed − 40 deducted = 60 remaining.
    assert s["liabilities"]["shipping_unpaid"] == 60.0, s["liabilities"]
    assert s["liabilities"]["by_shipping_company"]["أرامكس"]["paid"] == 40.0
    assert s["liabilities"]["by_shipping_company"]["أرامكس"]["remaining"] == 60.0
