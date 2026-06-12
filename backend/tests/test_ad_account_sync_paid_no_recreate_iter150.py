"""Iter-150 — Regression for the "paid liability re-created on next sync" bug.

User report (Feb 2026, Arabic): "عند تسديد مديونيه يقوم النظام بحذف
المديونيه والمزامنه الثانيه يضيف المديونيه من جديد".

Translated: "When I pay off the debt, the system removes it, then the
NEXT auto-sync adds the debt all over again."

Root cause (pre-fix): _run_sync_for_all used a drop-and-recreate pattern
for force=True re-syncs. The "drop" step looked for a liability with
status in [unpaid, partial] to reduce — but once the user paid it, the
status was `paid` and the drop step found nothing. The "apply" step
then created a brand-new liability for the FULL daily spend.

Fix: delta-based sync. Only apply (platform_total − prev_total_applied)
as new spend. Re-runs with no new spend are a genuine no-op and never
touch the (possibly paid) liability.
"""
import os
import uuid

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
    email = f"i150-{suffix}@example.com"
    pwd = "T#150a"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I150"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    # Make a bank account for topups (used to pay off liability)
    bank = requests.post(f"{BASE_URL}/api/accounts",
                         json={"name": "Bank Main", "account_type": "bank",
                               "opening_balance": 100000.0},
                         headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb(),
           "bank_id": bank["id"]}


def _make_account(ctx, name, provider="snapchat", external_id=None):
    payload = {"name": name, "ad_provider": provider, "force": True}
    if external_id:
        payload["external_account_id"] = external_id
    r = requests.post(f"{BASE_URL}/api/ad-accounts",
                      json=payload, headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_paid_off_liability_is_not_recreated_on_force_resync(ctx):
    """The critical bug: after the user pays the cron-created liability,
    the next force=True sync must NOT recreate it."""
    cp = _make_account(ctx, "Meta paid off", "meta", "act_PAIDOFF")
    today = __import__("datetime").date.today().isoformat()

    # 1) Platform shows 500 SAR spend → first sync creates 500 liability
    ctx["db"].meta_ads_daily.insert_one({
        "user_id": ctx["uid"], "account_id": "act_PAIDOFF",
        "date": today, "spend": 500.0,
    })
    r1 = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                       json={"from_date": today, "to_date": today},
                       headers=ctx["hdr"], timeout=15)
    res1 = r1.json()["results"][0]
    assert res1["spend"] == 500.0
    assert res1["debt_created"] == 500.0

    # 2) User pays off the liability via /topup endpoint
    topup_r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp}/topup",
        json={"amount": 500.0, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": today, "notes": "سداد المديونية"},
        headers=ctx["hdr"], timeout=10,
    )
    assert topup_r.status_code == 200, topup_r.text

    # Verify liability is now paid
    paid_liab = ctx["db"].liabilities.find_one(
        {"user_id": ctx["uid"], "counterparty_id": cp,
         "source": "ad_account_cron"},
    )
    assert paid_liab is not None
    assert paid_liab["status"] == "paid"
    assert paid_liab["paid_amount"] == 500.0

    # Verify open_debt summary == 0
    summary_after_pay = requests.get(
        f"{BASE_URL}/api/ad-accounts/{cp}",
        headers=ctx["hdr"], timeout=10,
    ).json()
    assert summary_after_pay["open_debt"] == 0.0, (
        f"Liability should be paid off but open_debt={summary_after_pay['open_debt']}"
    )

    # 3) Cron runs again (force=True, same day). THIS is where the bug
    #    was: it would recreate the 500 liability. With the Iter-150
    #    fix it sees delta=0 and is a no-op.
    r2 = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                       json={"from_date": today, "to_date": today, "force": True},
                       headers=ctx["hdr"], timeout=15)
    res2 = r2.json()["results"][0]
    assert res2.get("no_op") is True, f"Expected no_op=True, got {res2}"
    assert res2["debt_created"] == 0.0

    # 4) CRITICAL ASSERTION: no NEW unpaid liability was created
    open_liabs = list(ctx["db"].liabilities.find(
        {"user_id": ctx["uid"], "counterparty_id": cp,
         "kind": "ad_account",
         "status": {"$in": ["unpaid", "partial"]}},
    ))
    assert len(open_liabs) == 0, (
        f"Bug recurrence: paid-off liability was recreated. open={open_liabs}"
    )

    # 5) The paid liability should still be paid, with same amount
    paid_liabs = list(ctx["db"].liabilities.find(
        {"user_id": ctx["uid"], "counterparty_id": cp,
         "source": "ad_account_cron"},
    ))
    assert len(paid_liabs) == 1
    assert paid_liabs[0]["status"] == "paid"
    assert paid_liabs[0]["expected_amount"] == 500.0

    # 6) open_debt remains 0
    summary_after_resync = requests.get(
        f"{BASE_URL}/api/ad-accounts/{cp}",
        headers=ctx["hdr"], timeout=10,
    ).json()
    assert summary_after_resync["open_debt"] == 0.0


