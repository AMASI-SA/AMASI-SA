"""Eligible Orders Read-Only endpoint tests — Iter-001 (2026-07-01).

Guards
──────
    • Endpoint is GET-only (no writes on any collection).
    • NO Qoyod API calls.
    • `already_sent` orders are hidden from `items` when
      `show_already_sent=False` but always counted in `counts`.
    • bank_transfer → `blocked_bank_transfer_routing`.
    • COD (clean) → `ready_for_preview` + posting_mode='credit_invoice_only'.
    • Order missing from inbox → `missing_from_pipeline`.
    • Order with DRY product → `blocked_product`.
    • Order with unmapped customer → `blocked_customer`.
    • Order with prior real invoice → `already_sent`.
    • cancelled / refunded / deleted are excluded from the query.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from integrations.qoyod.eligible_orders import (
    ELIGIBLE_STATUSES,
    build_eligible_orders_report,
    _classify,
    _check_totals,
    _is_real_invoice_id,
    _normalise_phone,
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
                          items=None, phone="+966554681361"):
        oid = f"O-{uuid.uuid4().hex[:8]}"
        # Iter-001b: real `unified_orders` schema uses `order_status`
        # + `order_date` (YYYY-MM-DD), NOT `status` / `created_at`.
        from datetime import date
        await db.unified_orders.insert_one({
            "user_id": uid, "order_id": oid, "order_number": oid,
            "order_status": status, "order_status_slug": status,
            "payment_method": pm,
            "total_amount": total,
            "shipping_amount": 0, "tax_amount": 0,
            "items": items or [{"sku": "X", "quantity": 1,
                                "unit_price": total}],
            "customer": {"phone": phone},
            "customer_mobile": phone,
            "order_date": date.today().isoformat(),
            "received_at": self._now_iso(),
        })
        return oid

    async def _seed_inbox(self, db, uid, oid, stage="COMPLETED"):
        await db.integration_inbox.insert_one({
            "user_id": uid, "salla_order_id": oid,
            "salla_order_number": oid,
            "trace_id": f"t-{uuid.uuid4().hex[:8]}",
            "pipeline_stage": stage,
            "received_at": self._now_iso(),
        })

    async def _seed_invoice(self, db, uid, oid,
                            invoice_id="Q-9001",
                            posting_mode="paid_receipt"):
        await db.qoyod_invoices.insert_one({
            "user_id": uid, "salla_order_id": oid,
            "salla_order_number": oid,
            "qoyod_invoice_id": invoice_id,
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
            assert report["total_scanned"] == 0
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
        """Iter-001b — `debug=true` emits collection totals + samples
        + status breakdowns without changing counts. Read-only."""
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
            assert d["user_id_used_in_query"] == uid
            assert d["unified_orders_total_all_time"] >= 1
            assert d["unified_orders_total_90d"] >= 1
            assert d["integration_inbox_total_all_time"] >= 1
            assert isinstance(
                d["unified_orders_status_breakdown_all_time"], dict)
            assert isinstance(
                d["sample_unified_orders_all_time"], list)
            assert len(d["sample_unified_orders_all_time"]) >= 1
            assert d["eligible_statuses_configured"]
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

    async def test_fallback_to_integration_inbox_when_unified_empty(
            self, db):
        """Iter-001c — when `unified_orders` is empty for the tenant,
        the report reconstructs orders from `integration_inbox`
        (BSON datetime filter fixed). Production scenario."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            # DO NOT seed unified_orders — this is the fallback case.
            # Seed an inbox row with a canonical_payload that describes
            # a Salla order.
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
                },
                "qoyod_payloads": {"invoice": {
                    "contact_id": 223,
                    "line_items": [{"product_id": 42, "quantity": 1}],
                }},
            })
            report = await build_eligible_orders_report(
                db, user_id=uid, debug=True)
            assert report["source_mode"] == "integration_inbox_fallback"
            assert "unified_orders is empty" in report["source_reason"]
            assert report["total_scanned"] == 1
            assert any("missing_from_pipeline unavailable"
                       in n for n in report["notes"])
            # The row should be classified (not missing_from_pipeline).
            item = report["items"][0]
            assert item["order_number"] == "268307955"
            assert item["classification"] != "missing_from_pipeline"
            # Diagnostic block reports the source correctly.
            assert report["_diagnostic"]["source_mode"] == \
                "integration_inbox_fallback"
            assert report["_diagnostic"]["integration_inbox_total_90d"] \
                >= 1
            assert "date" in report["_diagnostic"][
                "received_at_type_breakdown"] \
                or "string" in report["_diagnostic"][
                    "received_at_type_breakdown"]
        finally:
            await db.integration_inbox.delete_many({"user_id": uid})
            await self._clean(db, uid)

    async def test_fallback_already_sent_detected_from_inbox(self, db):
        """When inbox row carries a real qoyod_invoice_id we synthesise
        an invoice dict so classifier catches `already_sent`."""
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
                },
            })
            report = await build_eligible_orders_report(
                db, user_id=uid, show_already_sent=True)
            assert report["source_mode"] == "integration_inbox_fallback"
            assert report["counts"]["already_sent"] == 1
        finally:
            await db.integration_inbox.delete_many({"user_id": uid})
            await self._clean(db, uid)

    async def test_invariant_holds_all_scanned_rows_accounted_for(self, db):
        """Iter-001d — the invariant
            total_classified + excluded_status_count == total_scanned
        must hold in every scenario, including mixed statuses in
        fallback mode."""
        uid = self._uid()
        now = datetime.now(timezone.utc)
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            # 3 rows with billable status + 2 rows with ineligible
            # status → all must land somewhere.
            for i, st in enumerate(
                    ["delivered", "completed", "shipping",
                     "pending",   "waiting"]):
                await db.integration_inbox.insert_one({
                    "user_id": uid,
                    "connector_key": "salla",
                    "idempotency_key": f"inv-{i}",
                    "salla_order_number": f"INV-{i}",
                    "trace_id": f"t-INV-{i}",
                    "pipeline_stage": "COMPLETED"
                                       if st in ("delivered", "completed",
                                                 "shipping")
                                       else "WAITING",
                    "received_at": now,
                    "canonical_payload": {
                        "order_status": st,
                        "payment_method": "mada",
                        "total_amount": 100.0,
                        "shipping_amount": 0, "tax_amount": 0,
                        "items": [{"sku": "X", "quantity": 1,
                                   "unit_price": 100.0}],
                        "customer": {"phone": "+966554681361"},
                    },
                })
            report = await build_eligible_orders_report(db, user_id=uid)
            assert report["source_mode"] == "integration_inbox_fallback"
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
            assert any("pending" in k or "waiting" in k
                       for k in report["excluded_reason_counts"].keys())
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
        now = datetime.now(timezone.utc)
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid, "X")
            for i in range(3):
                await db.integration_inbox.insert_one({
                    "user_id": uid,
                    "connector_key": "salla",
                    "idempotency_key": f"sent-{i}",
                    "salla_order_number": f"SENT-{i}",
                    "trace_id": f"t-{i}",
                    "pipeline_stage": "COMPLETED",
                    "received_at": now,
                    "qoyod_invoice_id": f"Q-REAL-{i}",
                    "canonical_payload": {
                        "order_status": "delivered",
                        "payment_method": "mada",
                        "total_amount": 100.0,
                        "shipping_amount": 0, "tax_amount": 0,
                        "items": [{"sku": "X", "quantity": 1,
                                   "unit_price": 100.0}],
                        "customer": {"phone": "+966554681361"},
                    },
                })
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
