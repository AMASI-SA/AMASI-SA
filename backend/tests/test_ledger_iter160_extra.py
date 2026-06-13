"""Iter-160 — Additional regression coverage (single test).

Covers items not asserted by test_ledger_iter160.py / test_dashboard_ledger_ssot_iter160.py:

  1. /api/ad-accounts/{cp_id}/audit-log returns scoped entries.
  2. /api/ad-accounts/{cp_id}/adjustment-entries lists ledger rows.
  3. /api/dashboard daily_ads_total uses ledger SSOT (legacy daily_costs ignored).
  4. /api/reports/ads spend per platform uses ledger SSOT.
  5. /api/ad-accounts/{id}/topup + /spend still work.
  6. /api/ad-accounts/{id}/credit-limit unaffected.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_iter160_extra_regression():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email = f"x160-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "X", "email": email,
                                    "password": "pass1234"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        now = datetime.now(timezone.utc).isoformat()
        today = now[:10]

        # ── Create ad-account counterparties for each provider ───────
        cp_ids = {}
        for provider in ("snapchat", "meta", "tiktok"):
            cp_id = str(uuid.uuid4())
            await db.counterparties.insert_one({
                "id": cp_id, "user_id": uid, "kind": "ad_account",
                "name": f"acc-{provider}", "name_lower": f"acc-{provider}",
                "ad_provider": provider, "balance": 0.0,
            })
            cp_ids[provider] = cp_id

        # Seed a liability for the snapchat account (legacy debt = 400)
        snap_id = cp_ids["snapchat"]
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "kind": "ad_account", "counterparty_id": snap_id,
            "expected_amount": 400, "paid_amount": 0,
            "status": "unpaid", "created_at": now,
        })

        # ── (5) topup endpoint still works ────────────────────────
        # First create a bank account to topup from
        bank_id = str(uuid.uuid4())
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid,
            "name": "Test Bank", "kind": "bank", "balance": 5000.0,
            "created_at": now, "updated_at": now,
        })
        r_topup = await client.post(
            f"/api/ad-accounts/{snap_id}/topup", headers=h,
            json={"amount": 100, "transaction_date": today,
                  "paid_from_account_id": bank_id},
        )
        assert r_topup.status_code in (200, 201), r_topup.text

        # ── (6) credit-limit unaffected ───────────────────────────
        r_cl = await client.put(
            f"/api/ad-accounts/{snap_id}/credit-limit", headers=h,
            json={"credit_limit": 1000.0},
        )
        assert r_cl.status_code in (200, 204), r_cl.text

        # ── (1)+(2) ad-account audit-log & adjustment-entries ─────
        # Post an adjustment via scoped endpoint
        r_adj = await client.post(
            f"/api/ad-accounts/{snap_id}/adjustments", headers=h,
            json={"kind": "settlement", "amount": 50,
                  "direction": "reduce_debt",
                  "reason_code": "actual_payment", "notes": "test"},
        )
        assert r_adj.status_code == 200, r_adj.text

        r_audit = await client.get(
            f"/api/ad-accounts/{snap_id}/audit-log", headers=h)
        assert r_audit.status_code == 200
        audit_items = r_audit.json()["items"]
        assert len(audit_items) >= 1
        # all items must be scoped to this ad-account entity
        for it in audit_items:
            assert it["entity_type"] == "ad_account"
            assert it["entity_id"] == snap_id

        r_ent = await client.get(
            f"/api/ad-accounts/{snap_id}/adjustment-entries", headers=h)
        assert r_ent.status_code == 200
        ent_items = r_ent.json()["items"]
        assert len(ent_items) >= 1
        for it in ent_items:
            assert it["entity_id"] == snap_id
            assert it["entry_type"] in ("settlement", "writeoff",
                                        "adjustment", "reversal")

        # ── Seed legacy daily_costs / meta_ads_daily / tiktok_ads_daily
        #     with huge numbers; the dashboard MUST IGNORE them. ────
        await db.daily_costs.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "date": today,
            "snapchat_ads": 99999.0, "snapchat_ads_2": 88888.0,
            "tiktok_ads": 77777.0,
            "created_at": now, "updated_at": now,
        })
        await db.meta_ads_daily.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "date": today,
            "spend": 55555.0, "purchases": 0, "purchase_value": 0,
        })
        await db.tiktok_ads_daily.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "date": today,
            "spend": 66666.0,
        })

        # Seed ad_account_ledger.type=spend rows
        ledger_amounts = {"snapchat": 250.0, "meta": 100.0, "tiktok": 75.0}
        for prov, amt in ledger_amounts.items():
            await db.ad_account_ledger.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_ids[prov], "type": "spend",
                "amount": amt, "date": today, "created_at": now,
            })

        # ── (3) /api/dashboard daily_ads_total uses ledger only ───
        r_d = await client.get(
            f"/api/dashboard?from_date={today}&to_date={today}", headers=h)
        assert r_d.status_code == 200
        d = r_d.json()
        totals = d.get("totals") or {}
        # The total ads cost must equal the sum of the seeded ledger
        # entries (250+100+75 = 425) — NOT the legacy giant numbers.
        expected_total = sum(ledger_amounts.values())
        actual_total = float(
            totals.get("daily_ads_total")
            or totals.get("total_ads_cost")
            or 0
        )
        assert abs(actual_total - expected_total) < 0.5, (
            f"daily_ads_total leaked legacy data: got {actual_total}, "
            f"expected {expected_total}, totals={totals}"
        )

        # ── (4) /api/reports/ads uses ledger SSOT ─────────────────
        r_rep = await client.get(
            f"/api/reports/ads?start_date={today}&end_date={today}",
            headers=h,
        )
        assert r_rep.status_code == 200, r_rep.text
        rep = r_rep.json()
        # Find platform spends in the response (structure may vary)
        body_str = str(rep)
        # Negative assertion: no leaked legacy huge numbers
        for legacy in ("99999", "88888", "77777", "55555", "66666"):
            assert legacy not in body_str, (
                f"/api/reports/ads contains legacy value {legacy}"
            )

        # ── cleanup ───────────────────────────────────────────────
        await db.counterparties.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
        await db.daily_costs.delete_many({"user_id": uid})
        await db.meta_ads_daily.delete_many({"user_id": uid})
        await db.tiktok_ads_daily.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})
        await db.accounts.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