def test_paid_off_then_new_spend_creates_new_debt(ctx):
    """After paying off the cron debt, if the platform reports MORE
    spend later in the day, only the DELTA should be added as new debt
    — not the full new total."""
    cp = _make_account(ctx, "Snap follow-on", "snapchat", "acc_FOLLOW")
    today = __import__("datetime").date.today().isoformat()

    # 1) Spend = 200 → debt = 200
    ctx["db"].snapchat_account_daily.insert_one({
        "user_id": ctx["uid"], "ad_account_id": "acc_FOLLOW",
        "date": today, "spend": 200.0,
    })
    requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                  json={"from_date": today, "to_date": today},
                  headers=ctx["hdr"], timeout=15)

    # 2) User pays off the 200 liability
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp}/topup",
        json={"amount": 200.0, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": today, "notes": "سداد"},
        headers=ctx["hdr"], timeout=10,
    )

    # 3) Platform reports 50 more SAR spent (total now = 250)
    # Note: unique index on (user_id, ad_account_id, date) → upsert.
    ctx["db"].snapchat_account_daily.update_one(
        {"user_id": ctx["uid"], "ad_account_id": "acc_FOLLOW",
         "date": today},
        {"$inc": {"spend": 50.0}},
    )

    # 4) Force re-sync — delta = 250 - 200 = 50 → new debt 50
    r2 = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                       json={"from_date": today, "to_date": today, "force": True},
                       headers=ctx["hdr"], timeout=15)
    res2 = r2.json()["results"][0]
    assert res2["spend"] == 250.0
    assert res2.get("delta_applied") == 50.0
    assert res2["debt_created"] == 50.0

    # 5) Total open debt = 50 (the NEW liability), not 250
    summary = requests.get(f"{BASE_URL}/api/ad-accounts/{cp}",
                          headers=ctx["hdr"], timeout=10).json()
    assert summary["open_debt"] == 50.0, (
        f"Should be 50 (delta), got {summary['open_debt']}"
    )

    # 6) Two liability rows total — one paid (200), one open (50)
    liabs = list(ctx["db"].liabilities.find(
        {"user_id": ctx["uid"], "counterparty_id": cp,
         "source": "ad_account_cron"},
    ).sort("created_at", 1))
    assert len(liabs) == 2
    assert liabs[0]["status"] == "paid"
    assert liabs[0]["expected_amount"] == 200.0
    assert liabs[1]["status"] == "unpaid"
    assert liabs[1]["expected_amount"] == 50.0


def test_repeated_force_sync_no_spend_change_is_pure_noop(ctx):
    """Running force=True 5 times in a row with no change in platform
    spend must NOT add any new ledger rows or modify the liability."""
    cp = _make_account(ctx, "TT noop", "tiktok")
    today = __import__("datetime").date.today().isoformat()
    ctx["db"].tiktok_ads_daily.insert_one({
        "user_id": ctx["uid"], "date": today, "spend": 120.0,
        "campaign_id": "_default",
    })
    # First sync
    r1 = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                       json={"from_date": today, "to_date": today},
                       headers=ctx["hdr"], timeout=15)
    res1 = r1.json()["results"][0]
    if res1.get("skipped") or res1.get("error"):
        pytest.skip(f"tiktok scope check: {res1}")
    initial_liab = ctx["db"].liabilities.find_one(
        {"user_id": ctx["uid"], "counterparty_id": cp,
         "source": "ad_account_cron"},
    )
    initial_updated = initial_liab["updated_at"]

    # Force re-sync 5 times — must be no-op
    for _ in range(5):
        r = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                          json={"from_date": today, "to_date": today, "force": True},
                          headers=ctx["hdr"], timeout=15)
        rr = r.json()["results"][0]
        assert rr.get("no_op") is True

    # Ledger should still have exactly 1 auto_cron row
    rows = list(ctx["db"].ad_account_ledger.find(
        {"user_id": ctx["uid"], "counterparty_id": cp,
         "type": "spend", "breakdown.auto_cron": True},
    ))
    assert len(rows) == 1

    # Liability unchanged (updated_at preserved → no DB writes)
    final_liab = ctx["db"].liabilities.find_one(
        {"user_id": ctx["uid"], "counterparty_id": cp,
         "source": "ad_account_cron"},
    )
    assert final_liab["updated_at"] == initial_updated
    assert final_liab["expected_amount"] == 120.0
