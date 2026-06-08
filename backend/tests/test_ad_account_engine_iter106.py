"""Iter-106 — Ad-Account Balance + Auto-Debt Engine.

Validates every business rule from the spec:
  1. Spend covered by balance → balance ↓, no debt.
  2. Spend exceeds balance → balance → 0, the rest becomes debt (auto mode).
  3. Top-up pays existing debt FIRST, then adds remainder to balance.
  4. Manual mode never auto-creates debt; uncovered piece stays uncovered.
  5. Mode toggle via /settings.
  6. Ledger captures every movement with type and breakdown.
"""
import os
import uuid

import requests


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _new_user_with_account_and_bank(provider="snapchat", name="Snapchat 1"):
    suffix = uuid.uuid4().hex[:8]
    email = f"iter106-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#106t", "name": "AdAcc"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#106t"},
        timeout=10,
    )
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    bank = requests.post(
        f"{BASE_URL}/api/accounts",
        json={"name": "بنك Iter-106", "account_type": "bank",
              "currency": "SAR", "opening_balance": 50000,
              "opening_balance_date": "2026-01-01"},
        headers=h, timeout=10,
    ).json()
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "ad_account", "ad_provider": provider, "name": name},
        headers=h, timeout=10,
    ).json()
    return {"headers": h, "bank_id": bank["id"], "cp_id": cp["id"]}


# ── 1) Spend fully covered by balance → no debt ─────────────────────
def test_spend_covered_by_balance_no_debt():
    ctx = _new_user_with_account_and_bank()
    # Top-up 1000 first
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 1000, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx["headers"], timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/spend",
        json={"amount": 300, "spend_date": "2026-06-02"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["covered_by_balance"] == 300.0
    assert body["uncovered"] == 0.0
    assert body["debt_created"] == 0.0
    assert body["ad_account"]["balance"] == 700.0
    assert body["ad_account"]["open_debt"] == 0.0


# ── 2) Spend > balance → balance → 0 + debt = remainder (auto) ──────
def test_spend_exceeds_balance_creates_debt_in_auto_mode():
    """User's example: balance 500, spend 800 → balance 0, debt 300."""
    ctx = _new_user_with_account_and_bank()
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 500, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx["headers"], timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/spend",
        json={"amount": 800, "spend_date": "2026-06-02"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["covered_by_balance"] == 500.0
    assert body["uncovered"] == 300.0
    assert body["debt_created"] == 300.0
    assert body["ad_account"]["balance"] == 0.0
    assert body["ad_account"]["open_debt"] == 300.0

    # And a liability row must exist
    r = requests.get(
        f"{BASE_URL}/api/liabilities?kind=ad_account",
        headers=ctx["headers"], timeout=10,
    )
    rows = [l for l in r.json()["items"] if l.get("counterparty_id") == ctx["cp_id"]]
    assert len(rows) == 1
    assert rows[0]["expected_amount"] == 300.0


# ── 3) Top-up: pay down debt first, then balance ─────────────────────
def test_topup_pays_down_existing_debt_first():
    """User's example: debt 300, top-up 1000 → debt 0, balance 700."""
    ctx = _new_user_with_account_and_bank()
    # Generate a 300 debt: topup 500, spend 800
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 500, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx["headers"], timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/spend",
        json={"amount": 800, "spend_date": "2026-06-02"},
        headers=ctx["headers"], timeout=10,
    )
    # Now topup 1000 — should pay 300 debt then add 700 to balance.
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 1000, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-03"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied_to_debt"] == 300.0
    assert body["applied_to_balance"] == 700.0
    assert body["ad_account"]["open_debt"] == 0.0
    assert body["ad_account"]["balance"] == 700.0


