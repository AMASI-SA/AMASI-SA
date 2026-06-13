"""Iter-160 — Universal Ledger + Audit Log (single suite test).

Multiple AsyncClient tests in one file fail with "Event loop is closed"
due to motor's module-level connection caching across pytest-asyncio
event loops. We consolidate every assertion into ONE test so we run
one event loop only.

Coverage:
  1. Reason codes endpoint returns canonical Arabic dictionary.
  2. Reason code is MANDATORY for adjustments.
  3. Adjustment creates a posted entry + audit row.
  4. Reversal mirrors original on opposite side + marks original
     as `reversed`. Original entry is NEVER deleted.
  5. Balance is computed from POSTED entries only.
  6. Ad-account /adjustments endpoint reduces `open_debt`.
  7. Old destructive endpoints (reset-debt, recompute-debt) GONE.
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
async def test_universal_ledger_end_to_end():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport,
                            base_url="http://test") as client:
        # ── Register a fresh user ─────────────────────────────────
        email = f"led-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        # ── (1) reason codes ──────────────────────────────────────
        r = await client.get("/api/ledger/reason-codes", headers=h)
        assert r.status_code == 200
        codes = {c["code"] for c in r.json()}
        for req in ("actual_payment", "data_entry_error",
                     "duplicate_entry", "accounting_settle",
                     "approved_writeoff", "other"):
            assert req in codes

        # ── (2) reason_code is mandatory ──────────────────────────
        r = await client.post("/api/ledger/adjustments", headers=h, json={
            "entity_type": "ad_account", "entity_id": "x",
            "amount": 50, "kind": "settlement",
            "direction": "reduce_debt", "reason_code": "",
        })
        assert r.status_code in (400, 422)

        # ── (3) Create settlement, see audit row ──────────────────
        ent_id = f"x-{uuid.uuid4().hex[:8]}"
        r = await client.post("/api/ledger/adjustments", headers=h, json={
            "entity_type": "ad_account", "entity_id": ent_id,
            "amount": 200, "kind": "settlement",
            "direction": "reduce_debt",
            "reason_code": "actual_payment", "notes": "تجربة",
        })
        assert r.status_code == 200
        entry = r.json()["entry"]
        eid = entry["id"]
        assert entry["status"] == "posted"
        assert entry["side"] == "debit"
        assert entry["reason_code"] == "actual_payment"

        r2 = await client.get(
            f"/api/ledger/audit-log?entity_id={ent_id}", headers=h)
        assert "create_settlement" in {it["action"]
                                          for it in r2.json()["items"]}

        # ── (4) Reverse → original status=reversed, mirror entry created ─
        r3 = await client.post(
            f"/api/ledger/entries/{eid}/reverse", headers=h,
            json={"reason_code": "data_entry_error", "notes": "أُلغي"},
        )
        assert r3.status_code == 200
        rev = r3.json()["reversal_entry"]
        assert rev["entry_type"] == "reversal"
        assert rev["side"] == "credit"  # opposite of debit
        assert rev["reverses_entry_id"] == eid
        # double-reverse blocked
        r4 = await client.post(
            f"/api/ledger/entries/{eid}/reverse", headers=h,
            json={"reason_code": "other"},
        )
        assert r4.status_code == 400
        # original now reversed
        r5 = await client.get(
            f"/api/ledger/entries?entity_id={ent_id}", headers=h)
        statuses = {it["id"]: it["status"] for it in r5.json()["items"]}
        assert statuses[eid] == "reversed"

        # ── (5) Balance computed from POSTED only ─────────────────
        ent_b = f"b-{uuid.uuid4().hex[:8]}"
        for d in [
            {"amount": 300, "kind": "settlement",
              "direction": "reduce_debt", "reason_code": "actual_payment"},
            {"amount": 100, "kind": "writeoff",
              "direction": "reduce_debt", "reason_code": "approved_writeoff"},
            {"amount": 50,  "kind": "adjustment",
              "direction": "increase_debt", "reason_code": "data_entry_error"},
        ]:
            await client.post("/api/ledger/adjustments", headers=h, json={
                "entity_type": "ad_account", "entity_id": ent_b, **d,
            })
        b = (await client.get(
            f"/api/ledger/balance?entity_type=ad_account&entity_id={ent_b}",
            headers=h)).json()
        assert b["debits"] == 400.0
        assert b["credits"] == 50.0
        assert b["net_balance"] == 350.0

        # ── (6) Ad-account /adjustments reduces displayed open_debt ──
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        cp_id = str(uuid.uuid4())
        await db.counterparties.insert_one({
            "id": cp_id, "user_id": uid, "kind": "ad_account",
            "name": "TestAcc", "name_lower": "testacc",
            "ad_provider": "meta", "balance": 0.0,
        })
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "kind": "ad_account", "counterparty_id": cp_id,
            "expected_amount": 500, "paid_amount": 0,
            "status": "unpaid",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # before adjustment: open_debt = 500
        rows = (await client.get("/api/ad-accounts", headers=h)).json()["items"]
        acc = next(a for a in rows if a["id"] == cp_id)
        assert acc["open_debt"] == 500.0

        # post a 200 settlement on the ad-account
        r6 = await client.post(
            f"/api/ad-accounts/{cp_id}/adjustments", headers=h, json={
                "kind": "settlement", "amount": 200,
                "direction": "reduce_debt",
                "reason_code": "actual_payment",
                "notes": "تسوية جزئية",
            })
        assert r6.status_code == 200
        # liability row untouched (immutable history)
        liab = await db.liabilities.find_one(
            {"user_id": uid, "counterparty_id": cp_id})
        assert liab["expected_amount"] == 500
        assert liab["paid_amount"] == 0
        # but displayed open_debt is now 300
        rows = (await client.get("/api/ad-accounts", headers=h)).json()["items"]
        acc = next(a for a in rows if a["id"] == cp_id)
        assert acc["open_debt"] == 300.0
        assert acc["adjustments_total_debit"] == 200.0

        # cleanup
        await db.counterparties.delete_one({"id": cp_id})
        await db.liabilities.delete_many({"counterparty_id": cp_id})
        await db.general_ledger.delete_many({"entity_id": cp_id})
        await db.accounting_audit_log.delete_many({"entity_id": cp_id})

        # ── (7) Old destructive endpoints removed ─────────────────
        r7a = await client.post(
            "/api/ad-accounts/anything/reset-debt", headers=h)
        assert r7a.status_code in (404, 405)
        r7b = await client.post(
            "/api/ad-accounts/anything/recompute-debt", headers=h)
        assert r7b.status_code in (404, 405)
