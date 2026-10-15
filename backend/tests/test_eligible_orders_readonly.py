"""Eligible Orders Read-Only endpoint tests — Iter-001 (2026-07-01).

Guards
──────
    • Endpoint is GET-only (no writes on any collection).
    • NO Qoyod API calls.
    • `unified_orders` is the only candidate authority; inbox is evidence.
    • `already_sent` requires an exact official Qoyod reference plus a real
      invoice id; inbox markers do not prove it.
    • Explicit `from_date` / `to_date` are honoured without a 2026-07-01
      floor, using the Salla business date in Asia/Riyadh.
    • `already_sent` orders are hidden from `items` when
      `show_already_sent=False` but always counted in `counts`.
    • bank_transfer → `blocked_bank_transfer_routing`.
    • COD (clean) → `ready_for_preview` + posting_mode='credit_invoice_only'.
    • Order missing from inbox → `missing_from_pipeline`.
    • Order with DRY product → `blocked_product`.
    • Order with unmapped customer → `blocked_customer`.
    • Order with an exact official prior invoice → `already_sent`.
    • cancelled / refunded / deleted remain visible in scanned/excluded
      accounting but never enter the eligible classifier.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, date, timedelta, timezone

import pytest

from integrations.qoyod.eligible_orders import (
    ELIGIBLE_STATUSES,
    INELIGIBLE_STATUSES,
    QOYOD_SYNC_START_DATE,
    QOYOD_TAX_PERIOD,
    build_eligible_orders_report,
    _classify,
    _check_totals,
    _is_real_invoice_id,
    _is_eligible_status,
    _normalise_phone,
    _normalize_status,
    _expand_status_variants,
    _extract_order_created_at,
    _parse_iso_date,
)


@pytest.fixture
def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


# ── Pure-unit tests (no DB) ────────────────────────────────────────
class TestPureUnits:
    def test_dry_invoice_id_not_real(self):
        assert not _is_real_invoice_id(None)
        assert not _is_real_invoice_id("")
        assert not _is_real_invoice_id("DRY:tmp")
        assert not _is_real_invoice_id("PREVIEW:abc")
        assert _is_real_invoice_id(12345)
        assert _is_real_invoice_id("Q-9001")

    def test_phone_normalisation(self):
        assert _normalise_phone("0554681361") == "+966554681361"
        assert _normalise_phone("966554681361") == "+966554681361"
        assert _normalise_phone("+966554681361") == "+966554681361"
        assert _normalise_phone(None) is None

    def test_totals_pass(self):
        r = _check_totals({
            "total_amount": 116.85,
            "items": [{"quantity": 1, "unit_price": 100.0}],
            "shipping_amount": 15.0,
            "tax_amount": 1.85,
        })
        assert r["valid"] is True
        assert r["diff"] == 0.0

    def test_totals_fail(self):
        r = _check_totals({
            "total_amount": 200.00,
            "items": [{"quantity": 1, "unit_price": 100.0}],
            "shipping_amount": 15.0,
            "tax_amount": 1.85,
        })
        assert r["valid"] is False
        assert r["diff"] != 0.0

    def test_eligible_statuses_content(self):
        # User directive: only completed / delivered / shipping.
        for s in ("completed", "delivered", "shipping",
                  "تم التنفيذ", "تم التوصيل", "جاري التوصيل"):
            assert s in ELIGIBLE_STATUSES
        for s in ("cancelled", "refunded", "deleted",
                  "pending", "waiting"):
            assert s not in ELIGIBLE_STATUSES

    # ── Iter-001e: Status normalization ──────────────────────────
    def test_normalize_status_underscore_to_space(self):
        assert _normalize_status("جاري_التوصيل") == _normalize_status(
            "جاري التوصيل")
        assert _normalize_status("تم_التوصيل") == _normalize_status(
            "تم التوصيل")
        assert _normalize_status("تم_التنفيذ") == _normalize_status(
            "تم التنفيذ")

    def test_normalize_status_trim_and_case(self):
        assert _normalize_status("  COMPLETED  ") == "completed"
        assert _normalize_status("Delivered") == "delivered"
        assert _normalize_status("  جاري   التوصيل  ") == \
            _normalize_status("جاري التوصيل")

    def test_normalize_status_none_and_empty(self):
        assert _normalize_status(None) == ""
        assert _normalize_status("") == ""

    def test_is_eligible_status_underscore_arabic(self):
        # Underscore Arabic variants MUST match eligible set.
        assert _is_eligible_status("جاري_التوصيل") is True
        assert _is_eligible_status("تم_التوصيل") is True
        assert _is_eligible_status("تم_التنفيذ") is True
        # Space Arabic still eligible.
        assert _is_eligible_status("جاري التوصيل") is True
        assert _is_eligible_status("تم التوصيل") is True
        # English canonical still eligible.
        assert _is_eligible_status("completed") is True
        assert _is_eligible_status("Delivered") is True
        assert _is_eligible_status("SHIPPING") is True

    def test_is_eligible_status_ineligible_stays_out(self):
        for s in ("waiting", "pending", "in_review", "in review",
                  "cancelled", "canceled", "refunded", "deleted",
                  "محذوف", "بإنتظار_الدفع", "بإنتظار الدفع", "ملغي"):
            assert _is_eligible_status(s) is False, (
                f"{s!r} must NOT be eligible")

    def test_ineligible_statuses_defined(self):
        # Sanity: our documented ineligible set includes common cases.
        for s in ("waiting", "pending", "cancelled", "refunded",
                  "deleted", "محذوف"):
            assert s in INELIGIBLE_STATUSES

    def test_expand_status_variants_includes_both_forms(self):
        variants = _expand_status_variants(frozenset({"جاري التوصيل"}))
        assert "جاري التوصيل" in variants
        assert "جاري_التوصيل" in variants
        # single word (no change).
        variants2 = _expand_status_variants(frozenset({"completed"}))
        assert "completed" in variants2

    # ── Iter-001f: Tax-period sync cutoff (2026-07-01, Q3-2026) ────
    def test_sync_start_date_is_2026_07_01(self):
        assert QOYOD_SYNC_START_DATE == "2026-07-01"
        assert QOYOD_TAX_PERIOD == "Q3-2026"

    def test_parse_iso_date_handles_shapes(self):
        assert _parse_iso_date("2026-07-01") == date(2026, 7, 1)
        assert _parse_iso_date("2026-07-01T10:00:00Z") == \
            date(2026, 7, 1)
        assert _parse_iso_date("2026-07-01 10:00:00+03:00") == \
            date(2026, 7, 1)
        assert _parse_iso_date(datetime(2026, 6, 30, 12, 0)) == \
            date(2026, 6, 30)
        assert _parse_iso_date(date(2026, 6, 30)) == date(2026, 6, 30)
        assert _parse_iso_date(None) is None
        assert _parse_iso_date("") is None
        assert _parse_iso_date("not-a-date") is None

    def test_extract_order_created_at_priority(self):
        # 1. Direct `created_at` wins.
        assert _extract_order_created_at(
            {"created_at": "2026-07-15",
             "order_date": "2026-07-20"}) == date(2026, 7, 15)
        # 2. `order_date` used when created_at missing.
        assert _extract_order_created_at(
            {"order_date": "2026-07-20"}) == date(2026, 7, 20)
        # 3. `order_date_inferred=True` disqualifies order_date.
        assert _extract_order_created_at(
            {"order_date": "2026-07-20",
             "order_date_inferred": True}) is None
        # 4. Salla webhook raw shape via inbox row.
        assert _extract_order_created_at({
            "_inbox_row": {"raw_payload": {
                "data": {"date": {"date": "2026-08-01 10:00:00"}}}}
        }) == date(2026, 8, 1)
        # 5. Nothing → None.
        assert _extract_order_created_at({}) is None


# ── Classifier tests (no DB, direct _classify calls) ───────────────
class TestClassifier:
    def _customer_ok(self):
        return {"resolved": True, "qoyod_id": 223, "reason": None}

    def _products_ok(self):
        return {"resolved": True, "resolved_count": 3,
                "dry_run_only": 0, "missing": [],
                "first_blocker": None}

    def _totals_ok(self):
        return {"valid": True, "total": 100.0,
                "expected": 100.0, "diff": 0.0}

    def _order(self, pm="tabby_installment"):
        return {"payment_method": pm, "total_amount": 100.0,
                "items": [{"sku": "X"}]}

    def test_already_sent_wins_over_everything(self):
        v = _classify(
            self._order(), inbox_row=None,
            invoice={"qoyod_invoice_id": "Q-9001"},
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=False)
        assert v["classification"] == "already_sent"

    def test_totals_mismatch(self):
        v = _classify(
            self._order(), inbox_row={"pipeline_stage": "RULES"},
            invoice=None,
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check={"valid": False, "total": 200.0,
                          "expected": 100.0, "diff": 100.0},
            receiving_bank_configured=True)
        assert v["classification"] == "totals_mismatch"

    def test_bank_transfer_blocked(self):
        v = _classify(
            self._order(pm="bank_transfer"),
            inbox_row={"pipeline_stage": "RULES"},
            invoice=None,
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=False)
        assert v["classification"] == "blocked_bank_transfer_routing"
        assert v["posting_mode"] == "credit_invoice_only"

    def test_bank_transfer_blocked_even_when_bank_configured(self):
        # User directive: bank_transfer stays blocked until Iter-294
        # ships, regardless of receiving bank config.
        v = _classify(
            self._order(pm="bank_transfer"),
            inbox_row={"pipeline_stage": "RULES"},
            invoice=None,
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=True)
        assert v["classification"] == "blocked_bank_transfer_routing"

    def test_unsupported_method_blocked_status(self):
        v = _classify(
            self._order(pm="cheque"),
            inbox_row={"pipeline_stage": "RULES"},
            invoice=None,
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=True)
        assert v["classification"] == "blocked_status"

    def test_customer_missing_blocks(self):
        v = _classify(
            self._order(),
            inbox_row={"pipeline_stage": "RULES"},
            invoice=None,
            customer_check={"resolved": False, "qoyod_id": None,
                            "reason": "no mapping"},
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=True)
        assert v["classification"] == "blocked_customer"

    def test_product_dry_blocks(self):
        v = _classify(
            self._order(),
            inbox_row={"pipeline_stage": "RULES"},
            invoice=None,
            customer_check=self._customer_ok(),
            products_check={
                "resolved": False, "resolved_count": 2,
                "dry_run_only": 1, "missing": [],
                "first_blocker": "SKU 'AMS11542' is dry_run_only"},
            totals_check=self._totals_ok(),
            receiving_bank_configured=True)
        assert v["classification"] == "blocked_product"

    def test_missing_from_pipeline(self):
        v = _classify(
            self._order(),
            inbox_row=None,   # ← key signal
            invoice=None,
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=True)
        assert v["classification"] == "missing_from_pipeline"
        # posting_mode still derived for downstream Preview
        assert v["posting_mode"] == "paid_receipt"

    def test_manual_approval_when_inbox_stalled(self):
        v = _classify(
            self._order(),
            inbox_row={"pipeline_stage": "UNRESOLVED_QOYOD_DEPENDENCY"},
            invoice=None,
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=True)
        assert v["classification"] == "ready_for_manual_approval"

    def test_ready_for_preview_full_green(self):
        v = _classify(
            self._order(),
            inbox_row={"pipeline_stage": "COMPLETED"},
            invoice=None,
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=True)
        assert v["classification"] == "ready_for_preview"
        assert v["posting_mode"] == "paid_receipt"

    def test_cod_ready_shows_credit_invoice_only(self):
        v = _classify(
            self._order(pm="cod"),
            inbox_row={"pipeline_stage": "COMPLETED"},
            invoice=None,
            customer_check=self._customer_ok(),
            products_check=self._products_ok(),
            totals_check=self._totals_ok(),
            receiving_bank_configured=True)
        assert v["classification"] == "ready_for_preview"
        assert v["posting_mode"] == "credit_invoice_only"


# ── End-to-end with real Mongo ─────────────────────────────────────
@pytest.mark.asyncio
class TestEndToEndReport:
    async def _clean(self, db, uid):
        for coll in ("unified_orders", "integration_inbox",
                     "qoyod_invoices", "qoyod_customers_mapping",
                     "qoyod_products_mapping", "qoyod_settings"):
            await db[coll].delete_many({"user_id": uid})

    def _uid(self):
        return f"eo-test-{uuid.uuid4().hex[:8]}"

    def _now_iso(self):
        return datetime.now(timezone.utc).isoformat()

    async def _seed_customer(self, db, uid, phone="+966554681361",
                             qoyod_id=223, dry=False):
        await db.qoyod_customers_mapping.insert_one({
            "user_id": uid, "lookup_key": phone,
            "lookup_kind": "phone",
            "qoyod_customer_id": qoyod_id,
            "dry_run_only": dry,
        })

    async def _seed_product(self, db, uid, sku, pid=42, dry=False):
        await db.qoyod_products_mapping.insert_one({
            "user_id": uid, "sku": sku,
            "qoyod_product_id": pid,
            "dry_run_only": dry,
        })

    async def _seed_order(self, db, uid, *, status="delivered",
                          pm="tabby_installment", total=100.0,
                          items=None, phone="+966554681361",
                          order_date=None, created_at=None):
        oid = f"O-{uuid.uuid4().hex[:8]}"
        # Iter-001b: real `unified_orders` schema uses `order_status`
        # + `order_date` (YYYY-MM-DD), NOT `status` / `created_at`.
        # Iter-001f: default `order_date` is TODAY (>= sync cutoff);
        # tests that want to check the cutoff pass their own value.
        from datetime import date as _date
        od = order_date or _date.today().isoformat()
        doc = {
            "user_id": uid, "order_id": oid, "order_number": oid,
            "order_status": status, "order_status_slug": status,
            "payment_method": pm,
            "total_amount": total,
            "shipping_amount": 0, "tax_amount": 0,
            "items": items or [{"sku": "X", "quantity": 1,
                                "unit_price": total}],
            "customer": {"phone": phone},
            "customer_mobile": phone,
            "order_date": od,
            "received_at": self._now_iso(),
        }
        if created_at is not None:
            doc["created_at"] = created_at
        await db.unified_orders.insert_one(doc)
        return oid

    async def _seed_inbox(self, db, uid, oid, stage="COMPLETED",
                          canonical_order_date=None,
                          canonical_status=None):
        # Iter-001f — inbox rows now need a discoverable created_at
        # for the sync cutoff. `raw_payload.data.date.date` mirrors the
        # real Salla webhook shape; default = today (post-cutoff).
        from datetime import date as _date
        od = canonical_order_date or _date.today().isoformat()
        await db.integration_inbox.insert_one({
            "user_id": uid, "salla_order_id": oid,
            "salla_order_number": oid,
            "connector_key":   f"salla-{oid}",
            "idempotency_key": f"idem-{oid}",
            "trace_id": f"t-{uuid.uuid4().hex[:8]}",
            "pipeline_stage": stage,
            "received_at": self._now_iso(),
            "raw_payload": {"data": {"date": {"date": f"{od} 10:00:00"}}},
            "canonical_payload": {
                "order_id": oid, "order_number": oid,
                "order_status": canonical_status or "delivered",
                "order_status_slug": canonical_status or "delivered",
                "payment_method": "cod",
                "total_amount": 100.0,
                "shipping_amount": 0, "tax_amount": 0,
                "items": [{"sku": "X", "quantity": 1,
                           "unit_price": 100.0}],
                "customer": {"phone": "+966554681361"},
                "order_date": od,
            },
        })

    async def _seed_invoice(self, db, uid, oid,
                            invoice_id="Q-9001",
                            posting_mode="paid_receipt"):
        await db.qoyod_invoices.insert_one({
            "user_id": uid, "salla_order_id": oid,
            "salla_order_number": oid,
            "qoyod_invoice_id": invoice_id,
            "reference": oid,
            "qoyod_official_reference": oid,
            "reference_provenance": "qoyod.reference",
            "posting_mode": posting_mode,
            "created_at": self._now_iso(),
        })

    async def test_already_sent_hidden_by_default_but_counted(self, db):
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid)
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            await self._seed_invoice(db, uid, oid,
                                     invoice_id="Q-REAL-9001")
            report = await build_eligible_orders_report(
                db, user_id=uid, show_already_sent=False)
            assert report["counts"]["already_sent"] == 1
            assert all(i["classification"] != "already_sent"
                       for i in report["items"])
            # And now visible when requested:
            report2 = await build_eligible_orders_report(
                db, user_id=uid, show_already_sent=True)
            assert any(i["classification"] == "already_sent"
                       for i in report2["items"])
        finally:
            await self._clean(db, uid)

    async def test_missing_from_pipeline_detected(self, db):
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid)
            # NO inbox seeded — mimics webhook miss.
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["counts"]["missing_from_pipeline"] == 1
            item = next(i for i in report["items"]
                        if i["order_number"] == oid)
            assert item["classification"] == "missing_from_pipeline"
            assert item["latest_trace_id"] is None
        finally:
            await self._clean(db, uid)

    async def test_bank_transfer_classified(self, db):
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid, pm="bank_transfer")
            await self._seed_inbox(db, uid, oid)
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["counts"]["blocked_bank_transfer_routing"] == 1
            item = report["items"][0]
            assert item["classification"] == "blocked_bank_transfer_routing"
            assert item["posting_mode"] == "credit_invoice_only"
        finally:
            await self._clean(db, uid)

    async def test_cod_ready_shows_credit_invoice_only(self, db):
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid, pm="cod")
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["counts"]["ready_for_preview"] == 1
            item = report["items"][0]
            assert item["classification"] == "ready_for_preview"
            assert item["posting_mode"] == "credit_invoice_only"
        finally:
            await self._clean(db, uid)

    async def test_dry_product_blocks(self, db):
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X", pid="DRY:X", dry=True)
            oid = await self._seed_order(db, uid)
            await self._seed_inbox(db, uid, oid)
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["counts"]["blocked_product"] == 1
            assert report["items"][0]["classification"] == "blocked_product"
        finally:
            await self._clean(db, uid)

    async def test_missing_customer_blocks(self, db):
        uid = self._uid()
        try:
            # No customer mapping seeded.
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid)
            await self._seed_inbox(db, uid, oid)
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["counts"]["blocked_customer"] == 1
        finally:
            await self._clean(db, uid)

    async def test_cancelled_orders_excluded(self, db):
        uid = self._uid()
        try:
            await self._seed_order(db, uid, status="cancelled")
            await self._seed_order(db, uid, status="refunded")
            await self._seed_order(db, uid, status="deleted")
            report = await build_eligible_orders_report(db, user_id=uid)
            assert sum(report["counts"].values()) == 0
            # Scanned is the authoritative unified universe before status
            # eligibility; exclusions remain visible and auditable.
            assert report["total_scanned"] == 3
            assert report["excluded_status_count"] == 3
            assert report["excluded_reason_counts"][
                "status_not_eligible"
            ] == 3
        finally:
            await self._clean(db, uid)

    # ── Iter-001e: Status normalization end-to-end ─────────────────
    async def test_underscore_arabic_status_treated_as_eligible(self, db):
        """Order with `جاري_التوصيل` (underscore) MUST be picked up as
        eligible — same as `جاري التوصيل` (space)."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid, status="جاري_التوصيل",
                                         pm="cod")
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            report = await build_eligible_orders_report(db, user_id=uid)
            # Must be scanned (query picked it up via _expand_status_variants).
            assert report["total_scanned"] == 1, (
                f"underscore Arabic status not picked up: {report}")
            # Must classify (not excluded).
            assert report["excluded_status_count"] == 0
            # Must land in a `counts` bucket (ready_for_preview here).
            assert report["counts"]["ready_for_preview"] == 1
            # Breakdown should record the raw form.
            assert "جاري_التوصيل" in report["total_eligible_by_status"]
        finally:
            await self._clean(db, uid)

    async def test_space_arabic_status_still_eligible(self, db):
        """Regression: `جاري التوصيل` (space) MUST still work."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid, status="جاري التوصيل",
                                         pm="cod")
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["total_scanned"] == 1
            assert report["counts"]["ready_for_preview"] == 1
            assert "جاري التوصيل" in report["total_eligible_by_status"]
        finally:
            await self._clean(db, uid)

    async def test_all_underscore_arabic_variants_eligible(self, db):
        """جاري_التوصيل, تم_التوصيل, تم_التنفيذ — all eligible."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            for st in ("جاري_التوصيل", "تم_التوصيل", "تم_التنفيذ"):
                oid = await self._seed_order(db, uid, status=st, pm="cod")
                await db.integration_inbox.insert_one({
                    "user_id": uid, "salla_order_id": oid,
                    "salla_order_number": oid,
                    "connector_key": f"salla-{oid}",
                    "idempotency_key": f"idem-{oid}",
                    "trace_id": f"t-{uuid.uuid4().hex[:8]}",
                    "pipeline_stage": "COMPLETED",
                    "received_at": self._now_iso(),
                })
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["total_scanned"] == 3, (
                f"expected 3 scanned, got {report['total_scanned']} — "
                f"one of the underscore variants was dropped: "
                f"{report['total_eligible_by_status']}")
            assert report["counts"]["ready_for_preview"] == 3
        finally:
            await self._clean(db, uid)

    async def test_invariant_holds_with_normalization(self, db):
        """Mix eligible + ineligible; invariant must hold."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            # 2 eligible (underscore + space Arabic).
            o1 = await self._seed_order(db, uid, status="جاري_التوصيل",
                                        pm="cod")
            await db.integration_inbox.insert_one({
                "user_id": uid, "salla_order_id": o1,
                "salla_order_number": o1,
                "connector_key": f"salla-{o1}",
                "idempotency_key": f"idem-{o1}",
                "trace_id": f"t-{uuid.uuid4().hex[:8]}",
                "pipeline_stage": "COMPLETED",
                "received_at": self._now_iso(),
            })
            o2 = await self._seed_order(db, uid, status="delivered",
                                        pm="cod")
            await db.integration_inbox.insert_one({
                "user_id": uid, "salla_order_id": o2,
                "salla_order_number": o2,
                "connector_key": f"salla-{o2}",
                "idempotency_key": f"idem-{o2}",
                "trace_id": f"t-{uuid.uuid4().hex[:8]}",
                "pipeline_stage": "COMPLETED",
                "received_at": self._now_iso(),
            })
            # 2 ineligible (in the DB but excluded by the query).
            await self._seed_order(db, uid, status="cancelled")
            await self._seed_order(db, uid, status="بإنتظار_الدفع")
            report = await build_eligible_orders_report(db, user_id=uid)
            # All four unified rows are scanned; only two enter classifier.
            assert report["total_scanned"] == 4
            assert report["total_classified"] == 2
            assert report["excluded_status_count"] == 2
            assert report["excluded_reason_counts"][
                "status_not_eligible"
            ] == 2
            assert report["invariant_holds"] is True
            # Response shape contract.
            assert "total_eligible_by_status" in report
            assert "total_ineligible_by_status" in report
            assert "excluded_reason_counts" in report
        finally:
            await self._clean(db, uid)

    async def test_response_has_normalization_note(self, db):
        """UI depends on the normalization note to explain behavior."""
        uid = self._uid()
        try:
            report = await build_eligible_orders_report(db, user_id=uid)
            notes = " ".join(report.get("notes") or [])
            assert "normalization" in notes.lower() or "طبيع" in notes \
                or "_" in notes, (
                f"notes must mention normalization: {report.get('notes')}")
        finally:
            await self._clean(db, uid)

    # ── 2026-08-22: exact requested accounting interval ───────────
    async def test_response_advertises_exact_date_contract(self, db):
        uid = self._uid()
        try:
            r = await build_eligible_orders_report(
                db,
                user_id=uid,
                from_date="2026-06-10",
                to_date="2026-06-30",
            )
            # Rollout metadata remains visible but never clamps the request.
            assert r["sync_start_date"] == "2026-07-01"
            assert r["tax_period"] == "Q3-2026"
            assert r["from_date"] == "2026-06-10"
            assert r["to_date"] == "2026-06-30"
            assert r["since_date"] == "2026-06-10"
            assert r["date_filter_basis"] == (
                "salla_order_created_at_Asia/Riyadh"
            )
            assert "excluded_before_sync_start_date_count" in r
            assert "excluded_missing_order_created_at_count" in r
            notes = " ".join(r.get("notes") or [])
            assert "2026-07-01" in notes
            assert "never silently clamped" in notes
        finally:
            await self._clean(db, uid)

    async def test_order_before_july_is_included_when_requested(self, db):
        """An explicit Q2 interval is honoured without a July floor."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(
                db, uid, status="delivered", pm="cod",
                order_date="2026-06-30",
                created_at="2026-06-30T14:00:00+03:00")
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            r = await build_eligible_orders_report(
                db,
                user_id=uid,
                from_date="2026-06-30",
                to_date="2026-06-30",
            )
            assert r["total_scanned"] == 1
            assert r["from_date"] == "2026-06-30"
            assert r["to_date"] == "2026-06-30"
            assert r["excluded_before_sync_start_date_count"] == 0
            assert r["total_classified"] == 1
            assert r["counts"]["ready_for_preview"] == 1
            assert r["invariant_holds"] is True
            assert any(i["order_number"] == oid for i in r["items"])
        finally:
            await self._clean(db, uid)

    async def test_order_on_cutoff_included(self, db):
        """Iter-001f: order created exactly on 2026-07-01 IS eligible."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(
                db, uid, status="delivered", pm="cod",
                order_date="2026-07-01",
                created_at="2026-07-01T00:05:00+03:00")
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            r = await build_eligible_orders_report(
                db,
                user_id=uid,
                from_date="2026-07-01",
                to_date="2026-07-01",
            )
            assert r["total_scanned"] == 1
            assert r["excluded_before_sync_start_date_count"] == 0
            assert r["counts"]["ready_for_preview"] == 1
            item = next(i for i in r["items"] if i["order_number"] == oid)
            assert item["salla_order_created_at"] == "2026-07-01"
        finally:
            await self._clean(db, uid)

    async def test_late_arrival_uses_salla_date_and_requested_range(self, db):
        """Inbox arrival time cannot move an old Salla order out of range."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(
                db, uid, status="delivered", pm="cod",
                order_date="2026-05-15",             # Q2
                created_at="2026-05-15T10:00:00+03:00")
            # `received_at` is auto-set to `_now_iso()` (today = Q3).
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            r = await build_eligible_orders_report(
                db,
                user_id=uid,
                from_date="2026-05-15",
                to_date="2026-05-15",
            )
            assert r["from_date"] == "2026-05-15"
            assert r["to_date"] == "2026-05-15"
            assert r["excluded_before_sync_start_date_count"] == 0
            assert r["total_scanned"] == 1
            assert r["total_classified"] == 1
            assert any(i["order_number"] == oid for i in r["items"])
            assert r["invariant_holds"] is True
        finally:
            await self._clean(db, uid)

    async def test_missing_created_at_excluded_and_counted(self, db):
        """Iter-001f: no way to date the order → excluded, not classified."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = f"O-{uuid.uuid4().hex[:8]}"
            # Manually insert an order with NO order_date + inferred flag
            # to hide the fallback. This is the pathological case.
            await db.unified_orders.insert_one({
                "user_id": uid, "order_id": oid, "order_number": oid,
                "order_status": "delivered",
                "order_status_slug": "delivered",
                "payment_method": "cod",
                "total_amount": 100.0, "shipping_amount": 0,
                "tax_amount": 0,
                "items": [{"sku": "X", "quantity": 1, "unit_price": 100.0}],
                "customer": {"phone": "+966554681361"},
                "customer_mobile": "+966554681361",
                # No order_date, no created_at, and order_date_inferred=True
                # to block the fallback path if something later fills it.
                "order_date_inferred": True,
                "received_at": self._now_iso(),
            })
            # Since order_date is missing, the query's `$or` will match
            # via received_at (BSON) OR order_date (>=str), but we've
            # stored received_at as ISO string — mixed types. To force
            # the row into the fetch, add order_date matching today.
            await db.unified_orders.update_one(
                {"user_id": uid, "order_id": oid},
                {"$set": {"order_date": datetime.now(timezone.utc)
                                                .date().isoformat()}})
            # Now order_date exists but `order_date_inferred=True` will
            # cause _extract_order_created_at to return None.
            r = await build_eligible_orders_report(db, user_id=uid)
            assert r["excluded_missing_order_created_at_count"] == 1
            assert r["total_classified"] == 0
            assert r["invariant_holds"] is True
            assert "missing_or_inferred_order_date" in \
                r["excluded_reason_counts"]
        finally:
            await self._clean(db, uid)

    async def test_invariant_holds_across_requested_q2_q3_range(self, db):
        """An explicit cross-quarter range classifies every eligible row."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            # 2 eligible (post cutoff).
            for _ in range(2):
                oid = await self._seed_order(
                    db, uid, status="delivered", pm="cod",
                    order_date="2026-07-05",
                    created_at="2026-07-05T10:00:00+03:00")
                await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            # 1 pre-cutoff.
            await self._seed_order(
                db, uid, status="delivered", pm="cod",
                order_date="2026-06-15",
                created_at="2026-06-15T10:00:00+03:00")
            r = await build_eligible_orders_report(
                db,
                user_id=uid,
                from_date="2026-06-01",
                to_date="2026-07-31",
            )
            assert r["total_scanned"] == 3
            assert r["excluded_before_sync_start_date_count"] == 0
            assert r["counts"]["ready_for_preview"] == 2
            assert r["counts"]["missing_from_pipeline"] == 1
            assert r["total_classified"] == 3
            assert r["invariant_holds"] is True
        finally:
            await self._clean(db, uid)

    async def test_read_only_no_writes(self, db):
        """Sanity check — running the report against seeded data does
        not modify ANY of the six read collections."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid)
            await self._seed_inbox(db, uid, oid)
            # Snapshot counts.
            before = {
                c: await db[c].count_documents({"user_id": uid})
                for c in ("unified_orders", "integration_inbox",
                          "qoyod_invoices", "qoyod_customers_mapping",
                          "qoyod_products_mapping", "qoyod_settings")
            }
            _ = await build_eligible_orders_report(db, user_id=uid)
            after = {
                c: await db[c].count_documents({"user_id": uid})
                for c in before
            }
            assert before == after, (
                "Endpoint must not write to any collection")
        finally:
            await self._clean(db, uid)

    async def test_gates_present_in_response(self, db):
        uid = self._uid()
        try:
            report = await build_eligible_orders_report(db, user_id=uid)
            gates = report["gates"]
            assert "production_writes_locked" in gates
            assert "selective_live_send_enabled" in gates
            assert "settlements_write_gate" in gates
            assert "receiving_bank_configured" in gates
        finally:
            await self._clean(db, uid)

    async def test_debug_diagnostic_block_when_enabled(self, db):
        """Debug exposes safe reference-set evidence, never raw samples."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid)
            await self._seed_inbox(db, uid, oid)
            report = await build_eligible_orders_report(
                db, user_id=uid, debug=True)
            assert "_diagnostic" in report
            d = report["_diagnostic"]
            assert d["orders_user_id"] == uid
            assert d["markers_user_id"] == uid
            assert d["reference_sets"]["eligible"] == [oid]
            assert d["reference_sets"]["sent_exact"] == []
            assert d["reference_sets"]["worker_candidates"] == [oid]
            assert d["duplicate_qoyod_references"] == []
            assert not any("sample" in key for key in d)
            assert not any("raw" in key for key in d)
        finally:
            await self._clean(db, uid)

    async def test_debug_diagnostic_absent_when_disabled(self, db):
        uid = self._uid()
        try:
            report = await build_eligible_orders_report(
                db, user_id=uid, debug=False)
            assert "_diagnostic" not in report
        finally:
            await self._clean(db, uid)

    async def test_arabic_native_status_matches(self, db):
        """Regression — Arabic status labels (تم التوصيل) should
        also flow through the classifier."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid, status="تم التوصيل")
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["total_scanned"] == 1
            assert report["counts"]["ready_for_preview"] == 1
        finally:
            await self._clean(db, uid)

    async def test_source_mode_is_unified_orders_when_populated(self, db):
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            oid = await self._seed_order(db, uid)
            await self._seed_inbox(db, uid, oid, stage="COMPLETED")
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["source_mode"] == "unified_orders"
        finally:
            await self._clean(db, uid)

    async def test_inbox_only_order_does_not_expand_unified_universe(
            self, db):
        """Inbox is evidence only and can never create a candidate."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            # Deliberately do not seed unified_orders.
            await db.integration_inbox.insert_one({
                "user_id": uid,
                "salla_order_id":     "268307955",
                "salla_order_number": "268307955",
                "trace_id":           "t-268307955",
                "pipeline_stage":     "UNRESOLVED_QOYOD_DEPENDENCY",
                "received_at":        datetime.now(timezone.utc),
                "canonical_payload": {
                    "order_status":      "delivered",
                    "order_status_slug": "delivered",
                    "payment_method":    "tabby_installment",
                    "total_amount":      116.85,
                    "shipping_amount":   0,
                    "tax_amount":        0,
                    "items": [{"sku": "X", "quantity": 1,
                               "unit_price": 116.85}],
                    "customer": {"phone": "+966554681361"},
                    "order_date": datetime.now(timezone.utc)
                                        .date().isoformat(),
                },
                "qoyod_payloads": {"invoice": {
                    "contact_id": 223,
                    "line_items": [{"product_id": 42, "quantity": 1}],
                }},
            })
            report = await build_eligible_orders_report(
                db, user_id=uid, debug=True)
            assert report["source_mode"] == "unified_orders"
            assert report["source_authority"] == "unified_orders"
            assert "only candidate authority" in report["source_reason"]
            assert report["total_scanned"] == 0
            assert report["total_classified"] == 0
            assert report["worker_candidate_count"] == 0
            assert report["items"] == []
            assert report["_diagnostic"]["reference_sets"] == {
                "eligible": [],
                "sent_exact": [],
                "worker_candidates": [],
            }
        finally:
            await db.integration_inbox.delete_many({"user_id": uid})
            await self._clean(db, uid)

    async def test_inbox_marker_alone_does_not_prove_already_sent(self, db):
        """A local inbox marker is not an exact official Qoyod reference."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            await db.integration_inbox.insert_one({
                "user_id": uid,
                "salla_order_number": "269571122",
                "trace_id": "t-269571122",
                "pipeline_stage": "COMPLETED",
                "received_at": datetime.now(timezone.utc),
                "qoyod_invoice_id": "Q-REAL-5555",
                "canonical_payload": {
                    "order_status": "completed",
                    "payment_method": "mada",
                    "total_amount": 100.0, "shipping_amount": 0,
                    "tax_amount": 0,
                    "items": [{"sku": "X", "quantity": 1,
                               "unit_price": 100.0}],
                    "customer": {"phone": "+966554681361"},
                    "order_date": datetime.now(timezone.utc)
                                        .date().isoformat(),
                },
            })
            report = await build_eligible_orders_report(
                db, user_id=uid, show_already_sent=True)
            assert report["source_mode"] == "unified_orders"
            assert report["total_scanned"] == 0
            assert report["counts"]["already_sent"] == 0
            assert report["exact_qoyod_reference_matches"] == 0
            assert report["items"] == []
        finally:
            await db.integration_inbox.delete_many({"user_id": uid})
            await self._clean(db, uid)

    async def test_invariant_holds_all_scanned_rows_accounted_for(self, db):
        """The unified-authority invariant
            total_classified + excluded_status_count == total_scanned
        accounts for eligible and status-excluded unified rows."""
        uid = self._uid()
        now = datetime.now(timezone.utc)
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            # 3 billable + 2 ineligible rows in unified_orders. Inbox is
            # attached only as explanatory evidence for the billable rows.
            for st in (
                "delivered", "completed", "shipping", "pending", "waiting",
            ):
                oid = await self._seed_order(
                    db,
                    uid,
                    status=st,
                    pm="mada",
                    order_date=now.date().isoformat(),
                )
                if st in ("delivered", "completed", "shipping"):
                    await self._seed_inbox(
                        db,
                        uid,
                        oid,
                        stage="COMPLETED",
                        canonical_status=st,
                    )
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["source_mode"] == "unified_orders"
            assert report["total_scanned"] == 5
            # 3 billable statuses land in `counts`.
            assert report["total_classified"] == 3
            # 2 ineligible-status rows land in excluded, NOT dropped.
            assert report["excluded_status_count"] == 2
            # Invariant holds.
            assert report["invariant_holds"] is True
            assert (report["total_classified"]
                    + report["excluded_status_count"]
                    == report["total_scanned"])
            # Reason breakdown present.
            assert report["excluded_reason_counts"]
            assert report["excluded_reason_counts"][
                "status_not_eligible"
            ] == 2
            # No mystery bucket.
            assert report["unclassified_count"] == 0
        finally:
            await db.integration_inbox.delete_many({"user_id": uid})
            await self._clean(db, uid)

    async def test_new_bookkeeping_fields_present(self, db):
        uid = self._uid()
        try:
            report = await build_eligible_orders_report(db, user_id=uid)
            for k in ("total_source_rows", "total_scanned",
                      "total_classified", "excluded_status_count",
                      "unclassified_count", "total_hidden_already_sent",
                      "total_returned_items", "invariant_holds",
                      "excluded_reason_counts"):
                assert k in report, f"missing bookkeeping field: {k}"
        finally:
            await self._clean(db, uid)

    async def test_hidden_already_sent_counter_matches(self, db):
        """`total_hidden_already_sent` should equal already_sent count
        when `show_already_sent=False`."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            for i in range(3):
                oid = await self._seed_order(
                    db, uid, status="delivered", pm="mada",
                )
                await self._seed_inbox(db, uid, oid, stage="COMPLETED")
                await self._seed_invoice(
                    db, uid, oid, invoice_id=f"Q-REAL-{i}",
                )
            report = await build_eligible_orders_report(
                db, user_id=uid, show_already_sent=False)
            assert report["counts"]["already_sent"] == 3
            assert report["total_hidden_already_sent"] == 3
            assert report["total_returned_items"] == 0
        finally:
            await db.integration_inbox.delete_many({"user_id": uid})
            await self._clean(db, uid)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
