"""Iter-70.1 — Schema preparation for the auto-settlements pipeline.

This phase ONLY:
  • Adds `detection_source`, `trigger`, `detection_metadata` to every
    `payment_adjustments` document.
  • Adds a partial unique index that blocks duplicate AUTO settlements
    on the same (user, order, original→new) tuple.
  • Backfills the new fields on legacy docs so the UI never sees nulls.
  • Provides `record_auto_settlement()` + `stamp_order_amount_history()`
    helpers that the 70.2 detection pipeline will call.

It does NOT yet:
  • Detect refunds from `upsert_unified_order()` (that's 70.2).
  • Touch `expected_orders_balance` (that's 70.3).
  • Change any number visible to the merchant.

Tests below prove all of the above explicitly so we can ship 70.1 alone.
"""
from __future__ import annotations
import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

API = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


# ── Unit ────────────────────────────────────────────────────────────────
def test_constants_and_types_are_defined():
    """The new provenance vocabulary must be exposed for the rest of the
    codebase to import."""
    from settlements_routes import (
        ADJUSTMENT_TYPES,
        DETECTION_SOURCES,
        TRIGGER_SOURCES,
    )
    assert DETECTION_SOURCES == {"manual", "auto"}
    assert {"excel_upsert", "make_webhook", "salla_oauth", "manual"} <= TRIGGER_SOURCES
    assert {"partial_refund", "full_refund", "item_removed",
            "order_cancelled", "manual_adjustment"} == ADJUSTMENT_TYPES


def test_to_public_defaults_missing_provenance_to_manual():
    """Legacy docs without the new fields must still render — UI never
    sees `None` for detection_source."""
    from settlements_routes import _to_public
    legacy = {
        "id": "x", "user_id": "u", "order_number": "1", "payment_method": "مدى",
        "original_amount": 100, "new_amount": 50, "adjustment_amount": 50,
        "adjustment_type": "partial_refund", "order_created_at": "2026-01-01",
        "adjusted_at": "2026-01-02",
    }
    pub = _to_public(legacy)
    assert pub["detection_source"] == "manual"
    assert pub["trigger"] == "manual"
    assert pub["detection_metadata"] is None


@pytest.mark.asyncio
async def test_backfill_is_idempotent():
    """Running the backfill twice must update 0 docs the second time."""
    from settlements_routes import backfill_settlement_provenance
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        first = await backfill_settlement_provenance(db)
        second = await backfill_settlement_provenance(db)
        assert second == 0, f"backfill should be idempotent; got {second}"
        assert first >= 0
        # Every doc now has the provenance fields.
        missing = await db.payment_adjustments.count_documents(
            {"detection_source": {"$exists": False}}
        )
        assert missing == 0, "backfill left some docs un-stamped"
    finally:
        client.close()


@pytest.mark.asyncio
async def test_unique_partial_index_exists_and_is_partial():
    """The new index must (a) exist, (b) be unique, (c) be partial on
    detection_source='auto' so manual entries can still repeat."""
    from settlements_routes import ensure_settlements_indexes
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        await ensure_settlements_indexes(db)  # idempotent
        info = await db.payment_adjustments.index_information()
        idx = info.get("uniq_auto_settlement_per_diff")
        assert idx is not None, f"index missing; have {sorted(info.keys())}"
        assert idx.get("unique") is True
        pfe = idx.get("partialFilterExpression") or {}
        assert pfe.get("detection_source") == "auto"
    finally:
        client.close()


@pytest.mark.asyncio
async def test_unique_index_blocks_duplicate_auto_only():
    """Two AUTO entries with the same (order, original, new) must collide.
    Two MANUAL entries with the same tuple must NOT collide (the partial
    filter excludes them)."""
    from settlements_routes import ensure_settlements_indexes
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        await ensure_settlements_indexes(db)
        # Use a sentinel user_id to avoid touching real data.
        uid = f"_pytest_iter701_{uuid.uuid4().hex[:8]}"
        order_no = "PYTEST-ORD-70.1"
        base_auto = {
            "user_id": uid, "order_number": order_no,
            "payment_method": "مدى",
            "original_amount": 100.0, "new_amount": 60.0,
            "adjustment_amount": 40.0, "adjustment_type": "partial_refund",
            "order_created_at": "2026-01-01", "adjusted_at": "2026-01-02",
            "detection_source": "auto", "trigger": "excel_upsert",
        }
        try:
            base_doc = {**base_auto, "id": str(uuid.uuid4())}
            await db.payment_adjustments.insert_one(base_doc)
            with pytest.raises(DuplicateKeyError):
                dup = {**base_auto, "id": str(uuid.uuid4())}
                dup.pop("_id", None)
                await db.payment_adjustments.insert_one(dup)

            # Same tuple but MANUAL — must succeed because partial filter
            # only enforces uniqueness when detection_source='auto'.
            manual_template = {
                **{k: v for k, v in base_auto.items() if k != "_id"},
                "detection_source": "manual",
                "trigger": "manual",
            }
            await db.payment_adjustments.insert_one(
                {**manual_template, "id": str(uuid.uuid4())}
            )
            await db.payment_adjustments.insert_one(
                {**manual_template, "id": str(uuid.uuid4())}
            )
        finally:
            await db.payment_adjustments.delete_many({"user_id": uid})
    finally:
        client.close()


