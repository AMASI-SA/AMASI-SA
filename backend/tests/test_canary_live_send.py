"""Iter-001k+ — Canary Live Send tests.

13 invariants pinned. Every real Qoyod call is mocked. The pipeline
delegate `reprocess_one_order` is fully patched so no HTTP happens.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.canary_live_send import (   # noqa: E402
    CANARY_APPROVAL_PHRASE,
    CANARY_ORDER_NUMBER,
    REQUIRED_QOYOD_PRODUCT_ID,
    REQUIRED_SKU,
    execute_canary_live_send,
)
from tests.test_dry_rca_report import _FakeColl        # noqa: E402


def _ok_settings():
    return [{"user_id": "main",
             "selective_live_send_enabled": False,
             "production_writes_locked":    True}]


def _canary_row(**overrides):
    row = {
        "user_id":              "main",
        "salla_order_number":   CANARY_ORDER_NUMBER,
        "trace_id":             "trace-canary-default-abc123",
        "received_at":          "2026-07-05T12:00:00+00:00",
        "existing_qoyod_invoice_id": "DRY:invoice:xxx",
        "qoyod_customer_id":    "DRY:contact:yyy",
        "salla_order_created_at": "2026-07-05",
        "canonical_payload": {
            "order_number":   CANARY_ORDER_NUMBER,
            "salla_order_created_at": "2026-07-05",
            "payment_method": "tabby_installment",
            "status":         "completed",
            "subtotal":       80.00,
            "shipping_amount": 13.05,
            "tax_amount":     0.00,
            "discount_amount": 0.00,
            "total_amount":   100.00,
            "customer": {
                "mobile":  "+966557951913",
                "email":   "suziyousif9@gmail.com",
                "name":    "سوزان عوض الله",
            },
            "items": [{
                "sku":              REQUIRED_SKU,
                "quantity":         1,
                "unit_price":       80.00,
                "discount_amount":  0.0,
                "tax_amount":       6.95,
                "total":            86.95,
                "qoyod_product_id": "DRY:product:cc",
            }],
        },
    }
    # Deep-merge overrides into canonical.
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(row.get(k), dict):
            row[k].update(v)
        else:
            row[k] = v
    return row


def _build_db(*, row=None, mapping=None, settings=None):
    class DB:
        pass
    db = DB()
    db.qoyod_settings          = _FakeColl(settings or _ok_settings())
    db.integration_inbox       = _FakeColl([row] if row else [])
    db.qoyod_products_mapping  = _FakeColl(mapping if mapping is not None
                                           else [{"user_id": "main",
                                                  "sku": REQUIRED_SKU,
                                                  "qoyod_product_id": 45,
                                                  "dry_run_only": False}])
    audit_inserts: list[dict] = []
    class _AuditColl:
        async def insert_one(self, doc):
            audit_inserts.append(doc)
    db.canary_send_audit_log = _AuditColl()
    db._audit_inserts = audit_inserts    # test hook
    return db


# ── Guard-refusal matrix ────────────────────────────────────────────
@pytest.mark.asyncio
class TestGuardsRefuseCorrectly:

    async def test_guard1_phrase_mismatch(self):
        db = _build_db(row=_canary_row())
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase="WRONG PHRASE")
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 1

    async def test_guard2_wrong_order_number(self):
        db = _build_db(row=_canary_row())
        r = await execute_canary_live_send(
            db, order_number="999999999",
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 2

    async def test_guard3_wrong_payment_method(self):
        row = _canary_row()
        row["canonical_payload"]["payment_method"] = "bank_transfer"
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 3

    async def test_guard4_status_not_completed(self):
        row = _canary_row()
        row["canonical_payload"]["status"] = "shipping"
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 4

    async def test_guard5_created_before_q3_cutoff(self):
        row = _canary_row()
        row["salla_order_created_at"] = "2026-06-15"
        row["canonical_payload"]["salla_order_created_at"] = \
            "2026-06-15"
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 5

    async def test_guard6_real_existing_invoice(self):
        row = _canary_row()
        row["existing_qoyod_invoice_id"] = 999999
        row["canonical_payload"]["existing_qoyod_invoice_id"] = 999999
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 6

    async def test_guard7_wrong_product_id(self):
        db = _build_db(row=_canary_row(),
                       mapping=[{"user_id": "main",
                                 "sku": REQUIRED_SKU,
                                 "qoyod_product_id": 99,
                                 "dry_run_only": False}])
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 7

    async def test_guard8_wrong_phone(self):
        row = _canary_row()
        row["canonical_payload"]["customer"]["mobile"] = "+966500000000"
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 8

    async def test_guard9_wrong_email(self):
        row = _canary_row()
        row["canonical_payload"]["customer"]["email"] = \
            "someone@example.com"
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 9

    async def test_guard9_missing_email(self):
        row = _canary_row()
        row["canonical_payload"]["customer"]["email"] = ""
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 9

    async def test_guard10_totals_invalid_via_monkeypatch(
            self, monkeypatch):
        from integrations.qoyod import canary_live_send as clv
        from integrations.qoyod import eligible_orders as eo
        monkeypatch.setattr(
            eo, "_check_totals",
            lambda _o: {"valid": False, "total": 100.0,
                        "expected": 200.0, "diff": 100.0,
                        "legacy_diff": 0.0,
                        "mezan_vat_rate": 0.15,
                        "payload_date_source": "send_date",
                        "guard_engine": "mezan_vat_15_simulation"})
        db = _build_db(row=_canary_row())
        r = await clv.execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 10

    async def test_guard11_gate_flipped_open_in_db_refuses(self):
        db = _build_db(row=_canary_row(),
                       settings=[{"user_id": "main",
                                  "selective_live_send_enabled": True,
                                  "production_writes_locked": True}])
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 11

    async def test_guard12_writes_unlocked_in_db_refuses(self):
        db = _build_db(row=_canary_row(),
                       settings=[{"user_id": "main",
                                  "selective_live_send_enabled": False,
                                  "production_writes_locked": False}])
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 12


# ── Settings mutation invariance ────────────────────────────────────
@pytest.mark.asyncio
class TestSettingsUntouched:

    async def test_settings_never_mutated_on_refuse(self):
        db = _build_db(row=_canary_row())
        # Snapshot BEFORE.
        before = list(db.qoyod_settings._docs)
        await execute_canary_live_send(
            db, order_number="999",
            approval_phrase="wrong")
        # Snapshot AFTER — identity + values unchanged.
        after = list(db.qoyod_settings._docs)
        assert before == after
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True

    async def test_settings_never_mutated_on_success(self):
        db = _build_db(row=_canary_row())
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-777",
                       "qoyod_customer_id":  "CUST-888",
                       "qoyod_receipt_id":   "RCPT-999",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        # Settings untouched.
        after = list(db.qoyod_settings._docs)
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True


# ── Success path (mocked pipeline) ──────────────────────────────────
@pytest.mark.asyncio
class TestSuccessPath:

    async def test_success_returns_ids_and_records_audit(self):
        db = _build_db(row=_canary_row())
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-777",
                       "qoyod_customer_id":  "CUST-888",
                       "qoyod_receipt_id":   "RCPT-999",
                   })) as mock_pipeline:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        # Pipeline called EXACTLY once, for this order only.
        assert mock_pipeline.call_count == 1
        call_kwargs = mock_pipeline.call_args.kwargs
        assert call_kwargs["order_number"] == CANARY_ORDER_NUMBER
        # Response carries the ids we care about.
        assert r["outcome"] == "SENT"
        assert r["qoyod_invoice_id"]  == "INV-777"
        assert r["qoyod_customer_id"] == "CUST-888"
        assert r["qoyod_receipt_id"]  == "RCPT-999"
        assert r["product_used"]["qoyod_product_id"] == \
            REQUIRED_QOYOD_PRODUCT_ID
        assert r["invoice_date_source"] == "send_date_riyadh"
        # Audit trail: attempt_received → guards_passed → pipeline_result.
        phases = [d["phase"] for d in db._audit_inserts]
        assert phases[0] == "attempt_received"
        assert "guards_passed" in phases
        assert phases[-1] == "pipeline_result"

    async def test_audit_row_written_on_refuse(self):
        db = _build_db(row=_canary_row())
        await execute_canary_live_send(
            db, order_number="999",
            approval_phrase="wrong")
        phases = [d["phase"] for d in db._audit_inserts]
        assert "attempt_received" in phases
        assert "guard_check" in phases
        refused = [d for d in db._audit_inserts
                   if d["phase"] == "guard_check"]
        assert refused[0]["status"] == "refused"
        assert refused[0]["guard_no"] == 1


# ── Production-parity: Fail-Closed default semantics ────────────────
# These tests pin the exact bug reported on Production 2026-02:
# Canary refused with Guard 11 because the settings doc existed but
# was MISSING the `selective_live_send_enabled` field entirely.
# The policy-report endpoint tolerated this (defaulted to False), so
# operators saw a false mismatch. Canary must apply the SAME defaults.
@pytest.mark.asyncio
class TestFailClosedDefaultSemantics:

    async def test_missing_selective_field_treated_as_false(self):
        """Settings doc exists but `selective_live_send_enabled` key
        is absent → must be treated as False (Fail-Closed), so guard
        11 PASSES and canary proceeds to later guards."""
        db = _build_db(row=_canary_row(),
                       settings=[{"user_id": "main",
                                  # selective_live_send_enabled key
                                  # DELIBERATELY OMITTED — same as
                                  # the Production bug repro.
                                  "production_writes_locked": True}])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-1",
                       "qoyod_customer_id":  "CUST-1",
                       "qoyod_receipt_id":   "RCPT-1",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        # Guards 11 & 12 must pass under Fail-Closed defaults.
        assert r["outcome"] == "SENT", (
            f"Canary refused unexpectedly: {r}")

    async def test_missing_writes_locked_field_treated_as_true(self):
        """Settings doc exists but `production_writes_locked` key
        is absent → must be treated as True (Fail-Closed default)."""
        db = _build_db(row=_canary_row(),
                       settings=[{"user_id": "main",
                                  "selective_live_send_enabled": False,
                                  # production_writes_locked OMITTED.
                                  }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-2",
                       "qoyod_customer_id":  "CUST-2",
                       "qoyod_receipt_id":   "RCPT-2",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", (
            f"Canary refused unexpectedly: {r}")

    async def test_settings_doc_completely_missing_uses_defaults(self):
        """No settings doc at all under this user_id → both fields
        default to Fail-Closed values, guards 11/12 pass."""
        db = _build_db(row=_canary_row(), settings=[])  # empty
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-3",
                       "qoyod_customer_id":  "CUST-3",
                       "qoyod_receipt_id":   "RCPT-3",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", (
            f"Canary refused unexpectedly: {r}")


# ── Debug info on REFUSED responses ─────────────────────────────────
@pytest.mark.asyncio
class TestRefuseCarriesSettingsDebug:

    async def test_refuse_carries_settings_debug_block(self):
        db = _build_db(row=_canary_row(),
                       settings=[{"user_id": "main",
                                  "selective_live_send_enabled": True,
                                  "production_writes_locked":    True}])
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 11
        d = r["settings_debug"]
        assert d["settings_source"] == "qoyod_settings"
        assert d["settings_user_id"] == "main"
        assert d["settings_doc_present"] is True
        assert d["raw_selective_live_send_enabled"] is True
        assert d["raw_selective_live_send_enabled_type"] == "bool"
        assert d["raw_production_writes_locked"] is True
        assert d["raw_production_writes_locked_type"] == "bool"
        # Debug must NEVER leak API keys / secrets.
        blob = str(d).lower()
        for banned in ("api_key", "credentials", "token",
                       "password", "secret"):
            assert banned not in blob

    async def test_refuse_debug_present_even_when_guard1_fires(self):
        """Even when guards 1 or 2 short-circuit before settings
        are loaded, the response must still carry a best-effort
        settings_debug snapshot."""
        db = _build_db(row=_canary_row())
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase="wrong")
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 1
        assert "settings_debug" in r
        assert r["settings_debug"]["settings_user_id"] == "main"


# ── user_id parameterisation (Production parity) ───────────────────
@pytest.mark.asyncio
class TestUserIdParameterisation:

    async def test_settings_query_uses_provided_user_id(self):
        """When caller passes a custom user_id, canary must query
        qoyod_settings under THAT id (not a hardcoded 'main')."""
        db = _build_db(
            row=_canary_row(),
            settings=[{"user_id": "custom_tenant_xyz",
                       "selective_live_send_enabled": False,
                       "production_writes_locked":    True}])
        # Replicate the row + mapping under the custom tenant.
        row = _canary_row()
        row["user_id"] = "custom_tenant_xyz"
        db.integration_inbox = _FakeColl([row])
        db.qoyod_products_mapping = _FakeColl(
            [{"user_id": "custom_tenant_xyz",
              "sku": REQUIRED_SKU,
              "qoyod_product_id": 45,
              "dry_run_only": False}])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-U",
                       "qoyod_customer_id":  "CUST-U",
                       "qoyod_receipt_id":   "RCPT-U",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE,
                user_id="custom_tenant_xyz")
        assert r["outcome"] == "SENT", r
        # Pipeline was called with the same user_id.
        assert (mock_pipe.call_args.kwargs["user_id"]
                == "custom_tenant_xyz")


# ── Guard #5 date extraction (Production parity) ───────────────────
# Repro for the Production bug reported 2026-02: canary refused with
# `created_at_missing` because the row surfaced the date under
# `canonical_payload.order_date` (or raw_payload.data.date.date) and
# NOT under `salla_order_created_at`. Canary must accept any of the
# supported source fields — identical to eligible_orders / policy
# report — and MUST NOT fall back to completed_at / delivered_at /
# received_at.
@pytest.mark.asyncio
class TestGuard5DateExtractionMatchesEligibleOrders:

    async def _run(self, row):
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-DT",
                       "qoyod_customer_id":  "CUST-DT",
                       "qoyod_receipt_id":   "RCPT-DT",
                   })):
            return await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)

    async def test_accepts_created_at_from_canonical_order_date(self):
        row = _canary_row()
        # Strip primary field, surface only `order_date`.
        row["canonical_payload"].pop("salla_order_created_at", None)
        row.pop("salla_order_created_at", None)
        row["canonical_payload"]["order_date"] = "2026-07-05"
        row["canonical_payload"]["order_date_inferred"] = False
        r = await self._run(row)
        assert r["outcome"] == "SENT", (
            f"Guard 5 refused unexpectedly: {r}")

    async def test_accepts_created_at_from_raw_payload_data_date_date(
            self):
        row = _canary_row()
        row["canonical_payload"].pop("salla_order_created_at", None)
        row.pop("salla_order_created_at", None)
        row["canonical_payload"].pop("order_date", None)
        row["raw_payload"] = {
            "data": {
                "date": {"date": "2026-07-05 10:30:00"},
            }
        }
        r = await self._run(row)
        assert r["outcome"] == "SENT", (
            f"Guard 5 refused unexpectedly: {r}")

    async def test_accepts_created_at_from_raw_payload_data_created_at(
            self):
        row = _canary_row()
        row["canonical_payload"].pop("salla_order_created_at", None)
        row.pop("salla_order_created_at", None)
        row["canonical_payload"].pop("order_date", None)
        row["raw_payload"] = {
            "data": {
                "created_at": "2026-07-05T10:30:00+03:00",
            }
        }
        r = await self._run(row)
        assert r["outcome"] == "SENT", (
            f"Guard 5 refused unexpectedly: {r}")

    async def test_refuses_when_all_date_sources_missing_with_debug(
            self):
        row = _canary_row()
        # Wipe EVERY known date source.
        row["canonical_payload"].pop("salla_order_created_at", None)
        row["canonical_payload"].pop("order_date", None)
        row["canonical_payload"].pop("created_at", None)
        row.pop("salla_order_created_at", None)
        row["raw_payload"] = {}  # no data
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 5
        assert r["code"] == "created_at_missing"
        # Debug block MUST be present and complete.
        d = r["date_debug"]
        af = d["available_date_fields"]
        for k in ("canonical_payload.salla_order_created_at",
                  "canonical_payload.order_date",
                  "canonical_payload.created_at",
                  "raw_payload.created_at",
                  "raw_payload.data.date.date",
                  "raw_payload.data.created_at"):
            assert k in af, f"missing key in debug: {k}"
        assert d["extracted_salla_order_created_at"] is None
        assert d["q3_cutoff_iso"] == "2026-07-01"

    async def test_completed_at_is_never_used_as_fallback(self):
        row = _canary_row()
        # Wipe every legitimate source AND set completed_at only.
        row["canonical_payload"].pop("salla_order_created_at", None)
        row["canonical_payload"].pop("order_date", None)
        row["canonical_payload"].pop("created_at", None)
        row.pop("salla_order_created_at", None)
        row["canonical_payload"]["completed_at"] = "2026-07-05"
        row["raw_payload"] = {}
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 5
        assert r["code"] == "created_at_missing"

    async def test_delivered_at_is_never_used_as_fallback(self):
        row = _canary_row()
        row["canonical_payload"].pop("salla_order_created_at", None)
        row["canonical_payload"].pop("order_date", None)
        row["canonical_payload"].pop("created_at", None)
        row.pop("salla_order_created_at", None)
        row["canonical_payload"]["delivered_at"] = "2026-07-05"
        row["raw_payload"] = {}
        db = _build_db(row=row)
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 5

    async def test_269629400_fixture_passes_guard5_via_order_date(self):
        # Exact Production fixture shape: date lives only in
        # canonical_payload.order_date (no salla_order_created_at,
        # no raw_payload data.date.date).
        row = {
            "user_id": "main",
            "salla_order_number": CANARY_ORDER_NUMBER,
            "trace_id": "trace-prod-269629400-canonical",
            "received_at": "2026-07-01T09:00:00+00:00",
            "existing_qoyod_invoice_id": "DRY:invoice:xxx",
            "qoyod_customer_id": "DRY:contact:yyy",
            "canonical_payload": {
                "order_number": CANARY_ORDER_NUMBER,
                "order_date": "2026-07-01",
                "order_date_inferred": False,
                "payment_method": "tabby_installment",
                "status": "completed",
                "subtotal": 80.00,
                "shipping_amount": 13.05,
                "tax_amount": 0.00,
                "discount_amount": 0.00,
                "total_amount": 100.00,
                "customer": {
                    "mobile": "+966557951913",
                    "email": "suziyousif9@gmail.com",
                    "name": "سوزان عوض الله",
                },
                "items": [{
                    "sku": REQUIRED_SKU,
                    "quantity": 1,
                    "unit_price": 80.00,
                    "discount_amount": 0.0,
                    "tax_amount": 6.95,
                    "total": 86.95,
                    "qoyod_product_id": "DRY:product:cc",
                }],
            },
        }
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-269",
                       "qoyod_customer_id":  "CUST-269",
                       "qoyod_receipt_id":   "RCPT-269",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", (
            f"269629400 fixture failed guard 5: {r}")

    async def test_no_qoyod_api_call_when_guard5_refuses(self):
        """When Guard 5 refuses, the delegate pipeline (which owns
        the actual Qoyod HTTP calls) must NEVER be invoked."""
        row = _canary_row()
        row["canonical_payload"].pop("salla_order_created_at", None)
        row["canonical_payload"].pop("order_date", None)
        row["canonical_payload"].pop("created_at", None)
        row.pop("salla_order_created_at", None)
        row["raw_payload"] = {}
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 5
        assert mock_pipe.call_count == 0, (
            "reprocess_one_order was called despite guard 5 refuse")
        assert r["no_qoyod_api_calls"] is True


# ── Internal confirm-token synthesis (Production-parity) ───────────
# Repro for Production bug 2026-02: canary reached the pipeline but
# passed `confirm='CANARY-269629400-CONFIRM'` while
# `reprocess_one_order` expects `REPROCESS-<order_number>`. The
# operator must NEVER supply this token — canary must synthesise it
# internally from `CONFIRM_TOKEN_TEMPLATE`.
@pytest.mark.asyncio
class TestInternalConfirmToken:

    async def test_success_passes_reprocess_confirm_token(self):
        db = _build_db(row=_canary_row())
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-CT",
                       "qoyod_customer_id":  "CUST-CT",
                       "qoyod_receipt_id":   "RCPT-CT",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        # confirm token MUST equal 'REPROCESS-<canary_order>' verbatim.
        kwargs = mock_pipe.call_args.kwargs
        assert kwargs["confirm"] == f"REPROCESS-{CANARY_ORDER_NUMBER}"
        # approval_phrase to reprocess MUST match its template.
        assert kwargs["approval_phrase"] == (
            f"Approved to send order {CANARY_ORDER_NUMBER} only")
        # order_number pinned to canary target.
        assert kwargs["order_number"] == CANARY_ORDER_NUMBER

    async def test_confirm_token_uses_reprocess_template_symbol(self):
        """Pin the source-of-truth: canary imports
        CONFIRM_TOKEN_TEMPLATE from one_shot_reprocess (never
        hard-codes the string). This guards against future drift."""
        from integrations.qoyod.one_shot_reprocess import (
            CONFIRM_TOKEN_TEMPLATE, APPROVAL_PHRASE_TEMPLATE,
        )
        assert CONFIRM_TOKEN_TEMPLATE == "REPROCESS-{order_number}"
        assert APPROVAL_PHRASE_TEMPLATE == (
            "Approved to send order {order_number} only")
        # Ensure canary source imports these symbols (protects
        # against someone re-inlining the raw literals).
        import integrations.qoyod.canary_live_send as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "CONFIRM_TOKEN_TEMPLATE" in src
        assert "APPROVAL_PHRASE_TEMPLATE" in src
        # And that we no longer hard-code the (wrong) old sentinel.
        assert "CANARY-" + CANARY_ORDER_NUMBER + "-CONFIRM" not in src

    async def test_operator_never_asked_for_second_confirm_string(self):
        """Endpoint signature: only approval_phrase + order_number
        are operator inputs. `confirm` MUST not appear anywhere in
        the route wrapper's expected payload keys."""
        # Read the route source and confirm it exposes only
        # `order_number` + `approval_phrase` from the request payload.
        with open("/app/backend/integrations/qoyod/routes.py",
                  encoding="utf-8") as f:
            src = f.read()
        # Locate the canary route body.
        i = src.find("/admin/canary-live-send")
        j = src.find("@router", i + 10)
        body = src[i:j]
        # Operator payload keys.
        assert "payload.get(\"order_number\"" in body
        assert "payload.get(\"approval_phrase\"" in body
        # NO extra `confirm` field pulled from payload.
        assert "payload.get(\"confirm\"" not in body
        assert "payload.get('confirm'" not in body

    async def test_oneshot_refused_returns_pipeline_error(self):
        """If reprocess_one_order raises OneShotRefused, canary
        returns PIPELINE_ERROR (not SENT) without Qoyod calls."""
        from integrations.qoyod.one_shot_reprocess import OneShotRefused
        db = _build_db(row=_canary_row())
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=OneShotRefused(
                       "confirm_token_mismatch",
                       "confirm must equal 'REPROCESS-<order_number>'",
                       expected="REPROCESS-269629400",
                       received="X"))):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "PIPELINE_ERROR"
        assert r["code"] == "OneShotRefused"
        # Diagnostic must reveal which templates were used, so future
        # drift is trivially spot-checked.
        assert r["internal_confirm_used"] == "REPROCESS-269629400"
        assert (r["internal_approval_phrase_used"]
                == f"Approved to send order {CANARY_ORDER_NUMBER} only")

    async def test_wrong_phrase_never_reaches_reprocess(self):
        db = _build_db(row=_canary_row())
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase="WRONG")
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 1
        assert mock_pipe.call_count == 0

    async def test_wrong_order_never_reaches_reprocess(self):
        db = _build_db(row=_canary_row())
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number="999999999",
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 2
        assert mock_pipe.call_count == 0

    async def test_no_qoyod_calls_no_settings_writes_on_any_refuse(
            self):
        """Any REFUSED / PIPELINE_ERROR path: audit rows may be
        appended, but qoyod_settings MUST NOT be mutated and Qoyod
        API MUST NOT be called."""
        from integrations.qoyod.one_shot_reprocess import OneShotRefused
        db = _build_db(row=_canary_row())
        before = list(db.qoyod_settings._docs)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=OneShotRefused(
                       "some_reason", "boom",
                       expected="x", received="y"))):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "PIPELINE_ERROR"
        after = list(db.qoyod_settings._docs)
        assert after == before
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True


