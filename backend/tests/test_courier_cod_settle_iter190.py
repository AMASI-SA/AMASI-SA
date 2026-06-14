"""Iter-190 — Multi-leg COD settlement for shipping companies.

Covers all four real-world scenarios captured in the merchant brief:
  (1) Full transfer to bank closes the COD receivable.
  (2) Partial transfer + shipping cost withheld.
  (3) Partial transfer + COD fee withheld.
  (4) Partial transfer + shipping cost + COD fee + other fees.
Plus the guards:
  • total settlement may NOT exceed the open cod_receivable.
  • bank_amount > 0 requires bank_account_id of type bank|cash only.
  • other_fees > 0 requires a valid expense_category code.
  • Unmatched remainder STAYS on the courier as cod_receivable.

All four legs are posted in ONE balanced txn_group via the Universal
Ledger; no legacy `shipping_payments` / `courier_transfers` rows are
written.
"""
import os
import uuid

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_courier_cod_settlement_all_scenarios():
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        email = f"cod-settle-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "S", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # ── Seed: 1 courier + 1 bank + 1 cash + default categories ──
        courier_id = str(uuid.uuid4())
        bank_id = str(uuid.uuid4())
        cash_id = str(uuid.uuid4())
        supplier_id = str(uuid.uuid4())  # negative test (not a bank)
        await db.counterparties.insert_many([
            {"id": courier_id, "user_id": uid, "kind": "courier",
             "name": "iMile", "name_lower": "imile"},
            {"id": supplier_id, "user_id": uid, "kind": "supplier",
             "name": "Sup", "name_lower": "sup"},
        ])
        await db.accounts.insert_many([
            {"id": bank_id, "user_id": uid, "account_type": "bank",
             "name": "الراجحي", "current_balance": 0},
            {"id": cash_id, "user_id": uid, "account_type": "cash",
             "name": "الصندوق الرئيسي", "current_balance": 0},
        ])
        # Trigger default expense categories.
        await client.get("/api/accounting/expense-categories", headers=h)

        # ── Helper: bootstrap an open COD receivable on the courier
        # by posting a manual ledger entry. We use the migration-style
        # opening_balance so we don't tangle with order ingestion.
        async def seed_cod_receivable(amount: float):
            from ledger_core import post_txn_group
            await post_txn_group(
                db, user_id=uid, actor_id=uid, actor_name="seed",
                txn_type="adjustment", notes=f"seed COD {amount}",
                entries=[
                    {"entity_type": "courier", "entity_id": courier_id,
                     "sub_account": "cod_receivable", "side": "debit",
                     "amount": amount, "entry_type": "opening_balance"},
                    {"entity_type": "equity", "entity_id": "opening_balance",
                     "side": "credit", "amount": amount,
                     "entry_type": "opening_balance"},
                ],
            )

        # ────────────────────────────────────────────────────────────
        # Scenario 1 — Full transfer to bank closes the COD.
        # COD = 5,000 → bank 5,000.
        # ────────────────────────────────────────────────────────────
        await seed_cod_receivable(5000)
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h,
            json={"bank_amount": 5000, "bank_account_id": bank_id,
                  "notes": "تحويل كامل"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["settlement_total"] == 5000
        assert body["previous_cod_balance"] == 5000
        assert body["remaining_cod_balance"] == 0
        # Verify the ledger entries are balanced & share one txn_group.
        gid = body["txn_group_id"]
        rows = await db.general_ledger.find(
            {"txn_group_id": gid}, {"_id": 0}).to_list(20)
        assert len(rows) == 2
        debits = sum(x["amount"] for x in rows if x["side"] == "debit")
        credits = sum(x["amount"] for x in rows if x["side"] == "credit")
        assert debits == credits == 5000

        # ────────────────────────────────────────────────────────────
        # Scenario 2 — Partial transfer + shipping cost.
        # COD = 10,000 → bank 8,000 + shipping 2,000.
        # ────────────────────────────────────────────────────────────
        await seed_cod_receivable(10000)
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h,
            json={"bank_amount": 8000, "bank_account_id": bank_id,
                  "shipping_cost": 2000,
                  "notes": "تحويل + خصم شحن"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["settlement_total"] == 10000
        assert body["remaining_cod_balance"] == 0
        gid = body["txn_group_id"]
        rows = await db.general_ledger.find(
            {"txn_group_id": gid}, {"_id": 0}).to_list(20)
        # 3 legs: bank debit + shipping expense debit + courier credit
        assert len(rows) == 3
        leg_amts = {r["entity_type"]: r["amount"] for r in rows}
        assert leg_amts["bank"] == 8000
        assert leg_amts["expense"] == 2000
        assert leg_amts["courier"] == 10000

        # ────────────────────────────────────────────────────────────
        # Scenario 3 — Partial transfer + COD fee + other fees.
        # COD = 10,000 → bank 7,500 + cod_fee 300 + other_fees 2,200
        # ────────────────────────────────────────────────────────────
        await seed_cod_receivable(10000)
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h,
            json={"bank_amount": 7500, "bank_account_id": bank_id,
                  "cod_fee": 300,
                  "other_fees": 2200,
                  "other_fees_category": "gateway_fees"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["settlement_total"] == 10000
        # 4 legs
        rows = await db.general_ledger.find(
            {"txn_group_id": body["txn_group_id"]}, {"_id": 0}).to_list(20)
        assert len(rows) == 4
        # Expense legs grouped by entity_id
        exp = {r["entity_id"]: r["amount"]
               for r in rows if r["entity_type"] == "expense"}
        assert exp["cod_fees"] == 300
        assert exp["gateway_fees"] == 2200

        # ────────────────────────────────────────────────────────────
        # Scenario 4 — All four legs + Cash account as recipient.
        # COD = 10,000 → cash 5,000 + shipping 2,200 + cod_fee 300 +
        #                other_fees 500 (= 8,000 settled; 2,000 stays).
        # ────────────────────────────────────────────────────────────
        await seed_cod_receivable(10000)
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h,
            json={"bank_amount": 5000, "bank_account_id": cash_id,
                  "shipping_cost": 2200, "cod_fee": 300,
                  "other_fees": 500,
                  "other_fees_category": "bank_fees"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["settlement_total"] == 8000
        assert body["previous_cod_balance"] == 10000
        # 2,000 should remain on the courier.
        assert body["remaining_cod_balance"] == 2000

        # The bank-leg used a CASH account → make sure it still
        # debited entity_type="bank" with the cash account's id.
        rows = await db.general_ledger.find(
            {"txn_group_id": body["txn_group_id"]}, {"_id": 0}).to_list(20)
        bank_leg = [x for x in rows if x["entity_type"] == "bank"][0]
        assert bank_leg["entity_id"] == cash_id
        assert bank_leg["amount"] == 5000

        # ════════════════════════════════════════════════════════════
        # Negative scenarios
        # ════════════════════════════════════════════════════════════
        # — Over-settlement rejected (2,000 left, try to settle 3,000)
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h,
            json={"bank_amount": 3000, "bank_account_id": bank_id},
        )
        assert r.status_code == 400
        assert "أكبر من رصيد" in r.json()["detail"]

        # — Empty payload rejected
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h, json={},
        )
        assert r.status_code == 400
        assert "أدخل قيمة واحدة" in r.json()["detail"]

        # — bank_amount > 0 without bank_account_id rejected
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h, json={"bank_amount": 100},
        )
        assert r.status_code == 400
        assert "اختر حساب الإيداع" in r.json()["detail"]

        # — bank_account_id of payment_platform type rejected
        pp_id = str(uuid.uuid4())
        await db.accounts.insert_one({
            "id": pp_id, "user_id": uid,
            "account_type": "payment_platform",
            "name": "تابي", "current_balance": 0,
        })
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h,
            json={"bank_amount": 100, "bank_account_id": pp_id},
        )
        assert r.status_code == 400
        assert "بنك" in r.json()["detail"]

        # — other_fees > 0 without category rejected
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h, json={"other_fees": 50},
        )
        assert r.status_code == 400
        assert "فئة المصاريف" in r.json()["detail"]

        # — Unknown courier → 404
        r = await client.post(
            f"/api/accounting/couriers/{uuid.uuid4()}/cod-settle",
            headers=h,
            json={"bank_amount": 10, "bank_account_id": bank_id},
        )
        assert r.status_code == 404

        # — Settle the last remainder (2,000) to confirm it can be closed
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-settle",
            headers=h,
            json={"bank_amount": 2000, "bank_account_id": bank_id,
                  "notes": "إغلاق المتبقي"},
        )
        assert r.status_code == 200
        assert r.json()["remaining_cod_balance"] == 0