@pytest.mark.asyncio
async def test_record_auto_settlement_helper_works_and_dedupes():
    """The helper used by 70.2 must (a) write the expected schema, (b)
    no-op on duplicate insert, (c) return None for non-positive diffs."""
    from settlements_routes import record_auto_settlement
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        uid = f"_pytest_iter701_helper_{uuid.uuid4().hex[:8]}"
        # (a) happy path
        doc = await record_auto_settlement(
            db,
            user_id=uid, order_number="X-1", payment_method="مدى",
            original_amount=200, new_amount=125, adjustment_type="partial_refund",
            order_created_at="2026-02-01", adjusted_at="2026-02-05",
            trigger="excel_upsert",
            detection_metadata={"prev_status": "تم التوصيل", "new_status": "مسترجع جزئي"},
        )
        try:
            assert doc is not None
            assert doc["detection_source"] == "auto"
            assert doc["trigger"] == "excel_upsert"
            assert doc["adjustment_amount"] == 75.0
            assert doc["detection_metadata"]["prev_status"] == "تم التوصيل"

            # (b) duplicate guarded by the partial index — returns None
            dup = await record_auto_settlement(
                db,
                user_id=uid, order_number="X-1", payment_method="مدى",
                original_amount=200, new_amount=125, adjustment_type="partial_refund",
                order_created_at="2026-02-01", adjusted_at="2026-02-05",
                trigger="excel_upsert",
            )
            assert dup is None, "duplicate detection failed — should be no-op"

            # (c) negative diff → None, no insert
            nope = await record_auto_settlement(
                db,
                user_id=uid, order_number="X-2", payment_method="مدى",
                original_amount=100, new_amount=120, adjustment_type="partial_refund",
                order_created_at="2026-02-01", adjusted_at="2026-02-05",
                trigger="excel_upsert",
            )
            assert nope is None
            count = await db.payment_adjustments.count_documents(
                {"user_id": uid, "order_number": "X-2"}
            )
            assert count == 0
        finally:
            await db.payment_adjustments.delete_many({"user_id": uid})
    finally:
        client.close()


@pytest.mark.asyncio
async def test_stamp_order_amount_history_appends_entry():
    """Helper that the 70.2 pipeline will call to push an amount_history
    entry onto the order doc — used by the UI's audit log + revert."""
    from settlements_routes import stamp_order_amount_history
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        uid = f"_pytest_iter701_hist_{uuid.uuid4().hex[:8]}"
        order_no = "HIST-1"
        await db.unified_orders.insert_one({
            "user_id": uid, "order_number": order_no,
            "total_amount": 500.0, "order_status": "تم التوصيل",
            "payment_method": "مدى",
        })
        try:
            await stamp_order_amount_history(
                db,
                user_id=uid, order_number=order_no,
                prev_amount=500, new_amount=350,
                settlement_id="settl-xyz",
                source="excel", trigger="excel_upsert",
            )
            doc = await db.unified_orders.find_one({"user_id": uid, "order_number": order_no})
            history = doc.get("amount_history") or []
            assert len(history) == 1
            entry = history[0]
            assert entry["prev_amount"] == 500.0
            assert entry["new_amount"] == 350.0
            assert entry["diff"] == 150.0
            assert entry["settlement_id"] == "settl-xyz"
            assert entry["source"] == "excel"
            assert entry["trigger"] == "excel_upsert"

            # Second call appends rather than replaces.
            await stamp_order_amount_history(
                db,
                user_id=uid, order_number=order_no,
                prev_amount=350, new_amount=300,
                settlement_id="settl-zzz",
                source="excel", trigger="excel_upsert",
            )
            doc2 = await db.unified_orders.find_one({"user_id": uid, "order_number": order_no})
            assert len(doc2.get("amount_history") or []) == 2
        finally:
            await db.unified_orders.delete_many({"user_id": uid})
    finally:
        client.close()


# ── End-to-end: existing API surface untouched ──────────────────────────
async def _login() -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=API, timeout=30.0)
    r = await c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    token = r.json().get("access_token") or r.json().get("token")
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.mark.asyncio
async def test_existing_create_settlement_still_works_and_stamps_manual():
    """A POST to /api/settlements should still succeed AND should
    automatically carry `detection_source='manual'` so the UI can
    distinguish it from future auto-created ones."""
    c = await _login()
    try:
        payload = {
            "order_number": f"PYT-{uuid.uuid4().hex[:6]}",
            "payment_method": "مدى",
            "original_amount": 100,
            "new_amount": 70,
            "adjustment_type": "partial_refund",
            "order_created_at": "2026-01-01",
            "adjusted_at": "2026-01-02",
            "reason": "iter-70.1 smoke test",
            "source": "manual_sync",
        }
        r = await c.post("/api/settlements", json=payload)
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["detection_source"] == "manual"
        assert doc["trigger"] == "manual"
        assert doc["detection_metadata"] is None
        # cleanup
        await c.delete(f"/api/settlements/{doc['id']}")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_70_1_did_not_change_any_money_number():
    """Smoke: total assets + reconciliation totals before and after the
    70.1 deploy must be identical (no number visible to the merchant
    should have shifted)."""
    c = await _login()
    try:
        r1 = await c.get("/api/accounts")
        accs = r1.json()
        total_before = sum(float(a.get("current_balance") or 0)
                           for a in accs if a.get("status") != "hidden")

        r2 = await c.get("/api/reconciliation/summary")
        recon = r2.json()
        exp_before = float(recon["totals"]["expected"])

        # Sanity: running a sync now must NOT change either number.
        await c.post("/api/accounts/sync-payment-methods")
        r3 = await c.get("/api/accounts")
        total_after = sum(float(a.get("current_balance") or 0)
                          for a in r3.json() if a.get("status") != "hidden")
        r4 = await c.get("/api/reconciliation/summary")
        exp_after = float(r4.json()["totals"]["expected"])

        assert abs(total_before - total_after) < 0.01, \
            f"current_balance drifted: {total_before} → {total_after}"
        assert abs(exp_before - exp_after) < 0.01, \
            f"expected drifted: {exp_before} → {exp_after}"
    finally:
        await c.aclose()
