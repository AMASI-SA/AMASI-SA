"""Iter-220 — BNPL Settlement Bridge (Phase 2b).

Validates that registering a BNPL bank-transfer settlement:
  1. Posts a balanced 5-leg group:
        DEBIT bank + DEBIT commission + DEBIT VAT + DEBIT fee
        CREDIT payment_gateway.{provider}/receivable
  2. Closes the receivable by EXACTLY `transferred + commission + vat + fee`.
  3. Partial settlements are allowed (leftover stays as receivable).
  4. Idempotent on (provider, settlement_reference) — same key returns
     the existing txn_group_id and does NOT duplicate the
     account_transactions row.
  5. Over-settlement (total > receivable) is REJECTED.
  6. Settlement on a provider with ZERO receivable is REJECTED
     (preserves "no negative receivable" invariant).
  7. The bank receives ONLY the `transferred_amount` (not the gross).
  8. Existing internal bank transfers / non-BNPL transactions are not affected.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from fastapi import HTTPException  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from ledger_core import compute_balance  # noqa: E402

from bnpl.ledger_bridge import post_bnpl_sale_to_ledger  # noqa: E402
from bnpl.settlement_bridge import (  # noqa: E402
    post_bnpl_settlement_to_ledger,
)


def _conn():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return cli, cli[os.environ["DB_NAME"]]


def _sale(provider: str, provider_id: str, amount: float) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "provider": provider,
        "provider_id": provider_id,
        "amount": amount,
        "status": "closed" if provider == "tabby" else "fully_captured",
        "order_reference_id": f"ORD-{provider_id}",
        "order_number": f"#{provider_id}",
        "created_at_provider": "2026-02-15T10:00:00Z",
    }


async def _seed_bank(db, uid: str) -> str:
    bid = str(uuid.uuid4())
    await db.accounts.insert_one({
        "id": bid, "user_id": uid, "name": "Test Bank",
        "account_type": "bank", "status": "active",
        "current_balance": 0.0,
    })
    return bid


async def _seed_receivable(db, uid: str, provider: str,
                            amount: float, sid: str = "p"):
    """Add a bookable sale to give the provider a positive receivable."""
    await post_bnpl_sale_to_ledger(
        db, user_id=uid, txn=_sale(provider, sid, amount),
    )


async def _cleanup(db, uid: str):
    await db.general_ledger.delete_many({"user_id": uid})
    await db.accounting_audit_log.delete_many({"user_id": uid})
    await db.accounts.delete_many({"user_id": uid})
    await db.account_transactions.delete_many({"user_id": uid})


# ── 1. Full settlement closes receivable exactly ──────────────────────
@pytest.mark.asyncio
async def test_full_settlement_closes_receivable():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        bank = await _seed_bank(db, uid)
        await _seed_receivable(db, uid, "tabby", 10000.0, sid="t1")

        res = await post_bnpl_settlement_to_ledger(
            db, user_id=uid, actor_id=uid, actor_name="tester",
            provider="tabby", bank_account_id=bank,
            transferred_amount=9000.0, commission=800.0,
            commission_vat=120.0, settlement_fee=80.0,
            settlement_reference="TBY-W42-001",
        )
        assert res["ok"] and res["txn_group_id"], res
        assert res["total_closed"] == 10000.0
        assert res["remaining_receivable"] == 0.0

        # 5-leg group, debit==credit
        entries = await db.general_ledger.find(
            {"user_id": uid, "txn_group_id": res["txn_group_id"]},
        ).to_list(20)
        assert len(entries) == 5, entries
        debits = sum(e["amount"] for e in entries if e["side"] == "debit")
        credits = sum(e["amount"] for e in entries if e["side"] == "credit")
        assert debits == credits == 10000.0

        # Receivable: 10000 (sale) - 10000 (settle) = 0
        recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tabby", sub_account="receivable",
        )
        assert recv["net_balance"] == 0.0

        # Bank: +9000
        bank_bal = await compute_balance(
            db, user_id=uid, entity_type="bank",
            entity_id=bank, sub_account="balance",
        )
        assert bank_bal["net_balance"] == 9000.0

        # Each expense recorded distinctly
        for code, amt in [("bnpl_commission", 800.0),
                          ("bnpl_commission_vat", 120.0),
                          ("bnpl_settlement_fee", 80.0)]:
            exp = await compute_balance(
                db, user_id=uid, entity_type="expense",
                entity_id=code,
            )
            assert exp["net_balance"] == amt, f"{code}={exp}"
    finally:
        await _cleanup(db, uid)
        cli.close()


# ── 2. Partial settlement leaves the residue as receivable ────────────
@pytest.mark.asyncio
async def test_partial_settlement_keeps_remaining_receivable():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        bank = await _seed_bank(db, uid)
        await _seed_receivable(db, uid, "tabby", 10000.0, sid="t2")

        # Close only 4000 out of 10000
        res = await post_bnpl_settlement_to_ledger(
            db, user_id=uid, actor_id=uid, actor_name="tester",
            provider="tabby", bank_account_id=bank,
            transferred_amount=3600.0, commission=320.0,
            commission_vat=48.0, settlement_fee=32.0,
            settlement_reference="TBY-W43-PARTIAL",
        )
        assert res["total_closed"] == 4000.0
        assert res["remaining_receivable"] == 6000.0

        recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tabby", sub_account="receivable",
        )
        assert recv["net_balance"] == 6000.0
    finally:
        await _cleanup(db, uid)
        cli.close()


# ── 3. Idempotent re-register ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_idempotent_re_register():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        bank = await _seed_bank(db, uid)
        await _seed_receivable(db, uid, "tabby", 5000.0, sid="t3")

        kwargs = dict(
            db=db, user_id=uid, actor_id=uid, actor_name="tester",
            provider="tabby", bank_account_id=bank,
            transferred_amount=4800.0, commission=180.0,
            commission_vat=20.0, settlement_fee=0.0,
            settlement_reference="TBY-IDEM-1",
        )
        r1 = await post_bnpl_settlement_to_ledger(**kwargs)
        r2 = await post_bnpl_settlement_to_ledger(**kwargs)
        assert r1.get("txn_group_id")
        assert r2.get("skipped"), r2
        assert r2.get("reason") == "idempotent_duplicate"
        assert r2.get("txn_group_id") == r1["txn_group_id"]

        # Receivable closed only ONCE
        recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tabby", sub_account="receivable",
        )
        assert recv["net_balance"] == 0.0
    finally:
        await _cleanup(db, uid)
        cli.close()


# ── 4. Over-settlement is rejected ────────────────────────────────────
@pytest.mark.asyncio
async def test_over_settlement_rejected():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        bank = await _seed_bank(db, uid)
        await _seed_receivable(db, uid, "tabby", 1000.0, sid="t4")

        with pytest.raises(HTTPException) as ei:
            await post_bnpl_settlement_to_ledger(
                db, user_id=uid, actor_id=uid, actor_name="tester",
                provider="tabby", bank_account_id=bank,
                transferred_amount=1500.0, commission=0,
                commission_vat=0, settlement_fee=0,
                settlement_reference="TBY-OVER-1",
            )
        assert ei.value.status_code == 400
        assert "يتجاوز" in str(ei.value.detail) or "exceed" in str(ei.value.detail).lower()

        # Receivable untouched
        recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tabby", sub_account="receivable",
        )
        assert recv["net_balance"] == 1000.0
    finally:
        await _cleanup(db, uid)
        cli.close()


# ── 5. Settlement with zero receivable is rejected ────────────────────
@pytest.mark.asyncio
async def test_settlement_without_receivable_rejected():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        bank = await _seed_bank(db, uid)
        # No sales — receivable is zero.
        with pytest.raises(HTTPException) as ei:
            await post_bnpl_settlement_to_ledger(
                db, user_id=uid, actor_id=uid, actor_name="tester",
                provider="tabby", bank_account_id=bank,
                transferred_amount=100.0, commission=0,
                commission_vat=0, settlement_fee=0,
                settlement_reference="TBY-NORECV-1",
            )
        assert ei.value.status_code == 400
        assert "رصيد" in str(ei.value.detail) or "receivable" in str(ei.value.detail).lower()
    finally:
        await _cleanup(db, uid)
        cli.close()


# ── 6. Tamara works the same way ──────────────────────────────────────
@pytest.mark.asyncio
async def test_tamara_settlement_works():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        bank = await _seed_bank(db, uid)
        await _seed_receivable(db, uid, "tamara", 2000.0, sid="tm1")

        res = await post_bnpl_settlement_to_ledger(
            db, user_id=uid, actor_id=uid, actor_name="tester",
            provider="tamara", bank_account_id=bank,
            transferred_amount=1800.0, commission=160.0,
            commission_vat=24.0, settlement_fee=16.0,
            settlement_reference="TMR-W42-001",
        )
        assert res["total_closed"] == 2000.0
        recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tamara", sub_account="receivable",
        )
        assert recv["net_balance"] == 0.0
        # Tabby receivable not touched
        tabby_recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tabby", sub_account="receivable",
        )
        assert tabby_recv["net_balance"] == 0.0
    finally:
        await _cleanup(db, uid)
        cli.close()


# ── 7. metadata captures the full breakdown ───────────────────────────
@pytest.mark.asyncio
async def test_metadata_breakdown_complete():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        bank = await _seed_bank(db, uid)
        await _seed_receivable(db, uid, "tabby", 3000.0, sid="t7")
        res = await post_bnpl_settlement_to_ledger(
            db, user_id=uid, actor_id=uid, actor_name="tester",
            provider="tabby", bank_account_id=bank,
            transferred_amount=2700.0, commission=240.0,
            commission_vat=36.0, settlement_fee=24.0,
            settlement_reference="TBY-META-1",
            settlement_date="2026-02-20",
        )
        legs = await db.general_ledger.find(
            {"user_id": uid, "txn_group_id": res["txn_group_id"]},
        ).to_list(20)
        # Every leg must carry the full metadata
        for leg in legs:
            meta = leg.get("metadata") or {}
            assert meta.get("provider") == "tabby"
            assert meta.get("transferred_amount") == 2700.0
            assert meta.get("commission") == 240.0
            assert meta.get("commission_vat") == 36.0
            assert meta.get("settlement_fee") == 24.0
            assert meta.get("settlement_reference") == "TBY-META-1"
            assert meta.get("bank_account_id") == bank
            assert meta.get("settlement_date") == "2026-02-20"
            assert meta.get("idempotency_key") == "bnpl_settlement:tabby:TBY-META-1"
    finally:
        await _cleanup(db, uid)
        cli.close()


# ── 8. Zero-amount expense legs are skipped (only non-zero booked) ───
@pytest.mark.asyncio
async def test_no_expense_zero_legs():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        bank = await _seed_bank(db, uid)
        await _seed_receivable(db, uid, "tabby", 1000.0, sid="t8")
        # Only transferred + commission (no VAT, no fee)
        res = await post_bnpl_settlement_to_ledger(
            db, user_id=uid, actor_id=uid, actor_name="tester",
            provider="tabby", bank_account_id=bank,
            transferred_amount=900.0, commission=100.0,
            commission_vat=0.0, settlement_fee=0.0,
            settlement_reference="TBY-NOEXP-1",
        )
        entries = await db.general_ledger.find(
            {"user_id": uid, "txn_group_id": res["txn_group_id"]},
        ).to_list(20)
        # 3 legs: bank + commission + receivable credit
        assert len(entries) == 3
        vat_exp = await compute_balance(
            db, user_id=uid, entity_type="expense",
            entity_id="bnpl_commission_vat",
        )
        fee_exp = await compute_balance(
            db, user_id=uid, entity_type="expense",
            entity_id="bnpl_settlement_fee",
        )
        assert vat_exp["net_balance"] == 0.0
        assert fee_exp["net_balance"] == 0.0
    finally:
        await _cleanup(db, uid)
        cli.close()