# ── 4) Top-up that exactly clears debt + smaller top-up ─────────────
def test_topup_exactly_clears_debt():
    ctx = _new_user_with_account_and_bank()
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 200, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx["headers"], timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/spend",
        json={"amount": 700, "spend_date": "2026-06-02"},   # 500 debt
        headers=ctx["headers"], timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 500, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-03"},
        headers=ctx["headers"], timeout=10,
    )
    body = r.json()
    assert body["applied_to_debt"] == 500.0
    assert body["applied_to_balance"] == 0.0
    assert body["ad_account"]["open_debt"] == 0.0
    assert body["ad_account"]["balance"] == 0.0


# ── 5) Manual mode never creates auto-debt ──────────────────────────
def test_manual_mode_does_not_create_debt():
    ctx = _new_user_with_account_and_bank()
    # Switch to manual mode
    r = requests.put(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/settings",
        json={"debt_mode": "manual"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["debt_mode"] == "manual"

    # Same scenario as #2 — but no debt should be created
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 500, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx["headers"], timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/spend",
        json={"amount": 800, "spend_date": "2026-06-02"},
        headers=ctx["headers"], timeout=10,
    )
    body = r.json()
    assert body["covered_by_balance"] == 500.0
    assert body["uncovered"] == 300.0
    assert body["debt_created"] == 0.0       # ← NO auto-debt
    assert body["mode"] == "manual"
    assert body["ad_account"]["open_debt"] == 0.0
    assert body["ad_account"]["balance"] == 0.0


# ── 6) Ledger captures all events ───────────────────────────────────
def test_ledger_records_every_movement():
    ctx = _new_user_with_account_and_bank()
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 500, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx["headers"], timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/spend",
        json={"amount": 800, "spend_date": "2026-06-02"},
        headers=ctx["headers"], timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 1000, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-03"},
        headers=ctx["headers"], timeout=10,
    )
    r = requests.get(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/ledger",
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200
    rows = r.json()["items"]
    types = [r["type"] for r in rows]
    # Should have at least: topup, spend, debt, topup (newest first)
    assert "topup" in types
    assert "spend" in types
    assert "debt" in types


# ── 7) Top-up deducts from the bank account ────────────────────────
def test_topup_deducts_bank_balance():
    ctx = _new_user_with_account_and_bank()
    bank0 = requests.get(
        f"{BASE_URL}/api/accounts/{ctx['bank_id']}",
        headers=ctx["headers"], timeout=10,
    ).json()
    starting = bank0["current_balance"]
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx['cp_id']}/topup",
        json={"amount": 750, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx["headers"], timeout=10,
    )
    bank1 = requests.get(
        f"{BASE_URL}/api/accounts/{ctx['bank_id']}",
        headers=ctx["headers"], timeout=10,
    ).json()
    assert bank1["current_balance"] == starting - 750


# ── 8) List endpoint returns totals across all ad accounts ──────────
def test_list_totals_match_individual_accounts():
    ctx1 = _new_user_with_account_and_bank(provider="snapchat", name="Snap A")
    # add a second account for the SAME user
    cp2 = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "ad_account", "ad_provider": "tiktok",
              "name": "TikTok Main", "force": True},
        headers=ctx1["headers"], timeout=10,
    ).json()
    # snap: balance 300, no debt
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{ctx1['cp_id']}/topup",
        json={"amount": 300, "paid_from_account_id": ctx1["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx1["headers"], timeout=10,
    )
    # tiktok: balance 200, debt 100  (topup 200, spend 300)
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp2['id']}/topup",
        json={"amount": 200, "paid_from_account_id": ctx1["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx1["headers"], timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp2['id']}/spend",
        json={"amount": 300, "spend_date": "2026-06-02"},
        headers=ctx1["headers"], timeout=10,
    )
    r = requests.get(
        f"{BASE_URL}/api/ad-accounts",
        headers=ctx1["headers"], timeout=10,
    )
    body = r.json()
    # 2 ad accounts visible
    assert body["total"] == 2
    assert body["totals"]["balance"] == 300.0
    assert body["totals"]["open_debt"] == 100.0
