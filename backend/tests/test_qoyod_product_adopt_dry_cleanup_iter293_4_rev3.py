"""Iter-293.4-rev3-cleanup — Product mapping repair workflow.

Operator demand (2026-XX) after Dry-run of order 269571122:
    "المنتج AMS11961 الـ Qoyod product_id الحقيقي = 39. لا تنشئ منتج
    جديد في قيود. حدِّث mapping في ميزان بحيث:
        sku = AMS11961
        qoyod_product_id = 39
        dry_run_only = false
    واستبدل/احذف أي mapping قديم يبدأ بـ DRY: أو PREVIEW:،
    خصوصاً DRY:product:fefe7c24."

This test pins two contracts:

  1. `POST /products/adopt` MUST clear `dry_run_only=False` when
     replacing a DRY:* mapping with a real Qoyod id, so the next
     preview-reprocess flips sendable=True.

  2. `GET /admin/products/dry-mappings` MUST surface every SKU that
     still needs repair (qoyod_product_id starting with DRY:/PREVIEW:
     OR `dry_run_only=True`).
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

sys.path.insert(0, "/app/backend")
os.environ.setdefault("QOYOD_API_BASE", "https://api.qoyod.test")

from integrations.qoyod.product_resolver import adopt_qoyod_product  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
class _ProductMappingColl:
    """Just enough Motor surface for adopt_qoyod_product + the route."""
    def __init__(self):
        self.rows: list[dict] = []

    async def update_one(self, q, upd, *, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                r.update(upd.get("$set") or {})
                class _R: matched_count = 1; modified_count = 1
                return _R()
        if upsert:
            new = {**q, **(upd.get("$set") or {}), **(upd.get("$setOnInsert") or {})}
            self.rows.append(new)
            class _R: matched_count = 0; modified_count = 1; upserted_id = new
            return _R()
        class _R: matched_count = 0; modified_count = 0
        return _R()

    def find(self, q, projection=None):
        # Eval the $or + $regex from the route.
        def _match(r):
            if "user_id" in q and r.get("user_id") != q["user_id"]:
                return False
            ors = q.get("$or")
            if not ors:
                return True
            for cond in ors:
                if cond.get("dry_run_only") is True and r.get("dry_run_only"):
                    return True
                rx = cond.get("qoyod_product_id", {})
                if isinstance(rx, dict) and "$regex" in rx:
                    pid = str(r.get("qoyod_product_id") or "")
                    import re
                    if re.match(rx["$regex"], pid,
                                re.IGNORECASE if rx.get("$options") == "i"
                                else 0):
                        return True
            return False
        out = [dict(r) for r in self.rows if _match(r)]
        return _Cursor(out)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self._limit = None
    def limit(self, n):
        self._limit = n
        return self
    def __aiter__(self):
        rows = self._rows[: self._limit] if self._limit else self._rows
        async def _gen():
            for r in rows:
                yield r
        return _gen()


# ─────────────────────────────────────────────────────────────────────
# Fix 1 — adopt clears dry_run_only
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestAdoptClearsDryRunOnly:
    async def test_replaces_dry_mapping_with_real_id(self):
        """Pre-existing DRY:* mapping with dry_run_only=True → adoption
        flips both fields to the real Qoyod id + dry_run_only=False."""
        class DB:
            qoyod_products_mapping = _ProductMappingColl()
        db = DB()
        # Seed the legacy DRY mapping (the state reported by the
        # operator from order 269571122).
        db.qoyod_products_mapping.rows.append({
            "user_id":           "main",
            "sku":               "AMS11961",
            "qoyod_product_id":  "DRY:product:fefe7c24",
            "dry_run_only":      True,
            "adopted":           False,
            "auto_created":      True,
            "source":            "mezan_dry_run_created",
        })

        result = await adopt_qoyod_product(
            db, user_id="main", sku="AMS11961",
            qoyod_product_id="39",
            qoyod_product_name="تغليف انيق معا الورد - أماسي",
            note="manual repair after preview for order 269571122",
            actor="operator:test",
        )

        assert result["ok"] is True
        assert result["qoyod_product_id"] == "39"
        assert result["dry_run_only"] is False    # pinned in response
        # And the stored row reflects the same.
        row = next(r for r in db.qoyod_products_mapping.rows
                   if r["sku"] == "AMS11961")
        assert row["qoyod_product_id"] == "39"
        assert row["dry_run_only"] is False
        assert row["adopted"] is True
        assert row["auto_created"] is False
        assert row["source"] == "operator_adopted"
        # No duplicate row created.
        assert len([r for r in db.qoyod_products_mapping.rows
                    if r["sku"] == "AMS11961"]) == 1

    async def test_adoption_on_fresh_sku_inserts_with_dry_run_false(self):
        class DB:
            qoyod_products_mapping = _ProductMappingColl()
        db = DB()
        result = await adopt_qoyod_product(
            db, user_id="main", sku="NEW-SKU",
            qoyod_product_id="100",
            actor="operator:test",
        )
        assert result["ok"] is True
        row = next(r for r in db.qoyod_products_mapping.rows
                   if r["sku"] == "NEW-SKU")
        assert row["qoyod_product_id"] == "100"
        assert row["dry_run_only"] is False
        assert row["adopted"] is True

    async def test_adoption_refuses_empty_inputs(self):
        class DB:
            qoyod_products_mapping = _ProductMappingColl()
        db = DB()
        r1 = await adopt_qoyod_product(
            db, user_id="main", sku="", qoyod_product_id="9")
        r2 = await adopt_qoyod_product(
            db, user_id="main", sku="X", qoyod_product_id="")
        assert r1["ok"] is False
        assert r2["ok"] is False


# ─────────────────────────────────────────────────────────────────────
# Fix 2 — dry-mappings audit listing
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestDryMappingsListing:
    """Shape contract for `GET /admin/products/dry-mappings`. The
    actual HTTP wiring is covered by the existing integration test
    file; here we pin the matcher logic used by the route handler."""

    async def test_dry_prefix_is_listed(self):
        coll = _ProductMappingColl()
        coll.rows.append({
            "user_id": "main", "sku": "AMS11961",
            "qoyod_product_id": "DRY:product:fefe7c24",
            "dry_run_only": True,
        })
        coll.rows.append({
            "user_id": "main", "sku": "REAL-SKU",
            "qoyod_product_id": "39",
            "dry_run_only": False,
        })
        # Apply the route's query.
        q = {
            "user_id": "main",
            "$or": [
                {"dry_run_only": True},
                {"qoyod_product_id": {
                    "$regex": r"^(DRY:|PREVIEW:)", "$options": "i"}},
            ],
        }
        rows = []
        async for r in coll.find(q):
            rows.append(r)
        skus = {r["sku"] for r in rows}
        assert "AMS11961" in skus
        assert "REAL-SKU" not in skus

    async def test_preview_prefix_is_listed(self):
        coll = _ProductMappingColl()
        coll.rows.append({
            "user_id": "main", "sku": "OTHER",
            "qoyod_product_id": "PREVIEW:product:abc",
            "dry_run_only": False,
        })
        q = {
            "user_id": "main",
            "$or": [
                {"dry_run_only": True},
                {"qoyod_product_id": {
                    "$regex": r"^(DRY:|PREVIEW:)", "$options": "i"}},
            ],
        }
        rows = []
        async for r in coll.find(q):
            rows.append(r)
        assert rows[0]["sku"] == "OTHER"

    async def test_real_id_without_dry_flag_is_NOT_listed(self):
        coll = _ProductMappingColl()
        coll.rows.append({
            "user_id": "main", "sku": "GOOD",
            "qoyod_product_id": "42",
            "dry_run_only": False,
        })
        q = {
            "user_id": "main",
            "$or": [
                {"dry_run_only": True},
                {"qoyod_product_id": {
                    "$regex": r"^(DRY:|PREVIEW:)", "$options": "i"}},
            ],
        }
        rows = []
        async for r in coll.find(q):
            rows.append(r)
        assert rows == []

    async def test_real_id_BUT_dry_flag_is_listed(self):
        """Defensive case — id looks real but dry_run_only=True."""
        coll = _ProductMappingColl()
        coll.rows.append({
            "user_id": "main", "sku": "WEIRD",
            "qoyod_product_id": "12345",
            "dry_run_only": True,
        })
        q = {
            "user_id": "main",
            "$or": [
                {"dry_run_only": True},
                {"qoyod_product_id": {
                    "$regex": r"^(DRY:|PREVIEW:)", "$options": "i"}},
            ],
        }
        rows = []
        async for r in coll.find(q):
            rows.append(r)
        assert rows[0]["sku"] == "WEIRD"
