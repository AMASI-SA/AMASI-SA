"""Iter-219 — Tabby/Tamara → general_ledger bridge (Phase 2a).

Validates:
  1. A bookable Tabby sale produces a BALANCED double-entry
     (DEBIT payment_gateway.tabby/receivable, CREDIT revenue.bnpl_sales).
  2. Re-running the bridge for the SAME provider_id is idempotent
     (no second ledger entry).
  3. A Tabby refund flips the sign correctly
     (DEBIT bnpl_sales, CREDIT payment_gateway.tabby/receivable).
  4. Refund whose sale was NEVER booked is SKIPPED — preserves
     "no historical backfill" invariant.
  5. Cutoff env (BNPL_BRIDGE_CUTOFF_ISO) blocks pre-cutoff sales
     from hitting the ledger.
  6. Non-bookable statuses (e.g. "created", "rejected") are SKIPPED.
  7. Tamara sales obey the same contract.
  8. Cross-provider isolation — Tabby and Tamara ledger sub-accounts
     don't bleed into each other.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from ledger_core import compute_balance  # noqa: E402

from bnpl.ledger_bridge import (  # noqa: E402
    post_bnpl_sale_to_ledger,
    post_bnpl_refund_to_ledger,
)


def _conn():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return cli, cli[os.environ["DB_NAME"]]


def _tabby_sale(provider_id: str, amount: float,
                created_at: str = "2026-02-15T10:00:00Z",
                status: str = "closed") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "provider": "tabby",
        "provider_id": provider_id,
        "amount": amount,
        "status": status,
        "order_reference_id": f"ORD-{provider_id}",
        "order_number": f"#{provider_id}",
        "created_at_provider": created_at,
    }


def _tabby_refund(provider_id: str, refund_id: str,
                  amount: float,
                  refunded_at: str = "2026-02-16T11:00:00Z") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "provider": "tabby",
        "provider_payment_id": provider_id,
        "provider_refund_id": refund_id,
        "amount": amount,
        "order_reference_id": f"ORD-{provider_id}",
        "refunded_at": refunded_at,
    }


def _tamara_sale(provider_id: str, amount: float,
                 created_at: str = "2026-02-15T10:00:00Z",
                 status: str = "fully_captured") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "provider": "tamara",
        "provider_id": provider_id,
        "amount": amount,
        "status": status,
        "order_reference_id": f"ORD-{provider_id}",
        "order_number": f"#{provider_id}",
        "created_at_provider": created_at,
    }


# ── 1. Bookable Tabby sale → balanced ledger group ────────────────────
@pytest.mark.asyncio
async def test_tabby_sale_creates_balanced_entry():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        txn = _tabby_sale("pay_1", 500.0)
        res = await post_bnpl_sale_to_ledger(db, user_id=uid, txn=txn)
        assert res["ok"] and "txn_group_id" in res, res

        entries = await db.general_ledger.find(
            {"user_id": uid, "txn_group_id": res["txn_group_id"]},
        ).to_list(10)
        assert len(entries) == 2, entries
        debits = sum(e["amount"] for e in entries if e["side"] == "debit")
        credits = sum(e["amount"] for e in entries if e["side"] == "credit")
        assert debits == credits == 500.0

        recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tabby", sub_account="receivable",
        )
        assert recv["debits"] == 500.0 and recv["credits"] == 0.0
        assert recv["net_balance"] == 500.0

        rev = await compute_balance(
            db, user_id=uid, entity_type="revenue",
            entity_id="bnpl_sales",
        )
        assert rev["credits"] == 500.0 and rev["debits"] == 0.0
    finally:
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        cli.close()


# ── 2. Idempotent re-run ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_tabby_sale_is_idempotent():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        txn = _tabby_sale("pay_2", 250.0)
        r1 = await post_bnpl_sale_to_ledger(db, user_id=uid, txn=txn)
        r2 = await post_bnpl_sale_to_ledger(db, user_id=uid, txn=txn)
        assert r1.get("txn_group_id"), r1
        assert r2.get("skipped"), r2
        assert r2.get("reason") == "idempotent_duplicate"

        entries = await db.general_ledger.count_documents(
            {"user_id": uid},
        )
        assert entries == 2
    finally:
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        cli.close()


# ── 3. Refund flips the entry ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_tabby_refund_flips_signs():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        await post_bnpl_sale_to_ledger(
            db, user_id=uid, txn=_tabby_sale("pay_3", 800.0),
        )
        refund = _tabby_refund("pay_3", "rfd_3", 100.0)
        rres = await post_bnpl_refund_to_ledger(
            db, user_id=uid, refund=refund,
        )
        assert rres.get("txn_group_id"), rres

        recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tabby", sub_account="receivable",
        )
        assert recv["debits"] == 800.0
        assert recv["credits"] == 100.0
        assert recv["net_balance"] == 700.0

        rev = await compute_balance(
            db, user_id=uid, entity_type="revenue",
            entity_id="bnpl_sales",
        )
        assert rev["credits"] == 800.0
        assert rev["debits"] == 100.0
        assert rev["net_balance"] == -700.0
    finally:
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        cli.close()


# ── 4. Refund without an underlying sale is skipped ───────────────────
@pytest.mark.asyncio
async def test_refund_without_sale_is_skipped():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        refund = _tabby_refund("pay_orphan", "rfd_orphan", 50.0)
        res = await post_bnpl_refund_to_ledger(
            db, user_id=uid, refund=refund,
        )
        assert res.get("skipped"), res
        assert res.get("reason") == "underlying_sale_not_in_ledger"

        count = await db.general_ledger.count_documents(
            {"user_id": uid},
        )
        assert count == 0
    finally:
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        cli.close()


# ── 5. Cutoff env blocks historical sales ─────────────────────────────
@pytest.mark.asyncio
async def test_cutoff_blocks_historical(monkeypatch):
    cli, db = _conn()
    uid = str(uuid.uuid4())
    monkeypatch.setenv("BNPL_BRIDGE_CUTOFF_ISO", "2026-02-12T00:00:00Z")
    try:
        historical = _tabby_sale(
            "pay_old", 999.0, created_at="2025-12-01T10:00:00Z",
        )
        res = await post_bnpl_sale_to_ledger(
            db, user_id=uid, txn=historical,
        )
        assert res.get("skipped"), res
        assert res.get("reason") == "before_bridge_cutoff"

        fresh = _tabby_sale(
            "pay_new", 100.0, created_at="2026-02-20T10:00:00Z",
        )
        res2 = await post_bnpl_sale_to_ledger(
            db, user_id=uid, txn=fresh,
        )
        assert res2.get("txn_group_id"), res2
    finally:
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        cli.close()


# ── 6. Non-bookable status is skipped ─────────────────────────────────
@pytest.mark.asyncio
async def test_non_bookable_status_skipped():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        pending = _tabby_sale("pay_pend", 300.0, status="created")
        res = await post_bnpl_sale_to_ledger(
            db, user_id=uid, txn=pending,
        )
        assert res.get("skipped"), res
        assert res.get("reason", "").startswith("status_not_bookable")

        count = await db.general_ledger.count_documents(
            {"user_id": uid},
        )
        assert count == 0
    finally:
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        cli.close()


# ── 7. Tamara sale (separate provider) ────────────────────────────────
@pytest.mark.asyncio
async def test_tamara_sale_books_correctly():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        txn = _tamara_sale("tm_1", 1200.0)
        res = await post_bnpl_sale_to_ledger(db, user_id=uid, txn=txn)
        assert res.get("txn_group_id"), res

        recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tamara", sub_account="receivable",
        )
        assert recv["net_balance"] == 1200.0
        rev = await compute_balance(
            db, user_id=uid, entity_type="revenue",
            entity_id="bnpl_sales",
        )
        assert rev["net_balance"] == -1200.0
    finally:
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        cli.close()


# ── 8. Provider isolation ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_provider_isolation():
    cli, db = _conn()
    uid = str(uuid.uuid4())
    try:
        await post_bnpl_sale_to_ledger(
            db, user_id=uid, txn=_tabby_sale("p_iso_1", 100.0),
        )
        await post_bnpl_sale_to_ledger(
            db, user_id=uid, txn=_tamara_sale("p_iso_2", 250.0),
        )
        tabby_recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tabby", sub_account="receivable",
        )
        tamara_recv = await compute_balance(
            db, user_id=uid, entity_type="payment_gateway",
            entity_id="tamara", sub_account="receivable",
        )
        assert tabby_recv["net_balance"] == 100.0
        assert tamara_recv["net_balance"] == 250.0
    finally:
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        cli.close()