# ── trace_id selection & ambiguity (Production-parity) ────────────
# Repro for Production bug 2026-02: canary passed all 14 guards then
# reprocess refused because integration_inbox had ≥2 rows for
# order_number=269629400. Canary now:
#   • Fetches ALL rows.
#   • Applies row-level canary criteria to each (deterministic).
#   • If exactly one row passes → uses its trace_id.
#   • If none pass → normal guard diagnostic (with duplicate_debug).
#   • If more than one passes → refuses `ambiguous_order_rows`.
@pytest.mark.asyncio
class TestTraceIdSelection:

    async def test_single_row_passes_trace_id_to_reprocess(self):
        db = _build_db(row=_canary_row())
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-T1",
                       "qoyod_customer_id":  "CUST-T1",
                       "qoyod_receipt_id":   "RCPT-T1",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        # trace_id was passed to reprocess_one_order.
        kwargs = mock_pipe.call_args.kwargs
        assert kwargs["trace_id"] == "trace-canary-default-abc123"
        # And surfaced in the top-level response.
        assert r["selected_trace_id"] == "trace-canary-default-abc123"
        assert r["selection_debug"]["passing_rows_count"] == 1

    async def test_two_rows_one_deterministic_picks_the_valid_one(
            self):
        """Two rows exist. Only ONE satisfies canary criteria — that
        specific trace_id is passed to reprocess. The other row is
        summarised (with reject reason) in duplicate_debug."""
        good = _canary_row()
        good["trace_id"] = "trace-good-777"
        bad = _canary_row()
        bad["trace_id"] = "trace-bad-666"
        # Break `bad` on ONE criterion only.
        bad["canonical_payload"]["payment_method"] = "bank_transfer"
        db = _build_db(row=good)
        db.integration_inbox = _FakeColl([bad, good])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-T2",
                       "qoyod_customer_id":  "CUST-T2",
                       "qoyod_receipt_id":   "RCPT-T2",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert mock_pipe.call_args.kwargs["trace_id"] == \
            "trace-good-777"
        assert r["selected_trace_id"] == "trace-good-777"
        # duplicate_debug enumerates both rows with per-row reason.
        sd = r["selection_debug"]
        assert sd["passing_rows_count"] == 1
        assert sd["duplicate_rows_count"] == 2
        assert set(sd["duplicate_trace_ids"]) == {
            "trace-good-777", "trace-bad-666"}
        # Each summary carries only the safe non-PII fields.
        for s in sd["duplicate_rows_summary"]:
            for banned in ("email", "phone", "mobile", "name",
                           "raw_payload"):
                assert banned not in s

    async def test_two_rows_both_pass_refuses_ambiguous(self):
        r1 = _canary_row()
        r1["trace_id"] = "trace-dup-1"
        r2 = _canary_row()
        r2["trace_id"] = "trace-dup-2"
        db = _build_db(row=r1)
        db.integration_inbox = _FakeColl([r1, r2])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["code"] == "ambiguous_order_rows"
        # NEVER dispatches to Qoyod.
        assert mock_pipe.call_count == 0
        assert r["no_qoyod_api_calls"] is True
        # Ambiguity debug present.
        dd = r["duplicate_debug"]
        assert dd["passing_rows_count"] == 2
        assert set(dd["duplicate_trace_ids"]) == {
            "trace-dup-1", "trace-dup-2"}

    async def test_selected_row_without_trace_id_refuses(self):
        row = _canary_row()
        row["trace_id"] = None      # simulate legacy row w/o trace_id
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["code"] == "selected_row_missing_trace_id"
        assert mock_pipe.call_count == 0

    async def test_no_qoyod_calls_on_ambiguity(self):
        r1 = _canary_row()
        r1["trace_id"] = "trace-a"
        r2 = _canary_row()
        r2["trace_id"] = "trace-b"
        db = _build_db(row=r1)
        db.integration_inbox = _FakeColl([r1, r2])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert mock_pipe.call_count == 0

    async def test_no_settings_writes_on_ambiguity(self):
        r1 = _canary_row()
        r1["trace_id"] = "trace-x"
        r2 = _canary_row()
        r2["trace_id"] = "trace-y"
        db = _build_db(row=r1)
        db.integration_inbox = _FakeColl([r1, r2])
        before = list(db.qoyod_settings._docs)
        await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        after = list(db.qoyod_settings._docs)
        assert after == before
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True

    async def test_internal_confirm_remains_reprocess_template(self):
        """Even under trace_id path, the internal confirm token
        must still be 'REPROCESS-<order_number>'."""
        db = _build_db(row=_canary_row())
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-C",
                       "qoyod_customer_id":  "CUST-C",
                       "qoyod_receipt_id":   "RCPT-C",
                   })) as mock_pipe:
            await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        kwargs = mock_pipe.call_args.kwargs
        assert kwargs["confirm"] == f"REPROCESS-{CANARY_ORDER_NUMBER}"
        assert kwargs["approval_phrase"] == (
            f"Approved to send order {CANARY_ORDER_NUMBER} only")


# ── Static / lint-style invariants ─────────────────────────────────
class TestStaticInvariants:

    def test_module_never_updates_qoyod_settings(self):
        import integrations.qoyod.canary_live_send as mod
        src = open(mod.__file__, encoding="utf-8").read()
        # No writes to qoyod_settings.
        assert "qoyod_settings.update_one" not in src
        assert "qoyod_settings.replace_one" not in src
        assert "qoyod_settings.insert_one" not in src

    def test_module_no_direct_qoyod_api_client_import(self):
        import integrations.qoyod.canary_live_send as mod
        src = open(mod.__file__, encoding="utf-8").read()
        # Delegates via reprocess_one_order — never imports the
        # HTTP client directly.
        assert "QoyodAPIClient" not in src
        assert "httpx" not in src
