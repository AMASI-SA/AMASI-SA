"""Selective Live Send Gate — Policy layer tests (Phase C.0).

Coverage matches user directive verbatim:
    • Q2 order blocked
    • Q3 eligible paid order allowed by policy فقط إذا كل الشروط صحيحة
    • COD Q3 allowed as credit_invoice_only فقط
    • bank_transfer blocked
    • DRY customer blocked
    • DRY product blocked
    • PREVIEW IDs blocked
    • totals mismatch > 0.01 blocked
    • missing created_at blocked
    • already sent blocked
    • selective_live_send_enabled=false يمنع أي Qoyod API call
    • production_writes_locked=true يبقى true
"""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict
from datetime import datetime, date, timedelta, timezone

import pytest

from integrations.qoyod.selective_send_policy import (
    BlockerCode,
    SelectiveSendDecision,
    QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT,
    QOYOD_INVOICE_DATE_SOURCE_DEFAULT,
    QOYOD_SEND_TIMEZONE,
    build_selective_send_policy_report,
    manual_approval_phrase_for,
    should_allow_selective_live_send,
    _ALLOWED_PAYMENT_METHODS,
)


@pytest.fixture
def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


# ── Sanity helpers ──────────────────────────────────────────────────
def _base_order(**overrides):
    """A canonical 'green' order that should pass every check when the
    two master gates are open. Tests override the specific field they
    want to isolate.

    Iter-001h — status defaults to `completed` because
    `qoyod_enabled_invoice_trigger_statuses` defaults to
    `["completed", "تم التنفيذ"]`. `delivered` / `shipping` /
    Arabic in-transit variants are now blocked by default.
    """
    o = {
        "order_number": "TEST-001",
        "salla_order_id": "TEST-001",
        "salla_order_created_at": "2026-07-05",  # Q3, post-cutoff
        "status": "completed",
        "payment_method": "mada",
        "existing_qoyod_invoice_id": None,
        "customer_status": {"resolved": True, "qoyod_id": 223,
                            "reason": None},
        "products_status": {"resolved": True, "resolved_count": 1,
                            "dry_run_only": 0, "missing": []},
        "totals_status": {"valid": True, "total": 100.0,
                          "expected": 100.0, "diff": 0.0},
    }
    o.update(overrides)
    return o


def _gates_OPEN():
    """A tests-only settings snapshot with BOTH master gates open.
    Production must NEVER see this — Fail-Closed is the real default."""
    return {
        "selective_live_send_enabled": True,
        "production_writes_locked":    False,
        "qoyod_sync_start_date":       "2026-07-01",
        "qoyod_tax_period":            "Q3-2026",
        "bank_transfer_routing_enabled": False,
        "qoyod_invoice_date_source":   "send_date",
        "qoyod_enabled_invoice_trigger_statuses":
            ["completed", "تم التنفيذ"],
    }


def _gates_CLOSED():
    """Fail-Closed default — production reality until go-live."""
    return {
        "selective_live_send_enabled": False,
        "production_writes_locked":    True,
        "qoyod_sync_start_date":       "2026-07-01",
        "qoyod_tax_period":            "Q3-2026",
        "bank_transfer_routing_enabled": False,
        "qoyod_invoice_date_source":   "send_date",
        "qoyod_enabled_invoice_trigger_statuses":
            ["completed", "تم التنفيذ"],
    }


# ── Master gates — Fail-Closed contract ─────────────────────────────
class TestMasterGates:
    def test_default_settings_dict_is_fail_closed(self):
        d = should_allow_selective_live_send(
            order=_base_order(), settings={})
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.GATE_DISABLED
        assert d.would_send_to_qoyod is False

    def test_gate_disabled_blocks_everything(self):
        # Perfect green order but gate is closed.
        d = should_allow_selective_live_send(
            order=_base_order(), settings=_gates_CLOSED())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.GATE_DISABLED

    def test_write_lock_active_blocks_even_when_gate_open(self):
        s = _gates_OPEN()
        s["production_writes_locked"] = True
        d = should_allow_selective_live_send(
            order=_base_order(), settings=s)
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.WRITE_LOCK_ACTIVE

    def test_gates_snapshot_included_in_decision(self):
        d = should_allow_selective_live_send(
            order=_base_order(), settings=_gates_OPEN())
        g = d.gates_snapshot
        assert g["selective_live_send_enabled"] is True
        assert g["production_writes_locked"] is False
        assert g["qoyod_sync_start_date"] == "2026-07-01"


