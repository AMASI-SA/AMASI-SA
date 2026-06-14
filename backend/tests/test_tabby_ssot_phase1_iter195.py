"""Iter-195 — Tabby SSOT Phase 1 Quick Fix.

Validates that the live balance displayed for BNPL accounts
(Tabby/Tamara) is sourced from BNPL SSOT — NOT the stale
`accounts.current_balance` field — across every endpoint that
shows or validates balances.

Forensic context: production was reading Tabby's raw
`current_balance` (= −47,351.51) on some pages while other pages
showed the BNPL formula value (+13,202.46). 60k SAR drift.

Tests are consolidated into ONE async function to avoid the
project-wide pytest-asyncio loop-close bug that affects every
HTTP-based test file with > 1 async function.
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


async def _seed_user_and_tabby(client, db, raw_current_balance: float):
    """Helper — register user, create Tabby account with a STALE
    `current_balance`, insert payment_transactions so the BNPL
    formula returns a specific value DIFFERENT from raw."""
    email = f"tabby195-{os.urandom(3).hex()}@test.com"
    r = await client.post("/api/auth/register", json={
        "name": "Tabby Tester", "email": email, "password": "pass1234",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["access_token"]
    uid = body["id"]
    h = {"Authorization": f"Bearer {token}"}

    tabby_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.accounts.insert_one({
        "id": tabby_id, "user_id": uid,
        "account_type": "payment_platform",
        "name": "تابي",
        "provider_name": "تابي",
        "normalized_payment_method": "tabby",
        "current_balance": raw_current_balance,
        "opening_balance": 0,
        "status": "active",
        "created_at": now, "updated_at": now,
    })
    await db.payment_transactions.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "provider": "tabby",
        "provider_id": "txn_" + uuid.uuid4().hex[:12],
        "order_reference_id": "ord_1",
        "order_number": "1001",
        "amount": 1000.0,
        "currency": "SAR",
        "status": "closed",
        "created_at_provider": "2026-01-15T10:00:00Z",
        "billing_eligible_at": "2026-01-22T10:00:00Z",
        "created_at": now,
    })
    return uid, h, tabby_id


@pytest.mark.asyncio
async def test_tabby_phase1_quick_fix_complete():
    """Consolidated Phase-1 assertions in a single event loop.

    Covers:
        1. resolve_live_balance classifies Tabby as bnpl_ssot
        2. resolve_live_balance keeps a non-BNPL bank as
           current_balance
        3. GET /api/accounts returns balance_source=bnpl_ssot
        4. The displayed current_balance differs from the stale field
        5. GET /api/accounts/summary aggregates with BNPL SSOT
        6. GET /api/accounting/cash-accounts-with-balances applies
           the override (was the primary leak)
        7. Read endpoints do NOT mutate any DB document
    """
    from balance_resolver import resolve_live_balance

    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        uid, h, tabby_id = await _seed_user_and_tabby(
            client, db, raw_current_balance=-47351.51,
        )

        # ── Also seed a non-BNPL bank account (regression check)
        bank_id = str(uuid.uuid4())
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid,
            "account_type": "bank", "name": "بنك الراجحي",
            "current_balance": 1500.0, "status": "active",
        })

        try:
            # ─── (1) Resolver classifies Tabby as bnpl_ssot ─────
            acc = await db.accounts.find_one(
                {"id": tabby_id, "user_id": uid}, {"_id": 0},
            )
            res = await resolve_live_balance(
                db, user_id=uid, account=acc,
            )
            assert res["source"] == "bnpl_ssot", (
                f"[1] Expected bnpl_ssot, got {res['source']}"
            )
            assert res["raw_balance"] == -47351.51
            assert res["balance"] != res["raw_balance"], (
                "[1b] BNPL SSOT and raw must differ"
            )
            assert res["balance"] > -10_000, (
                f"[1c] Expected near +1000 net of fees, "
                f"got {res['balance']}"
            )
            assert res["components"] is not None

            # ─── (2) Resolver classifies plain bank correctly ──
            bank_acc = await db.accounts.find_one({"id": bank_id})
            bres = await resolve_live_balance(
                db, user_id=uid, account=bank_acc,
            )
            assert bres["source"] == "current_balance", (
                f"[2] Bank without opening_balance should resolve to "
                f"current_balance, got {bres['source']}"
            )
            assert bres["balance"] == 1500.0

            # ─── (3) GET /api/accounts — Tabby badge ───────────
            r = await client.get(
                "/api/accounts?type=payment_platform", headers=h,
            )
            assert r.status_code == 200, r.text
            accounts = r.json()
            assert len(accounts) == 1
            tabby = accounts[0]
            assert tabby["id"] == tabby_id
            assert tabby.get("balance_source") == "bnpl_ssot", (
                f"[3] balance_source={tabby.get('balance_source')!r}"
            )
            displayed = float(tabby["current_balance"])
            assert displayed != -47351.51, (
                "[3b] API still returns the stale field"
            )
            assert "current_balance_ledger" in tabby
            assert tabby["current_balance_ledger"] == -47351.51, (
                "[3c] current_balance_ledger must preserve the "
                "original stored value for audit"
            )
            assert "bnpl_balance_components" in tabby

            # ─── (4) GET /api/accounts/summary — aggregate ──────
            r = await client.get("/api/accounts/summary", headers=h)
            assert r.status_code == 200
            data = r.json()
            pp_total = float(data["by_type"]["payment_platform"])
            assert pp_total != -47351.51, (
                f"[4] Summary uses stale field: pp_total={pp_total}"
            )
            assert pp_total > -10_000

            # ─── (5) Unified Entry — cash-accounts-with-balances
            r = await client.get(
                "/api/accounting/cash-accounts-with-balances",
                headers=h,
            )
            assert r.status_code == 200, r.text
            ax = r.json()["accounts"]
            tabby_x = next(a for a in ax if a["id"] == tabby_id)
            assert tabby_x["balance_source"] == "bnpl_ssot", (
                f"[5] Unified Entry leaks stale value, "
                f"source={tabby_x['balance_source']}"
            )
            assert float(tabby_x["live_balance"]) != -47351.51
            # current_balance still echoes the raw value (for audit)
            assert float(tabby_x["current_balance"]) == -47351.51

            # ─── (6) Read endpoints never mutate DB ─────────────
            before_tabby = await db.accounts.find_one(
                {"id": tabby_id}, {"_id": 0},
            )
            before_pt = await db.payment_transactions \
                .count_documents({"user_id": uid})
            before_gl = await db.general_ledger \
                .count_documents({"user_id": uid})

            for _ in range(3):
                rA = await client.get("/api/accounts", headers=h)
                rB = await client.get(
                    "/api/accounts/summary", headers=h)
                rC = await client.get(
                    "/api/accounting/cash-accounts-with-balances",
                    headers=h,
                )
                assert rA.status_code == 200
                assert rB.status_code == 200
                assert rC.status_code == 200

            after_tabby = await db.accounts.find_one(
                {"id": tabby_id}, {"_id": 0},
            )
            after_pt = await db.payment_transactions \
                .count_documents({"user_id": uid})
            after_gl = await db.general_ledger \
                .count_documents({"user_id": uid})

            assert (
                before_tabby["current_balance"]
                == after_tabby["current_balance"]
            ), (
                "[6] accounts.current_balance was mutated — "
                "violates Phase-1 read-only guarantee"
            )
            assert before_pt == after_pt
            assert before_gl == after_gl
        finally:
            await db.accounts.delete_many({"user_id": uid})
            await db.payment_transactions.delete_many(
                {"user_id": uid})