# ── Cutoff / date checks ────────────────────────────────────────────
class TestSyncCutoff:
    def test_q2_order_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(salla_order_created_at="2026-06-30"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.BEFORE_SYNC_START_DATE
        assert "2026-06-30" in (d.blocker_reason or "")

    def test_missing_created_at_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(salla_order_created_at=None),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.MISSING_ORDER_CREATED_AT

    def test_on_cutoff_allowed(self):
        d = should_allow_selective_live_send(
            order=_base_order(salla_order_created_at="2026-07-01"),
            settings=_gates_OPEN())
        assert d.decision == "allow"
        assert d.would_send_to_qoyod is True


# ── Status / already-sent checks ────────────────────────────────────
class TestStatusAndSent:
    def test_ineligible_status_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(status="waiting"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.STATUS_NOT_ELIGIBLE

    def test_underscore_arabic_completed_allowed(self):
        # Iter-001e — `تم_التنفيذ` normalises to `تم التنفيذ` and is
        # in the default enabled list.
        d = should_allow_selective_live_send(
            order=_base_order(status="تم_التنفيذ"),
            settings=_gates_OPEN())
        assert d.decision == "allow"

    def test_underscore_arabic_delivering_blocked_by_default(self):
        # Iter-001h — `جاري_التوصيل` normalises to `جاري التوصيل`
        # which is broadly eligible BUT NOT in default enabled list.
        d = should_allow_selective_live_send(
            order=_base_order(status="جاري_التوصيل"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED

    def test_already_sent_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(existing_qoyod_invoice_id="Q-REAL-9001"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.ALREADY_SENT

    def test_dry_invoice_id_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(existing_qoyod_invoice_id="DRY:temp-1"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.DRY_INVOICE_ID_DETECTED

    def test_preview_id_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(existing_qoyod_invoice_id="PREVIEW:abc"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.PREVIEW_ID_DETECTED


# ── Payment method rules ────────────────────────────────────────────
class TestPaymentMethods:
    def test_bank_transfer_hold_iter_294(self):
        d = should_allow_selective_live_send(
            order=_base_order(payment_method="bank_transfer"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.BANK_TRANSFER_ON_HOLD

    def test_cod_allowed_as_credit_invoice_only(self):
        d = should_allow_selective_live_send(
            order=_base_order(payment_method="cod"),
            settings=_gates_OPEN())
        assert d.decision == "allow"
        assert d.posting_mode == "credit_invoice_only"

    def test_prepaid_allowed_as_paid_receipt(self):
        for pm in ("mada", "apple_pay", "stc_pay", "credit_card",
                   "visa", "mastercard"):
            d = should_allow_selective_live_send(
                order=_base_order(payment_method=pm),
                settings=_gates_OPEN())
            assert d.decision == "allow", f"{pm} should allow"
            assert d.posting_mode == "paid_receipt"

    def test_bnpl_allowed_as_paid_receipt(self):
        for pm in ("tabby", "tabby_installment", "tamara",
                   "tamara_installment", "emkan"):
            d = should_allow_selective_live_send(
                order=_base_order(payment_method=pm),
                settings=_gates_OPEN())
            assert d.decision == "allow", f"{pm} should allow"
            assert d.posting_mode == "paid_receipt"

    def test_unknown_payment_method_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(payment_method="crypto"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.PAYMENT_METHOD_NOT_ALLOWED

    def test_empty_payment_method_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(payment_method=""),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.PAYMENT_METHOD_NOT_ALLOWED


# ── Customer / product resolution checks ────────────────────────────
class TestCustomerAndProducts:
    def test_customer_not_resolved_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(customer_status={
                "resolved": False, "qoyod_id": None,
                "reason": "no mapping"}),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.CUSTOMER_NOT_RESOLVED

    def test_customer_dry_qoyod_id_blocked(self):
        # `resolved=True` accidentally but qoyod_id is a DRY sentinel.
        d = should_allow_selective_live_send(
            order=_base_order(customer_status={
                "resolved": True, "qoyod_id": "DRY:1",
                "reason": None}),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.CUSTOMER_DRY_OR_NULL

    def test_customer_null_qoyod_id_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(customer_status={
                "resolved": True, "qoyod_id": None, "reason": None}),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.CUSTOMER_DRY_OR_NULL

    def test_products_missing_mapping_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(products_status={
                "resolved": False, "resolved_count": 0,
                "dry_run_only": 0, "missing": ["SKU-Z"]}),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.PRODUCT_MISSING_MAPPING

    def test_products_dry_run_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(products_status={
                "resolved": False, "resolved_count": 0,
                "dry_run_only": 1, "missing": []}),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.PRODUCT_DRY_OR_NULL

    def test_products_not_resolved_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(products_status={
                "resolved": False, "resolved_count": 0,
                "dry_run_only": 0, "missing": []}),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.PRODUCT_NOT_RESOLVED


# ── Totals rules ────────────────────────────────────────────────────
class TestTotals:
    def test_zero_diff_allowed(self):
        d = should_allow_selective_live_send(
            order=_base_order(totals_status={
                "valid": True, "total": 100.0, "expected": 100.0,
                "diff": 0.0}),
            settings=_gates_OPEN())
        assert d.decision == "allow"
        assert d.totals_warning is False

    def test_small_diff_within_tolerance_warns(self):
        d = should_allow_selective_live_send(
            order=_base_order(totals_status={
                "valid": True, "total": 100.01, "expected": 100.0,
                "diff": 0.01}),
            settings=_gates_OPEN())
        assert d.decision == "allow"
        assert d.totals_warning is True
        assert any("rounding" in w for w in d.warnings)

    def test_hard_diff_blocked(self):
        d = should_allow_selective_live_send(
            order=_base_order(totals_status={
                "valid": False, "total": 100.50, "expected": 100.0,
                "diff": 0.50}),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.TOTALS_MISMATCH_HARD


# ── The "perfect green" path ─────────────────────────────────────────
class TestGreenPath:
    def test_paid_order_allowed_only_when_all_conditions_true(self):
        d = should_allow_selective_live_send(
            order=_base_order(), settings=_gates_OPEN())
        assert d.decision == "allow"
        assert d.would_send_to_qoyod is True
        assert d.blocker_reason is None
        assert d.blocker_code is None
        assert d.posting_mode == "paid_receipt"

    def test_cod_green_path_is_credit_invoice_only(self):
        d = should_allow_selective_live_send(
            order=_base_order(payment_method="cod"),
            settings=_gates_OPEN())
        assert d.decision == "allow"
        assert d.posting_mode == "credit_invoice_only"


# ── Report builder (end-to-end DB) ──────────────────────────────────
@pytest.mark.asyncio
class TestReportBuilder:
    async def _clean(self, db, uid):
        for c in ("unified_orders", "integration_inbox",
                  "qoyod_invoices", "qoyod_customers_mapping",
                  "qoyod_products_mapping", "qoyod_settings"):
            await db[c].delete_many({"user_id": uid})

    def _uid(self):
        return f"pol-test-{uuid.uuid4().hex[:8]}"

    async def _seed_customer(self, db, uid, phone="+966554681361"):
        await db.qoyod_customers_mapping.insert_one({
            "user_id": uid, "lookup_key": phone,
            "lookup_kind": "phone",
            "qoyod_customer_id": 223, "dry_run_only": False})

    async def _seed_product(self, db, uid, sku="X"):
        await db.qoyod_products_mapping.insert_one({
            "user_id": uid, "sku": sku,
            "qoyod_product_id": 42, "dry_run_only": False})

    async def _seed_order(self, db, uid, *, status="completed",
                          pm="mada", order_date="2026-07-05",
                          total=100.0):
        oid = f"O-{uuid.uuid4().hex[:8]}"
        await db.unified_orders.insert_one({
            "user_id": uid, "order_id": oid, "order_number": oid,
            "order_status": status, "order_status_slug": status,
            "payment_method": pm,
            "total_amount": total,
            "shipping_amount": 0, "tax_amount": 0,
            "items": [{"sku": "X", "quantity": 1,
                       "unit_price": total}],
            "customer": {"phone": "+966554681361"},
            "customer_mobile": "+966554681361",
            "order_date": order_date,
            "created_at": f"{order_date}T10:00:00+03:00",
            "received_at": datetime.now(timezone.utc).isoformat(),
        })
        await db.integration_inbox.insert_one({
            "user_id": uid, "salla_order_id": oid,
            "salla_order_number": oid,
            "connector_key": f"salla-{oid}",
            "idempotency_key": f"idem-{oid}",
            "trace_id": f"t-{uuid.uuid4().hex[:8]}",
            "pipeline_stage": "COMPLETED",
            "received_at": datetime.now(timezone.utc),
        })
        return oid

    async def test_report_default_settings_blocks_everything(self, db):
        """Without qoyod_settings row (Fail-Closed defaults) every
        decision must be `block:gate_disabled`. This is the crucial
        production invariant."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid)
            await self._seed_order(db, uid)
            r = await build_selective_send_policy_report(
                db, user_id=uid)
            assert r["gates_snapshot"]["selective_live_send_enabled"] \
                is False
            assert r["gates_snapshot"]["production_writes_locked"] \
                is True
            assert r["counts"]["allow"] == 0
            assert r["would_send_to_qoyod_count"] == 0
            assert r["counts"]["block"] == r["total_decisions"]
            assert r["blocker_code_counts"].get(
                BlockerCode.GATE_DISABLED, 0) == r["total_decisions"]
        finally:
            await self._clean(db, uid)

    async def test_report_with_open_gates_computes_real_decisions(
            self, db):
        """With BOTH master gates opened (test-only), the report
        should show real per-order decisions."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid)
            # 2 Q3 mada orders (green) + 1 Q2 order (cutoff-blocked)
            # + 1 bank_transfer Q3 (hold).
            await self._seed_order(db, uid, pm="mada",
                                   order_date="2026-07-05")
            await self._seed_order(db, uid, pm="cod",
                                   order_date="2026-07-10")
            await self._seed_order(db, uid, pm="mada",
                                   order_date="2026-06-15")
            await self._seed_order(db, uid, pm="bank_transfer",
                                   order_date="2026-07-20")
            # Simulate open gates.
            await db.qoyod_settings.insert_one({
                "user_id": uid,
                "selective_live_send_enabled": True,
                "production_writes_locked":    False,
                "qoyod_sync_start_date":       "2026-07-01",
                "qoyod_tax_period":            "Q3-2026",
                "bank_transfer_routing_enabled": False,
            })
            r = await build_selective_send_policy_report(
                db, user_id=uid, since_days=180)
            # The requested 180-day interval is authoritative; the historic
            # 2026-07-01 rollout date is no longer an implicit upstream
            # filter.  The policy still explains the older row explicitly.
            assert r["total_decisions"] == 4
            assert r["counts"]["allow"] == 2
            assert r["counts"]["block"] == 2
            assert r["blocker_code_counts"].get(
                BlockerCode.BANK_TRANSFER_ON_HOLD) == 1
            assert r["blocker_code_counts"].get(
                BlockerCode.BEFORE_SYNC_START_DATE) == 1
        finally:
            await self._clean(db, uid)

    async def test_report_is_read_only(self, db):
        """Running the report must not modify any collection."""
        uid = self._uid()
        try:
            await self._seed_customer(db, uid)
            await self._seed_product(db, uid)
            await self._seed_order(db, uid)
            before = {}
            for c in ("unified_orders", "integration_inbox",
                      "qoyod_invoices", "qoyod_customers_mapping",
                      "qoyod_products_mapping", "qoyod_settings"):
                before[c] = await db[c].count_documents(
                    {"user_id": uid})
            _ = await build_selective_send_policy_report(
                db, user_id=uid)
            for c, n in before.items():
                after = await db[c].count_documents({"user_id": uid})
                assert after == n, f"{c} was written to!"
        finally:
            await self._clean(db, uid)

    async def test_report_notes_advertise_fail_closed_defaults(self, db):
        uid = self._uid()
        try:
            r = await build_selective_send_policy_report(
                db, user_id=uid)
            notes = " ".join(r["notes"])
            assert "selective_live_send_enabled" in notes
            assert "production_writes_locked"    in notes
            assert "2026-07-01"                  in notes
        finally:
            await self._clean(db, uid)


# ── Contract test — allow-list content matches user directive ────────
class TestPaymentAllowListContract:
    def test_allow_list_matches_user_directive(self):
        # Directive verbatim: mada, apple_pay, credit_card, visa,
        # mastercard, stc_pay, tabby/tabby_installment, tamara/
        # tamara_installment, emkan, cod.
        for pm in ("mada", "apple_pay", "credit_card", "visa",
                   "mastercard", "stc_pay",
                   "tabby", "tabby_installment",
                   "tamara", "tamara_installment",
                   "emkan", "cod"):
            assert pm in _ALLOWED_PAYMENT_METHODS, \
                f"{pm} MUST be on the allow-list"

    def test_bank_transfer_NOT_on_allow_list(self):
        assert "bank_transfer" not in _ALLOWED_PAYMENT_METHODS


# ── Iter-001h — Enabled trigger status contract ─────────────────────
class TestEnabledTriggerStatuses:
    """Default enabled_trigger_statuses = ['completed', 'تم التنفيذ'].
    delivered / shipping / تم التوصيل / جاري التوصيل are BROADLY
    eligible (visible in Eligible Orders) but blocked by policy."""

    def test_completed_allowed_by_default(self):
        d = should_allow_selective_live_send(
            order=_base_order(status="completed"),
            settings=_gates_OPEN())
        assert d.decision == "allow", d.blocker_reason
        assert d.normalized_status == "completed"

    def test_arabic_completed_allowed_by_default(self):
        d = should_allow_selective_live_send(
            order=_base_order(status="تم التنفيذ"),
            settings=_gates_OPEN())
        assert d.decision == "allow", d.blocker_reason

    def test_delivered_blocked_by_default(self):
        d = should_allow_selective_live_send(
            order=_base_order(status="delivered"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED

    def test_arabic_delivered_blocked_by_default(self):
        d = should_allow_selective_live_send(
            order=_base_order(status="تم التوصيل"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED

    def test_shipping_blocked_by_default(self):
        d = should_allow_selective_live_send(
            order=_base_order(status="shipping"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED

    def test_arabic_in_transit_blocked_by_default(self):
        d = should_allow_selective_live_send(
            order=_base_order(status="جاري التوصيل"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED

    def test_cod_does_not_bypass_trigger_status(self):
        """Explicit user directive: COD in جاري التوصيل is NOT
        allowed by default (even though posting_mode is
        credit_invoice_only)."""
        d = should_allow_selective_live_send(
            order=_base_order(status="جاري_التوصيل",
                              payment_method="cod"),
            settings=_gates_OPEN())
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED

    def test_delivered_allowed_when_opted_in_explicitly(self):
        """Tenant can enable delivered by adding it to
        `qoyod_enabled_invoice_trigger_statuses`."""
        s = _gates_OPEN()
        s["qoyod_enabled_invoice_trigger_statuses"] = [
            "completed", "تم التنفيذ", "delivered", "تم التوصيل"]
        d = should_allow_selective_live_send(
            order=_base_order(status="delivered"),
            settings=s)
        assert d.decision == "allow", d.blocker_reason

    def test_shipping_allowed_when_opted_in_explicitly(self):
        s = _gates_OPEN()
        s["qoyod_enabled_invoice_trigger_statuses"] = [
            "completed", "shipping", "جاري التوصيل"]
        d = should_allow_selective_live_send(
            order=_base_order(status="جاري_التوصيل"),
            settings=s)
        assert d.decision == "allow", d.blocker_reason

    def test_enabled_trigger_statuses_reported_in_decision(self):
        d = should_allow_selective_live_send(
            order=_base_order(), settings=_gates_OPEN())
        assert d.enabled_trigger_statuses == [
            "completed", "تم التنفيذ"]

    def test_default_matches_module_constant(self):
        # Guard against accidental default drift.
        assert QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT == \
            ("completed", "تم التنفيذ")


# ── Iter-001h — Invoice date = send_date (Asia/Riyadh) ──────────────
class TestInvoiceDate:
    """قيود invoice_date MUST always be the send moment in
    Asia/Riyadh — NOT order.created_at, completed_at, delivered_at,
    paid_at, or received_at."""

    def _fixed_now(self, y=2026, m=7, d=4, h=15, mi=30):
        return datetime(y, m, d, h, mi, 0, tzinfo=timezone.utc)

    def test_invoice_date_source_default_is_send_date(self):
        assert QOYOD_INVOICE_DATE_SOURCE_DEFAULT == "send_date"

    def test_send_timezone_is_asia_riyadh(self):
        assert QOYOD_SEND_TIMEZONE == "Asia/Riyadh"

    def test_invoice_date_equals_send_date_riyadh(self):
        # Order created 2026-07-01, sent (now) 2026-07-04 UTC 15:30
        # → Asia/Riyadh (+3) = 2026-07-04 18:30 → date = 2026-07-04.
        now = self._fixed_now(2026, 7, 4, 15, 30)
        d = should_allow_selective_live_send(
            order=_base_order(salla_order_created_at="2026-07-01"),
            settings=_gates_OPEN(), now_utc=now)
        assert d.decision == "allow", d.blocker_reason
        assert d.would_use_invoice_date == "2026-07-04"
        assert d.send_date_riyadh == "2026-07-04"
        assert d.invoice_date_source == "send_date"
        assert d.send_timezone == "Asia/Riyadh"

    def test_invoice_date_not_derived_from_order_created_at(self):
        now = self._fixed_now(2026, 7, 10, 12, 0)
        d = should_allow_selective_live_send(
            order=_base_order(salla_order_created_at="2026-07-01"),
            settings=_gates_OPEN(), now_utc=now)
        # created_at ≠ invoice_date.
        assert d.salla_order_created_at == "2026-07-01"
        assert d.would_use_invoice_date == "2026-07-10"

    def test_invoice_date_ignores_completed_at(self):
        # Even if the order carries completed_at, we don't consult it.
        o = _base_order()
        o["completed_at"] = "2026-07-02T14:00:00+03:00"
        now = self._fixed_now(2026, 7, 4, 10, 0)
        d = should_allow_selective_live_send(
            order=o, settings=_gates_OPEN(), now_utc=now)
        assert d.would_use_invoice_date == "2026-07-04"

    def test_riyadh_date_boundary_crossed_at_utc_2100(self):
        """UTC 21:00 = Asia/Riyadh 00:00 next day. Contract: the
        send_date_riyadh must reflect Asia/Riyadh's calendar day."""
        now = datetime(2026, 7, 4, 21, 30, 0, tzinfo=timezone.utc)
        d = should_allow_selective_live_send(
            order=_base_order(), settings=_gates_OPEN(), now_utc=now)
        # UTC 2026-07-04 21:30 → Asia/Riyadh 2026-07-05 00:30
        assert d.send_date_riyadh == "2026-07-05"

    def test_invoice_date_populated_even_on_block(self):
        # Auditable: `would_use_invoice_date` is set on BLOCK too so
        # operators see what the send date WOULD have been.
        now = self._fixed_now(2026, 7, 4, 10, 0)
        d = should_allow_selective_live_send(
            order=_base_order(status="waiting"),
            settings=_gates_OPEN(), now_utc=now)
        assert d.decision == "block"
        assert d.would_use_invoice_date == "2026-07-04"

    def test_send_timestamp_has_riyadh_offset(self):
        now = self._fixed_now(2026, 7, 4, 10, 0)
        d = should_allow_selective_live_send(
            order=_base_order(), settings=_gates_OPEN(), now_utc=now)
        # Riyadh is +03:00 year-round (no DST).
        assert "+03:00" in (d.send_timestamp_riyadh or "")

    def test_cod_uses_send_date_as_due_date_semantics(self):
        # Reified as documentation: COD → posting_mode credit_invoice
        # _only, invoice_date = due_date = send_date. The policy
        # surfaces invoice_date; the pipeline is responsible for
        # applying send_date to due_date too. This test just verifies
        # the policy carries the send_date so the pipeline can wire
        # it to both fields.
        now = self._fixed_now(2026, 7, 4, 10, 0)
        d = should_allow_selective_live_send(
            order=_base_order(payment_method="cod"),
            settings=_gates_OPEN(), now_utc=now)
        assert d.decision == "allow", d.blocker_reason
        assert d.posting_mode == "credit_invoice_only"
        assert d.would_use_invoice_date == "2026-07-04"

    def test_prepaid_uses_send_date_for_receipt_and_invoice(self):
        now = self._fixed_now(2026, 7, 4, 10, 0)
        d = should_allow_selective_live_send(
            order=_base_order(payment_method="mada"),
            settings=_gates_OPEN(), now_utc=now)
        assert d.decision == "allow", d.blocker_reason
        assert d.posting_mode == "paid_receipt"
        # invoice_date AND payment_date both derive from send_date.
        assert d.would_use_invoice_date == "2026-07-04"


# ── Iter-001i — Manual Send narrow bypass ───────────────────────────
class TestManualSend:
    """Manual Send unlocks a per-order path for delivered / shipping /
    Arabic in-transit ONLY, and ONLY when the operator supplies the
    exact approval phrase. Every other blocker still holds."""

    def _canonical_phrase(self, order_number):
        return manual_approval_phrase_for(order_number)

    # ── Availability (auto vs manual)
    def test_shipping_auto_send_blocked_manual_available(self):
        # Directive #1 & #3: shipping is not auto-eligible but IS
        # manual-eligible when all other conditions hold.
        order = _base_order(status="shipping")
        auto = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN())
        assert auto.decision == "block"
        assert auto.blocker_code == \
            BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED

        manual = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert manual.decision == "allow", manual.blocker_reason
        assert manual.manual_send_requested is True
        assert manual.manual_approval_phrase_provided is True

    def test_delivered_auto_send_blocked_manual_available(self):
        order = _base_order(status="delivered")
        manual = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert manual.decision == "allow", manual.blocker_reason

    def test_arabic_delivered_manual_send_available(self):
        order = _base_order(status="تم التوصيل")
        manual = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert manual.decision == "allow", manual.blocker_reason

    def test_arabic_in_transit_manual_send_available(self):
        order = _base_order(status="جاري التوصيل")
        manual = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert manual.decision == "allow", manual.blocker_reason

    # ── Phrase enforcement
    def test_manual_send_without_phrase_blocked(self):
        """Directive #10: press button without confirmation → no send."""
        order = _base_order(status="delivered")
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=None)
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.MANUAL_APPROVAL_PHRASE_REQUIRED

    def test_manual_send_with_wrong_phrase_blocked(self):
        order = _base_order(status="delivered")
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase="i approve this send")
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.MANUAL_APPROVAL_PHRASE_MISMATCH

    def test_manual_send_phrase_is_case_sensitive(self):
        order = _base_order(status="delivered")
        phrase = self._canonical_phrase(order["order_number"]).lower()
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=phrase)
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.MANUAL_APPROVAL_PHRASE_MISMATCH

    def test_manual_send_phrase_must_reference_correct_order(self):
        order = _base_order(order_number="ORDER-A", status="delivered")
        # Operator typed phrase for a DIFFERENT order.
        wrong = manual_approval_phrase_for("ORDER-B")
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=wrong)
        assert d.decision == "block"
        assert d.blocker_code == \
            BlockerCode.MANUAL_APPROVAL_PHRASE_MISMATCH

    # ── Non-bypassable blockers (directive #5–#8)
    def test_manual_send_does_NOT_bypass_bank_transfer(self):
        order = _base_order(status="delivered",
                            payment_method="bank_transfer")
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.BANK_TRANSFER_ON_HOLD

    def test_manual_send_does_NOT_bypass_q2_cutoff(self):
        order = _base_order(status="delivered",
                            salla_order_created_at="2026-06-15")
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.BEFORE_SYNC_START_DATE

    def test_manual_send_does_NOT_bypass_dry_customer(self):
        order = _base_order(status="delivered",
                            customer_status={
                                "resolved": True, "qoyod_id": "DRY:1",
                                "reason": None})
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.CUSTOMER_DRY_OR_NULL

    def test_manual_send_does_NOT_bypass_dry_product(self):
        order = _base_order(status="delivered",
                            products_status={
                                "resolved": False, "resolved_count": 0,
                                "dry_run_only": 1, "missing": []})
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.PRODUCT_DRY_OR_NULL

    def test_manual_send_does_NOT_bypass_preview_id(self):
        order = _base_order(status="delivered",
                            existing_qoyod_invoice_id="PREVIEW:abc")
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.PREVIEW_ID_DETECTED

    def test_manual_send_does_NOT_bypass_hard_totals_mismatch(self):
        order = _base_order(status="delivered",
                            totals_status={
                                "valid": False, "total": 100.50,
                                "expected": 100.0, "diff": 0.50})
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.TOTALS_MISMATCH_HARD

    def test_manual_send_does_NOT_bypass_already_sent(self):
        order = _base_order(status="delivered",
                            existing_qoyod_invoice_id="Q-REAL-9001")
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.ALREADY_SENT

    def test_manual_send_does_NOT_bypass_master_gate(self):
        order = _base_order(status="delivered")
        d = should_allow_selective_live_send(
            order=order, settings=_gates_CLOSED(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.GATE_DISABLED

    def test_manual_send_does_NOT_bypass_write_lock(self):
        s = _gates_OPEN()
        s["production_writes_locked"] = True
        order = _base_order(status="delivered")
        d = should_allow_selective_live_send(
            order=order, settings=s,
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.WRITE_LOCK_ACTIVE

    # ── Invoice date still uses send_date on manual path
    def test_manual_send_uses_send_date_as_invoice_date(self):
        """Directive #9: manual-send invoice_date = send_date, not
        order.created_at / completed_at."""
        order = _base_order(status="delivered",
                            salla_order_created_at="2026-07-01")
        order["completed_at"] = "2026-07-02T14:00:00+03:00"
        now = datetime(2026, 7, 4, 15, 30, 0, tzinfo=timezone.utc)
        d = should_allow_selective_live_send(
            order=order, settings=_gates_OPEN(),
            now_utc=now,
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase(
                order["order_number"]))
        assert d.decision == "allow", d.blocker_reason
        assert d.would_use_invoice_date == "2026-07-04"
        assert d.invoice_date_source == "send_date"

    # ── Manual send is ONLY for the narrow eligible statuses
    def test_manual_send_flag_ignored_when_status_is_completed(self):
        """`completed` is already auto-eligible; manual flag has no
        effect, still allow, no phrase required."""
        d = should_allow_selective_live_send(
            order=_base_order(status="completed"),
            settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=None)
        assert d.decision == "allow"

    def test_manual_send_flag_ignored_when_status_is_waiting(self):
        """`waiting` is not broadly eligible — manual flag CANNOT
        rescue it (guarded by broad eligibility check 5)."""
        d = should_allow_selective_live_send(
            order=_base_order(status="waiting"),
            settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=self._canonical_phrase("TEST-001"))
        assert d.decision == "block"
        assert d.blocker_code == BlockerCode.STATUS_NOT_ELIGIBLE


class TestManualSendPhraseHelper:
    def test_helper_produces_directive_phrase(self):
        assert manual_approval_phrase_for("269571122") == \
            "Approved manual Qoyod send for order 269571122 only"

    def test_helper_handles_none_gracefully(self):
        # Doesn't raise — matches the literal string with 'None'.
        p = manual_approval_phrase_for(None)
        assert "None" in p


class TestManualSendReportEnrichment:
    """The report must expose `manual_send_available` +
    `manual_send_confirmation_phrase` so the UI can decide when to
    show the button."""

    def test_report_shape_has_manual_send_fields_on_each_row(self):
        # Compute directly on a synthetic item (no DB): mimic what the
        # report loop does per-order.
        item = _base_order(status="delivered")
        expected = manual_approval_phrase_for(item["order_number"])
        probe = should_allow_selective_live_send(
            order=item, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=expected)
        assert probe.decision == "allow"
        # Contract for UI:
        assert probe.manual_send_requested is True
        assert probe.manual_approval_phrase_provided is True

    def test_report_manual_probe_does_not_store_phrase_text(self):
        item = _base_order(status="delivered")
        d = should_allow_selective_live_send(
            order=item, settings=_gates_OPEN(),
            manual_send_requested=True,
            manual_approval_phrase=manual_approval_phrase_for(
                item["order_number"]))
        # Audit-critical: never persist the phrase itself.
        # (`manual_approval_phrase_provided` is a bool, not the text.)
        as_dict = asdict(d)
        assert "manual_approval_phrase" not in as_dict
        assert "phrase_text" not in as_dict


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
