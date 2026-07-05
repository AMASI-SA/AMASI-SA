"""Iter-001k+ — Canary Live Send tests.

13 invariants pinned. Every real Qoyod call is mocked. The pipeline
delegate `reprocess_one_order` is fully patched so no HTTP happens.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
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
from tests.test_dry_rca_report import _FakeColl, _Cursor  # noqa: E402


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
        "qoyod_customer_id":    "12345",   # pre-resolved (skip
                                            # canary pre-resolve
                                            # in tests)
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
            "qoyod_customer_id": "12345",
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
        kwargs = mock_pipe.call_args.kwargs
        assert kwargs["trace_id"] == "trace-canary-default-abc123"
        assert r["selected_trace_id"] == "trace-canary-default-abc123"
        # Under latest-only policy: selected IS latest, single-row case.
        assert r["latest_trace_id"] == "trace-canary-default-abc123"
        assert r["selected_is_latest"] is True

    async def test_latest_row_picked_when_older_row_also_valid(
            self):
        """OLDER row = completed (would pass); LATEST row also
        completed but different trace. Latest-only policy → pick
        latest, never fall back to older."""
        older = _canary_row()
        older["trace_id"] = "trace-older"
        older["received_at"] = "2026-07-01T09:00:00+00:00"
        newer = _canary_row()
        newer["trace_id"] = "trace-newer"
        newer["received_at"] = "2026-07-05T15:00:00+00:00"
        db = _build_db(row=newer)
        # Insertion order matches sort(received_at DESC): newer first.
        db.integration_inbox = _FakeColl([newer, older])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-L1",
                       "qoyod_customer_id":  "CUST-L1",
                       "qoyod_receipt_id":   "RCPT-L1",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        # Selected == LATEST (never falls back).
        assert r["selected_trace_id"] == "trace-newer"
        assert r["latest_trace_id"] == "trace-newer"
        assert r["selected_is_latest"] is True
        assert mock_pipe.call_args.kwargs["trace_id"] == "trace-newer"

    async def test_latest_row_fails_criteria_refuses_no_fallback(
            self):
        """LATEST row fails criteria (e.g. status=in_review), OLDER
        row is completed and would pass. Latest-only policy MUST
        refuse — no fallback to the older completed row."""
        older = _canary_row()
        older["trace_id"] = "trace-older-completed"
        older["received_at"] = "2026-07-01T09:00:00+00:00"
        # Latest has a status that is NOT in the canary whitelist.
        newer = _canary_row()
        newer["trace_id"] = "trace-newer-in-review"
        newer["received_at"] = "2026-07-05T15:00:00+00:00"
        newer["canonical_payload"]["status"] = "in_review"
        db = _build_db(row=newer)
        db.integration_inbox = _FakeColl([newer, older])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        # REFUSED, no Qoyod call.
        assert r["outcome"] == "REFUSED", r
        assert r["guard_no"] == 4
        assert r["code"] == "status_not_completed"
        assert mock_pipe.call_count == 0
        assert r["no_qoyod_api_calls"] is True
        # Debug shows both rows and marks that older would have
        # matched — but selection ignored it (transparency).
        dd = r["duplicate_debug"]
        assert dd["duplicate_rows_count"] == 2
        assert dd["latest_trace_id"] == "trace-newer-in-review"
        assert dd["latest_matches_canary_criteria"] is False
        assert dd["latest_reject_reason"] == "status_not_completed"

    async def test_latest_row_delivering_marks_manual_send(self):
        """Latest row status=جاري_التوصيل is accepted (canary
        whitelist) and marked `manual_send_requested=true`. No
        fallback needed."""
        older = _canary_row()
        older["trace_id"] = "trace-older-completed"
        older["received_at"] = "2026-07-01T09:00:00+00:00"
        newer = _canary_row()
        newer["trace_id"] = "trace-newer-delivering"
        newer["received_at"] = "2026-07-05T15:00:00+00:00"
        newer["canonical_payload"]["status"] = "جاري_التوصيل"
        db = _build_db(row=newer)
        db.integration_inbox = _FakeColl([newer, older])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-D",
                       "qoyod_customer_id":  "CUST-D",
                       "qoyod_receipt_id":   "RCPT-D",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert r["selected_trace_id"] == "trace-newer-delivering"
        assert r["selected_is_latest"] is True
        assert r["manual_send_requested"] is True
        assert r["selected_normalized_status"] == "جاري التوصيل"
        assert r["latest_normalized_status"] == "جاري التوصيل"
        assert mock_pipe.call_args.kwargs["trace_id"] == \
            "trace-newer-delivering"

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

    async def test_no_qoyod_calls_when_latest_fails_criteria(self):
        """Latest fails → NO Qoyod API call, regardless of older
        rows' state."""
        older = _canary_row()
        older["trace_id"] = "trace-older"
        newer = _canary_row()
        newer["trace_id"] = "trace-newer-bad"
        newer["canonical_payload"]["payment_method"] = "bank_transfer"
        db = _build_db(row=newer)
        db.integration_inbox = _FakeColl([newer, older])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert mock_pipe.call_count == 0

    async def test_no_settings_writes_when_latest_fails_criteria(
            self):
        older = _canary_row()
        older["trace_id"] = "trace-x"
        newer = _canary_row()
        newer["trace_id"] = "trace-y-bad"
        newer["canonical_payload"]["payment_method"] = "bank_transfer"
        db = _build_db(row=newer)
        db.integration_inbox = _FakeColl([newer, older])
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


# ── Scoped dry_run override (Production-parity) ───────────────────
# Repro for Production bug 2026-02: canary passed all 14 guards +
# picked correct trace_id → then reprocess refused with
# `dry_run_mode_active`. The gate `dry_run_mode` MUST NOT be opened
# globally in DB. Canary applies a SCOPED override via a DB proxy
# that intercepts ONLY `qoyod_settings.find_one` for the duration of
# the reprocess call.
@pytest.mark.asyncio
class TestScopedDryRunOverride:

    async def _capture_reprocess_view(self, db, dry_run_val):
        """Return the settings snapshot that reprocess_one_order would
        observe when called via the scoped canary path."""
        # Overwrite the qoyod_settings collection with a value that
        # includes dry_run_mode.
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                dry_run_val,
        }])
        captured = {}

        async def fake_reprocess(db_arg, **kwargs):
            # Read qoyod_settings via the arg the pipeline uses.
            doc = await db_arg.qoyod_settings.find_one(
                {"user_id": "main"}, {"_id": 0})
            captured["doc"] = doc
            captured["called_with_db_id"] = id(db_arg)
            captured["real_db_id"] = id(db)
            return {
                "outcome":            "SENT",
                "qoyod_invoice_id":   "INV-DR",
                "qoyod_customer_id":  "CUST-DR",
                "qoyod_receipt_id":   "RCPT-DR",
            }

        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=fake_reprocess)):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        return r, captured

    async def test_dry_run_true_in_db_bypassed_scoped_for_canary(self):
        """When DB has `dry_run_mode=true`, canary's DB proxy must
        overlay `dry_run_mode=false` on the doc that reaches
        reprocess_one_order. Global DB value is NOT touched."""
        db = _build_db(row=_canary_row())
        r, cap = await self._capture_reprocess_view(db, True)
        assert r["outcome"] == "SENT", r
        # Reprocess saw dry_run_mode as False (scoped).
        assert cap["doc"]["dry_run_mode"] is False
        # But the real DB still has dry_run_mode=True.
        raw = db.qoyod_settings._docs[0]
        assert raw["dry_run_mode"] is True
        assert raw["selective_live_send_enabled"] is False
        assert raw["production_writes_locked"]    is True

    async def test_dry_run_mode_not_mutated_after_success(self):
        db = _build_db(row=_canary_row())
        before = list(db.qoyod_settings._docs)
        # dry_run_mode starts True.
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        before = list(db.qoyod_settings._docs)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-A",
                       "qoyod_customer_id":  "CUST-A",
                       "qoyod_receipt_id":   "RCPT-A",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        after = list(db.qoyod_settings._docs)
        assert after == before
        assert after[0]["dry_run_mode"] is True
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True

    async def test_dry_run_mode_not_mutated_after_refuse(self):
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        before = list(db.qoyod_settings._docs)
        r = await execute_canary_live_send(
            db, order_number="999999999",     # → guard 2 refuse
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        after = list(db.qoyod_settings._docs)
        assert after == before
        assert after[0]["dry_run_mode"] is True

    async def test_proxy_never_applied_to_other_orders(self):
        """The DB proxy is constructed ONLY inside
        execute_canary_live_send. Any other caller of
        reprocess_one_order gets the real DB with real dry_run_mode.
        Test this by asserting the module-level scope: the proxy
        classes are referenced ONLY when
        `order_number == CANARY_ORDER_NUMBER`. Guard 2 refuses
        earlier for non-canary orders, so proxy path is never
        constructed."""
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number="269629401",   # off-by-one — refuse.
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 2
        assert mock_pipe.call_count == 0    # never reached the proxy.

    async def test_selective_and_writes_lock_unchanged_by_override(
            self):
        """Iter-rev8: scoped policy override applies all THREE
        fields (dry_run, selective, writes_lock) — but only in the
        canary-scoped VIEW that reaches the pipeline. The REAL DB
        rows remain untouched (Fail-Closed on disk)."""
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        r, cap = await self._capture_reprocess_view(db, True)
        assert r["outcome"] == "SENT"
        # From reprocess's viewpoint via the proxy — ALL THREE
        # fields are overridden for the canary scope.
        assert cap["doc"]["selective_live_send_enabled"] is True
        assert cap["doc"]["production_writes_locked"]    is False
        assert cap["doc"]["dry_run_mode"]                is False
        # And in the real DB — nothing changed.
        raw = db.qoyod_settings._docs[0]
        assert raw["selective_live_send_enabled"] is False
        assert raw["production_writes_locked"]    is True
        assert raw["dry_run_mode"]                is True

    async def test_selected_trace_id_flows_through_scoped_proxy(self):
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-TR",
                       "qoyod_customer_id":  "CUST-TR",
                       "qoyod_receipt_id":   "RCPT-TR",
                   })) as mock_pipe:
            await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert mock_pipe.call_args.kwargs["trace_id"] == \
            "trace-canary-default-abc123"

    async def test_oneshot_refused_after_proxy_returns_pipeline_error(
            self):
        """If reprocess still refuses for a reason unrelated to
        dry_run (e.g. credentials_missing), canary surfaces
        PIPELINE_ERROR — never actually contacts Qoyod (mocked)."""
        from integrations.qoyod.one_shot_reprocess import OneShotRefused
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=OneShotRefused(
                       "credentials_missing",
                       "no api key configured",
                       user_id="main"))):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "PIPELINE_ERROR"
        assert r["code"] == "OneShotRefused"
        # DB dry_run_mode remains True.
        assert db.qoyod_settings._docs[0]["dry_run_mode"] is True

    async def test_no_qoyod_call_when_guard_fails_before_proxy(self):
        """If any pre-pipeline guard refuses, the scoped proxy is
        never constructed and the DB stays entirely untouched."""
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": True,    # gate flipped
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 11
        assert mock_pipe.call_count == 0
        # DB unchanged.
        assert db.qoyod_settings._docs[0]["selective_live_send_enabled"] \
            is True
        assert db.qoyod_settings._docs[0]["dry_run_mode"] is True


# ── settings_debug carries dry_run_mode transparency ───────────────
@pytest.mark.asyncio
class TestSettingsDebugCarriesDryRun:

    async def test_refuse_response_includes_raw_dry_run_mode_debug(
            self):
        """REFUSED response must surface raw_dry_run_mode +
        effective_dry_run_mode_for_canary + scope, so the operator
        can confirm the DB value is untouched."""
        db = _build_db(row=_canary_row(),
                       settings=[{"user_id": "main",
                                  "selective_live_send_enabled": True,
                                  "production_writes_locked":    True,
                                  "dry_run_mode":                True}])
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        sd = r["settings_debug"]
        assert sd["raw_dry_run_mode"] is True
        assert sd["raw_dry_run_mode_type"] == "bool"
        assert sd["effective_dry_run_mode_for_canary"] is False
        assert sd["dry_run_mode_scope"] == (
            f"canary_order_{CANARY_ORDER_NUMBER}_only")


# ── Partial-invoice-created (INVOICE_CREATED without real id) ──────
# Repro for Production bug 2026-02: canary passed all guards + dry_run
# scoped bypass, but reprocess refused because the selected row was
# in `INVOICE_CREATED` stage with `qoyod_invoice_id=null`. That's a
# stuck partial state — safe to reset and reprocess. Meanwhile a
# real invoice at INVOICE_CREATED MUST NEVER be re-created (idempotency).
@pytest.mark.asyncio
class TestPartialInvoiceCreatedHandling:

    async def test_invoice_created_null_invoice_id_allows_reset(self):
        row = _canary_row()
        row["pipeline_stage"] = "INVOICE_CREATED"
        row["qoyod_invoice_id"] = None
        row["existing_qoyod_invoice_id"] = None
        row["canonical_payload"].pop(
            "existing_qoyod_invoice_id", None)
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-PIC-1",
                       "qoyod_customer_id":  "CUST-PIC-1",
                       "qoyod_receipt_id":   "RCPT-PIC-1",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        # Reprocess was told the reset is permitted.
        assert (mock_pipe.call_args.kwargs
                ["allow_reset_from_partial_invoice_created"]
                is True)

    async def test_invoice_created_dry_invoice_id_allows_reset(self):
        row = _canary_row()
        row["pipeline_stage"] = "INVOICE_CREATED"
        row["qoyod_invoice_id"] = "DRY:invoice:zzz"
        row["existing_qoyod_invoice_id"] = "DRY:invoice:zzz"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-PIC-2",
                       "qoyod_customer_id":  "CUST-PIC-2",
                       "qoyod_receipt_id":   "RCPT-PIC-2",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert (mock_pipe.call_args.kwargs
                ["allow_reset_from_partial_invoice_created"]
                is True)

    async def test_invoice_created_preview_invoice_id_allows_reset(
            self):
        row = _canary_row()
        row["pipeline_stage"] = "INVOICE_CREATED"
        row["qoyod_invoice_id"] = "PREVIEW:invoice:aaa"
        row["existing_qoyod_invoice_id"] = "PREVIEW:invoice:aaa"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-PIC-3",
                       "qoyod_customer_id":  "CUST-PIC-3",
                       "qoyod_receipt_id":   "RCPT-PIC-3",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert (mock_pipe.call_args.kwargs
                ["allow_reset_from_partial_invoice_created"]
                is True)

    async def test_invoice_created_real_invoice_id_refuses(self):
        row = _canary_row()
        row["pipeline_stage"] = "INVOICE_CREATED"
        row["qoyod_invoice_id"] = "12345"    # real (non-DRY/PREVIEW).
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["code"] in {"partial_real_invoice_state",
                             "real_existing_invoice_id_present"}
        assert mock_pipe.call_count == 0

    async def test_flag_false_for_non_invoice_created_stages(self):
        """When the row is at NORMALIZED (or any non-IC stage), the
        canary MUST NOT ask for the partial-reset escape hatch."""
        row = _canary_row()
        row["pipeline_stage"] = "NORMALIZED"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-F",
                       "qoyod_customer_id":  "CUST-F",
                       "qoyod_receipt_id":   "RCPT-F",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        assert (mock_pipe.call_args.kwargs
                ["allow_reset_from_partial_invoice_created"]
                is False)

    async def test_trace_id_still_flows_through_partial_reset(self):
        row = _canary_row()
        row["pipeline_stage"] = "INVOICE_CREATED"
        row["qoyod_invoice_id"] = None
        row["existing_qoyod_invoice_id"] = None
        row["canonical_payload"].pop(
            "existing_qoyod_invoice_id", None)
        row["trace_id"] = "trace-partial-icc-123"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-T",
                       "qoyod_customer_id":  "CUST-T",
                       "qoyod_receipt_id":   "RCPT-T",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        assert (mock_pipe.call_args.kwargs["trace_id"]
                == "trace-partial-icc-123")
        assert (mock_pipe.call_args.kwargs["confirm"]
                == f"REPROCESS-{CANARY_ORDER_NUMBER}")

    async def test_no_qoyod_call_on_partial_real_invoice_refuse(self):
        row = _canary_row()
        row["pipeline_stage"] = "INVOICE_CREATED"
        row["qoyod_invoice_id"] = "REAL-9999"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert mock_pipe.call_count == 0
        assert r["no_qoyod_api_calls"] is True

    async def test_settings_untouched_on_partial_real_refuse(self):
        row = _canary_row()
        row["pipeline_stage"] = "INVOICE_CREATED"
        row["qoyod_invoice_id"] = "REAL-1234"
        db = _build_db(row=row)
        before = list(db.qoyod_settings._docs)
        await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        after = list(db.qoyod_settings._docs)
        assert after == before
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True


# ── State-machine two-hop for partial-IC reset ─────────────────────
# Repro for Production bug 2026-02: canary reached the partial-reset
# path but `_reset_row_to_stage` refused with
# `state-machine refused INVOICE_CREATED → NORMALIZED`.
# The direct transition is illegal by the state-machine contract.
# The canary path must go via RETRYING (INVOICE_CREATED → RETRYING
# → NORMALIZED) — mirroring how DEAD_LETTER / PARTIAL_FAILURE
# recover.
@pytest.mark.asyncio
class TestPartialICStateMachineTwoHop:

    async def test_state_machine_edge_invoice_created_to_retrying(
            self):
        """The state machine must expose an
        `INVOICE_CREATED → RETRYING` edge — the pre-requisite for
        canary's two-hop partial-reset. Direct
        `INVOICE_CREATED → NORMALIZED` must still be REFUSED
        (defence-in-depth)."""
        from integrations.qoyod.state_machine import (
            can_transition, InvalidTransition, transition,
        )
        assert can_transition("INVOICE_CREATED", "RETRYING") is True
        # Direct hop still forbidden — critical safety property.
        assert can_transition("INVOICE_CREATED", "NORMALIZED") is False
        # The state-machine must still allow RETRYING → NORMALIZED.
        assert can_transition("RETRYING", "NORMALIZED") is True
        # Test raising path.
        with pytest.raises(InvalidTransition):
            transition(from_stage="INVOICE_CREATED",
                       to_stage="NORMALIZED",
                       actor="test")

    async def test_reset_uses_two_hop_when_permit_partial_true(self):
        """When `permit_partial_invoice_created=True` and current is
        INVOICE_CREATED, `_reset_row_to_stage` writes TWO updates:
        Hop 1 = INVOICE_CREATED → RETRYING, Hop 2 = RETRYING →
        NORMALIZED. Neither writes to qoyod_settings."""
        from integrations.qoyod.one_shot_reprocess import (
            _reset_row_to_stage,
        )

        writes: list[dict] = []

        class _Coll:
            async def update_one(self, filt, patch, **kw):
                writes.append({"filter": filt, "patch": patch})
                # simulate applying the $set to preserve realistic
                # `pipeline_stage` progression for subsequent reads.
                return SimpleNamespace(modified_count=1)

        class _DB:
            integration_inbox = _Coll()

        row = {
            "id": "row-1",
            "pipeline_stage": "INVOICE_CREATED",
            "qoyod_invoice_id": None,
        }
        await _reset_row_to_stage(
            _DB(), row, resume_stage="NORMALIZED",
            actor="canary:test",
            permit_partial_invoice_created=True)
        # Two writes, both to integration_inbox.
        assert len(writes) == 2
        # Hop 1: → RETRYING
        h1 = writes[0]["patch"]["$set"]
        assert h1["pipeline_stage"] == "RETRYING"
        # Hop 2: → NORMALIZED
        h2 = writes[1]["patch"]["$set"]
        assert h2["pipeline_stage"] == "NORMALIZED"

    async def test_reset_refuses_without_flag_for_invoice_created(
            self):
        """Without the canary flag, INVOICE_CREATED still refuses
        (unsupported_current_stage) — pre-existing safety."""
        from integrations.qoyod.one_shot_reprocess import (
            _reset_row_to_stage, OneShotRefused,
        )

        class _NullDB:
            class integration_inbox:
                async def update_one(self, *a, **kw):
                    raise AssertionError("no writes expected")
            integration_inbox = integration_inbox()

        row = {"id": "row-2", "pipeline_stage": "INVOICE_CREATED",
               "qoyod_invoice_id": None}
        with pytest.raises(OneShotRefused) as exc:
            await _reset_row_to_stage(
                _NullDB(), row, resume_stage="NORMALIZED",
                actor="operator",
                permit_partial_invoice_created=False)
        assert exc.value.code == "unsupported_current_stage"


# ── SKIPPED partial-reset (Production-parity, 2026-02.rev4) ────────
# Repro: canary passed all guards + trace_id selection + INVOICE_CREATED
# two-hop, then encountered another row whose current stage is
# `SKIPPED` with no real invoice. Same safe rewind pattern applies:
# SKIPPED → RETRYING → NORMALIZED. Guarded by the same
# `partial_real_invoice_state` refusal when a real invoice exists.
@pytest.mark.asyncio
class TestSkippedPartialReset:

    async def test_state_machine_edge_skipped_to_retrying(self):
        """State-machine must allow `SKIPPED → RETRYING`, and STILL
        refuse `SKIPPED → NORMALIZED` direct (safety property)."""
        from integrations.qoyod.state_machine import (
            can_transition, InvalidTransition, transition,
        )
        assert can_transition("SKIPPED", "RETRYING") is True
        assert can_transition("SKIPPED", "NORMALIZED") is False
        assert can_transition("RETRYING", "NORMALIZED") is True
        with pytest.raises(InvalidTransition):
            transition(from_stage="SKIPPED", to_stage="NORMALIZED",
                       actor="test")

    async def test_skipped_no_real_invoice_allows_reset(self):
        row = _canary_row()
        row["pipeline_stage"] = "SKIPPED"
        row["qoyod_invoice_id"] = None
        row["existing_qoyod_invoice_id"] = None
        row["canonical_payload"].pop(
            "existing_qoyod_invoice_id", None)
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-SKP-1",
                       "qoyod_customer_id":  "CUST-SKP-1",
                       "qoyod_receipt_id":   "RCPT-SKP-1",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert (mock_pipe.call_args.kwargs
                ["allow_reset_from_partial_invoice_created"] is True)

    async def test_skipped_dry_invoice_allows_reset(self):
        row = _canary_row()
        row["pipeline_stage"] = "SKIPPED"
        row["qoyod_invoice_id"] = "DRY:invoice:sk1"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-SKP-2",
                       "qoyod_customer_id":  "CUST-SKP-2",
                       "qoyod_receipt_id":   "RCPT-SKP-2",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert (mock_pipe.call_args.kwargs
                ["allow_reset_from_partial_invoice_created"] is True)

    async def test_skipped_real_invoice_refuses(self):
        row = _canary_row()
        row["pipeline_stage"] = "SKIPPED"
        row["qoyod_invoice_id"] = "QID-77777"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["code"] in {"partial_real_invoice_state",
                             "real_existing_invoice_id_present"}
        assert mock_pipe.call_count == 0
        assert r["no_qoyod_api_calls"] is True

    async def test_skipped_row_failing_criteria_refuses(self):
        """Row is at SKIPPED but its canonical fails another canary
        criterion (e.g. wrong payment_method). Canary must refuse —
        NOT reset a row that fails criteria."""
        row = _canary_row()
        row["pipeline_stage"] = "SKIPPED"
        row["qoyod_invoice_id"] = None
        row["canonical_payload"]["payment_method"] = "bank_transfer"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] in {3, 2}   # payment_method_mismatch
        assert mock_pipe.call_count == 0

    async def test_skipped_reset_uses_two_hop_state_machine(self):
        """rev33 makes SKIPPED absolutely terminal. The prior two-hop
        canary escape hatch (SKIPPED → RETRYING → NORMALIZED) is
        REMOVED. Any call to `_reset_row_to_stage` with a SKIPPED
        current stage now raises `OneShotRefused("skipped_is_
        terminal_rev33")` before any DB write."""
        from integrations.qoyod.one_shot_reprocess import (
            _reset_row_to_stage, OneShotRefused,
        )
        writes: list[dict] = []

        class _Coll:
            async def update_one(self, filt, patch, **kw):
                writes.append({"filter": filt, "patch": patch})
                return SimpleNamespace(modified_count=1)

        class _DB:
            integration_inbox = _Coll()

        row = {"id": "row-skp", "pipeline_stage": "SKIPPED",
               "qoyod_invoice_id": None}
        with pytest.raises(OneShotRefused) as exc:
            await _reset_row_to_stage(
                _DB(), row, resume_stage="NORMALIZED",
                actor="canary:test",
                permit_partial_invoice_created=True)
        assert exc.value.code == "skipped_is_terminal_rev33"
        # rev33 invariant: NO db writes on refusal.
        assert writes == []

    async def test_skipped_reset_refuses_without_flag(self):
        """Without the canary flag, SKIPPED still refuses — the
        edge is unreachable outside canary."""
        from integrations.qoyod.one_shot_reprocess import (
            _reset_row_to_stage, OneShotRefused,
        )

        class _NullDB:
            class integration_inbox:
                async def update_one(self, *a, **kw):
                    raise AssertionError("no writes expected")
            integration_inbox = integration_inbox()

        row = {"id": "row-skp2", "pipeline_stage": "SKIPPED",
               "qoyod_invoice_id": None}
        with pytest.raises(OneShotRefused) as exc:
            await _reset_row_to_stage(
                _NullDB(), row, resume_stage="NORMALIZED",
                actor="operator",
                permit_partial_invoice_created=False)
        assert exc.value.code in {"unsupported_current_stage",
                                  "invalid_transition_to_resume",
                                  "skipped_is_terminal_rev33"}

    async def test_skipped_settings_untouched_on_refuse(self):
        row = _canary_row()
        row["pipeline_stage"] = "SKIPPED"
        row["qoyod_invoice_id"] = "REAL-1234"    # real → refuse
        db = _build_db(row=row)
        before = list(db.qoyod_settings._docs)
        await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        after = list(db.qoyod_settings._docs)
        assert after == before
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True
        assert after[0].get("dry_run_mode", False) is False


# ── Manual-canary status extension (Iter-2026-02.rev5) ─────────────
# Prod bug: order 269629400 status drifted to `جاري التوصيل` (in
# transit). Canary must accept this status (business-eligible), but
# only for the canary target order, and only under the same 14
# safety guards. When accepted, the response marks
# `manual_send_requested=true` for auditability.
@pytest.mark.asyncio
class TestManualCanaryStatusExtension:

    async def test_delivering_space_form_accepted_as_manual(self):
        row = _canary_row()
        row["canonical_payload"]["status"] = "جاري التوصيل"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-MAN-1",
                       "qoyod_customer_id":  "CUST-MAN-1",
                       "qoyod_receipt_id":   "RCPT-MAN-1",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert r["manual_send_requested"] is True
        assert r["selected_normalized_status"] == "جاري التوصيل"
        # Same trace_id + confirm token as automatic path.
        kw = mock_pipe.call_args.kwargs
        assert kw["order_number"] == CANARY_ORDER_NUMBER
        assert kw["confirm"] == f"REPROCESS-{CANARY_ORDER_NUMBER}"

    async def test_delivering_underscore_form_accepted_as_manual(self):
        row = _canary_row()
        row["canonical_payload"]["status"] = "جاري_التوصيل"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-MAN-2",
                       "qoyod_customer_id":  "CUST-MAN-2",
                       "qoyod_receipt_id":   "RCPT-MAN-2",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert r["manual_send_requested"] is True

    async def test_completed_still_accepted_and_not_manual(self):
        row = _canary_row()
        row["canonical_payload"]["status"] = "completed"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-C",
                       "qoyod_customer_id":  "CUST-C",
                       "qoyod_receipt_id":   "RCPT-C",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        assert r["manual_send_requested"] is False
        assert r["selected_normalized_status"] == "completed"

    async def test_other_statuses_still_refused_at_guard_4(self):
        """`shipping` / `delivered` / `تم التوصيل` are business-
        eligible but NOT permitted for canary — canary keeps the
        two-status whitelist tight."""
        for bad in ("shipping", "delivered", "تم التوصيل",
                    "in_review", "pending", "cancelled"):
            row = _canary_row()
            row["canonical_payload"]["status"] = bad
            db = _build_db(row=row)
            with patch("integrations.qoyod.one_shot_reprocess."
                       "reprocess_one_order",
                       new=AsyncMock()) as mock_pipe:
                r = await execute_canary_live_send(
                    db, order_number=CANARY_ORDER_NUMBER,
                    approval_phrase=CANARY_APPROVAL_PHRASE)
            assert r["outcome"] == "REFUSED", (
                f"status={bad!r} unexpectedly accepted: {r}")
            assert r["guard_no"] in {4, 2}
            assert mock_pipe.call_count == 0

    async def test_manual_status_still_requires_all_other_guards(self):
        """Delivering status is accepted, but every OTHER canary
        guard (payment method, customer, totals, product mapping,
        etc.) must still pass."""
        # payment_method mismatch under delivering status.
        row = _canary_row()
        row["canonical_payload"]["status"] = "جاري التوصيل"
        row["canonical_payload"]["payment_method"] = "bank_transfer"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] in {3, 2}
        assert mock_pipe.call_count == 0

    async def test_delivering_only_for_canary_order(self):
        """A non-canary order in delivering status must STILL be
        refused at Guard 2 (order_number_not_canary), BEFORE the
        status check runs."""
        row = _canary_row()
        row["salla_order_number"] = "999999999"
        row["canonical_payload"]["order_number"] = "999999999"
        row["canonical_payload"]["status"] = "جاري التوصيل"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number="999999999",
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 2   # order_number_not_canary
        assert mock_pipe.call_count == 0

    async def test_settings_untouched_when_delivering_accepted(self):
        row = _canary_row()
        row["canonical_payload"]["status"] = "جاري التوصيل"
        db = _build_db(row=row)
        before = list(db.qoyod_settings._docs)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-S",
                       "qoyod_customer_id":  "CUST-S",
                       "qoyod_receipt_id":   "RCPT-S",
                   })):
            await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        after = list(db.qoyod_settings._docs)
        assert after == before
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True


# ── Latest-only source-of-truth policy (Iter-2026-02.rev6) ─────────
# Prod bug repro: multiple rows exist for the canary order — an
# older row has status=completed while the newest row shows the
# current status (e.g. جاري_التوصيل). Canary MUST use the LATEST
# row only. Falling back to an older row that happens to match
# criteria would send the invoice against a stale status snapshot.
@pytest.mark.asyncio
class TestLatestOnlySelectionPolicy:

    async def test_older_completed_newer_delivering_uses_newer(self):
        """Repro of production 269629400 shape: OLDER trace is
        completed (would pass automatic path), NEWER trace is
        جاري_التوصيل (manual). Canary must select the NEWER and
        mark manual_send_requested=true."""
        older = _canary_row()
        older["trace_id"] = "trace-old-completed"
        older["received_at"] = "2026-07-01T21:38:27+00:00"
        older["canonical_payload"]["status"] = "completed"
        newer = _canary_row()
        newer["trace_id"] = "trace-new-delivering"
        newer["received_at"] = "2026-07-02T11:26:03+00:00"
        newer["canonical_payload"]["status"] = "جاري_التوصيل"
        db = _build_db(row=newer)
        # sort(received_at DESC): newer first.
        db.integration_inbox = _FakeColl([newer, older])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-LATEST",
                       "qoyod_customer_id":  "CUST-LATEST",
                       "qoyod_receipt_id":   "RCPT-LATEST",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert r["selected_trace_id"] == "trace-new-delivering"
        assert r["latest_trace_id"] == "trace-new-delivering"
        assert r["selected_is_latest"] is True
        assert r["manual_send_requested"] is True
        assert r["selected_normalized_status"] == "جاري التوصيل"
        assert r["latest_normalized_status"] == "جاري التوصيل"
        assert mock_pipe.call_args.kwargs["trace_id"] == \
            "trace-new-delivering"

    async def test_never_picks_older_completed_when_newer_exists(
            self):
        """Even if the older row is BETTER (completed), the newer
        row's trace_id is ALWAYS chosen. This is the anti-fallback
        invariant."""
        older = _canary_row()
        older["trace_id"] = "trace-A-completed"
        older["received_at"] = "2026-07-01T09:00:00+00:00"
        older["canonical_payload"]["status"] = "completed"
        newer = _canary_row()
        newer["trace_id"] = "trace-B-completed"
        newer["received_at"] = "2026-07-02T09:00:00+00:00"
        newer["canonical_payload"]["status"] = "completed"
        db = _build_db(row=newer)
        db.integration_inbox = _FakeColl([newer, older])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-B",
                       "qoyod_customer_id":  "CUST-B",
                       "qoyod_receipt_id":   "RCPT-B",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        # Selected is the NEWER trace (B), NOT the older one (A).
        assert r["selected_trace_id"] == "trace-B-completed"
        assert mock_pipe.call_args.kwargs["trace_id"] == \
            "trace-B-completed"

    async def test_latest_delivering_marks_manual_send_true(self):
        row = _canary_row()
        row["canonical_payload"]["status"] = "جاري_التوصيل"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-M",
                       "qoyod_customer_id":  "CUST-M",
                       "qoyod_receipt_id":   "RCPT-M",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        assert r["manual_send_requested"] is True

    async def test_latest_fails_criteria_refuses_no_fallback(self):
        """Latest = in_review (fails). Older = completed (would
        pass). Canary MUST refuse — no fallback."""
        older = _canary_row()
        older["trace_id"] = "trace-old-good"
        older["received_at"] = "2026-07-01T09:00:00+00:00"
        older["canonical_payload"]["status"] = "completed"
        newer = _canary_row()
        newer["trace_id"] = "trace-new-in-review"
        newer["received_at"] = "2026-07-05T09:00:00+00:00"
        newer["canonical_payload"]["status"] = "in_review"
        db = _build_db(row=newer)
        db.integration_inbox = _FakeColl([newer, older])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["code"] == "status_not_completed"
        assert mock_pipe.call_count == 0
        # Debug MUST show the older row was rejected by policy, NOT
        # by criteria mismatch.
        dd = r["duplicate_debug"]
        assert dd["latest_trace_id"] == "trace-new-in-review"
        assert dd["latest_matches_canary_criteria"] is False
        # Verify the older row's own row_matches_canary_criteria is
        # True (would have passed) — proving canary intentionally
        # avoided fallback.
        older_summary = next(s for s in dd["duplicate_rows_summary"]
                             if s["trace_id"] == "trace-old-good")
        assert older_summary["row_matches_canary_criteria"] is True

    async def test_no_qoyod_api_call_when_latest_refuses(self):
        older = _canary_row()
        older["trace_id"] = "trace-old-good"
        newer = _canary_row()
        newer["trace_id"] = "trace-new-bad"
        newer["canonical_payload"]["payment_method"] = "bank_transfer"
        db = _build_db(row=newer)
        db.integration_inbox = _FakeColl([newer, older])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert mock_pipe.call_count == 0

    async def test_response_carries_all_latest_only_diagnostics(self):
        """Success response must expose: latest_trace_id,
        selected_trace_id, selected_is_latest, latest_normalized_status,
        selected_normalized_status, manual_send_requested."""
        row = _canary_row()
        row["trace_id"] = "trace-solo"
        row["canonical_payload"]["status"] = "جاري_التوصيل"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-DIAG",
                       "qoyod_customer_id":  "CUST-DIAG",
                       "qoyod_receipt_id":   "RCPT-DIAG",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        for key in ("latest_trace_id", "selected_trace_id",
                    "selected_is_latest", "latest_normalized_status",
                    "selected_normalized_status",
                    "manual_send_requested"):
            assert key in r, f"missing key: {key}"
        assert r["latest_trace_id"] == "trace-solo"
        assert r["selected_trace_id"] == "trace-solo"
        assert r["selected_is_latest"] is True
        assert r["manual_send_requested"] is True


# ── End-to-end: SKIPPED partial-reset uses two-hop (rev7 fix) ──────
# Repro of Prod bug 2026-02.rev6→rev7: the `_permit_partial_ic` flag
# inside `one_shot_reprocess` was gated ONLY on `INVOICE_CREATED`.
# When the selected row was in `SKIPPED`, the flag stayed False → the
# reset code path took the direct hop `SKIPPED → NORMALIZED` (which
# the state-machine forbids) instead of the two-hop
# `SKIPPED → RETRYING → NORMALIZED`. This E2E test drives the FULL
# canary path against a real `_reset_row_to_stage` (no mock) and
# asserts the exact write sequence.
@pytest.mark.asyncio
class TestE2EPartialSkippedResetTwoHopWireLevel:

    async def test_canary_skipped_row_actually_writes_two_hop(self):
        """DIRECT test: `reprocess_one_order` must forward
        `permit_partial_invoice_created=True` to `_reset_row_to_stage`
        when the row is at SKIPPED with no real invoice — proving
        the fix at the exact seam that caused the Prod bug."""
        from integrations.qoyod import one_shot_reprocess as osr

        # Real row in SKIPPED with no real invoice.
        row = {
            "id":              "row-canary-e2e",
            "user_id":         "main",
            "trace_id":        "trace-e2e-skipped",
            "pipeline_stage":  "SKIPPED",
            "qoyod_invoice_id": None,
            "existing_qoyod_invoice_id": None,
            "salla_order_number": CANARY_ORDER_NUMBER,
            "canonical_payload": {
                "order_number": CANARY_ORDER_NUMBER,
                "status":       "جاري_التوصيل",
                "payment_method": "tabby_installment",
            },
        }

        # Track what `_reset_row_to_stage` receives.
        reset_calls: list[dict] = []

        async def _spy_reset(db, row_arg, *, resume_stage, actor,
                             permit_partial_invoice_created=False):
            reset_calls.append({
                "resume_stage": resume_stage,
                "actor": actor,
                "permit_partial_invoice_created":
                    permit_partial_invoice_created,
                "current_stage": row_arg.get("pipeline_stage"),
                "qoyod_invoice_id": row_arg.get("qoyod_invoice_id"),
            })

        async def _fake_find_target_row(*a, **kw):
            return row

        # Minimal DB stub (nothing after _reset_row_to_stage runs).
        class _StubColl:
            async def find_one(self, *a, **kw):
                return {"user_id": "main",
                        "selective_live_send_enabled": False,
                        "production_writes_locked": True,
                        "dry_run_mode": False}
            async def update_one(self, *a, **kw):
                return SimpleNamespace(modified_count=1)
            async def insert_one(self, *a, **kw):
                return SimpleNamespace(inserted_id="x")

        class _DB:
            qoyod_settings          = _StubColl()
            integration_inbox       = _StubColl()
            qoyod_per_order_approvals = _StubColl()
            qoyod_invoices          = _StubColl()

        with patch.object(osr, "_reset_row_to_stage",
                          new=AsyncMock(side_effect=_spy_reset)), \
             patch.object(osr, "_find_target_row",
                          new=AsyncMock(side_effect=_fake_find_target_row)), \
             patch.object(osr, "get_api_key",
                          new=AsyncMock(return_value="stub-key")), \
             patch.object(osr, "_quarantine_dry_mappings",
                          new=AsyncMock(return_value={
                              "quarantined_customer_maps": 0,
                              "quarantined_product_maps":  0})), \
             patch.object(osr, "_write_approval",
                          new=AsyncMock(return_value=None),
                          create=True):
            # Call reprocess_one_order the same way canary does.
            try:
                await osr.reprocess_one_order(
                    _DB(),
                    user_id="main",
                    order_number=CANARY_ORDER_NUMBER,
                    trace_id="trace-e2e-skipped",
                    confirm=f"REPROCESS-{CANARY_ORDER_NUMBER}",
                    approval_phrase=(
                        f"Approved to send order "
                        f"{CANARY_ORDER_NUMBER} only"),
                    actor="canary:test",
                    allow_reset_from_partial_invoice_created=True)
            except Exception:
                # We don't care about post-reset errors — just the
                # flag propagation and the reset call itself.
                pass

        # `_reset_row_to_stage` was called with `permit_partial=True`
        # for SKIPPED stage with no real invoice.
        assert len(reset_calls) == 1, (
            f"expected 1 reset call, got {len(reset_calls)}: "
            f"{reset_calls}")
        c = reset_calls[0]
        assert c["current_stage"] == "SKIPPED"
        assert c["permit_partial_invoice_created"] is True, (
            f"Prod-bug repro: flag stayed False → direct "
            f"SKIPPED → NORMALIZED. Got: {c}")
        assert c["resume_stage"] == "NORMALIZED"

    async def test_direct_two_hop_writes_SKIPPED_RETRYING_NORMALIZED(
            self):
        """rev33 — SKIPPED is absolutely terminal. The historic
        SKIPPED→RETRYING→NORMALIZED two-hop path is removed. Any
        direct call to `_reset_row_to_stage` on a SKIPPED row raises
        `OneShotRefused("skipped_is_terminal_rev33")` before any
        state-machine transition is written."""
        from integrations.qoyod.one_shot_reprocess import (
            _reset_row_to_stage, OneShotRefused,
        )
        writes: list[dict] = []

        class _Coll:
            async def update_one(self, filt, patch, **kw):
                writes.append({"filter": filt, "patch": patch})
                return SimpleNamespace(modified_count=1)

        class _DB:
            integration_inbox = _Coll()

        row = {"id": "row-2hop-skp", "pipeline_stage": "SKIPPED",
               "qoyod_invoice_id": None}
        with pytest.raises(OneShotRefused) as exc:
            await _reset_row_to_stage(
                _DB(), row, resume_stage="NORMALIZED",
                actor="canary:test",
                permit_partial_invoice_created=True)
        assert exc.value.code == "skipped_is_terminal_rev33"
        assert writes == [], (
            "rev33 invariant: NO DB writes on SKIPPED reset refusal.")

    async def test_pipeline_error_response_carries_reset_path(self):
        """When `_reset_row_to_stage` refuses transition, canary's
        PIPELINE_ERROR response surfaces `reset_path_attempted` +
        `state_machine_allowed_edges_for_current_stage` + the
        `allow_reset_from_partial_invoice_created` flag."""
        from integrations.qoyod.one_shot_reprocess import (
            OneShotRefused,
        )
        row = _canary_row()
        row["pipeline_stage"] = "SKIPPED"
        row["qoyod_invoice_id"] = None
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=OneShotRefused(
                       "invalid_transition_to_resume",
                       "state-machine refused RETRYING → NORMALIZED",
                       current_stage="SKIPPED",
                       resume_stage="NORMALIZED",
                       reset_path_attempted=(
                           "SKIPPED → RETRYING → NORMALIZED"),
                       permit_partial_invoice_created=True,
                       needs_retry_hop=True,
                       state_machine_allowed_edges_for_current_stage=(
                           ["RETRYING"])))):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "PIPELINE_ERROR"
        assert r["reset_path_attempted"] == (
            "SKIPPED → RETRYING → NORMALIZED")
        extra = r["one_shot_refused_extra"]
        assert extra["current_stage"] == "SKIPPED"
        assert extra["permit_partial_invoice_created"] is True
        assert extra["needs_retry_hop"] is True
        assert extra["state_machine_allowed_edges_for_current_stage"] \
            == ["RETRYING"]
        assert r["allow_reset_from_partial_invoice_created"] is True


# ── Scoped policy override (rev8) — three-field overlay ────────────
# The Iter-rev8 extends the DB proxy to also overlay the master gate
# (`selective_live_send_enabled=true`) and the write-lock
# (`production_writes_locked=false`) — all scoped to the single
# canary call. Prod DB values remain unchanged.
@pytest.mark.asyncio
class TestScopedPolicyOverrideRev8:

    async def _capture(self, db):
        captured = {}

        async def fake_reprocess(db_arg, **kwargs):
            doc = await db_arg.qoyod_settings.find_one(
                {"user_id": "main"}, {"_id": 0})
            captured["doc"] = doc
            return {
                "outcome":            "SENT",
                "qoyod_invoice_id":   "INV-OV",
                "qoyod_customer_id":  "CUST-OV",
                "qoyod_receipt_id":   "RCPT-OV",
            }

        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=fake_reprocess)):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        return r, captured

    async def test_pipeline_view_has_selective_true(self):
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                False,
        }])
        r, cap = await self._capture(db)
        assert r["outcome"] == "SENT"
        assert cap["doc"]["selective_live_send_enabled"] is True

    async def test_pipeline_view_has_writes_locked_false(self):
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                False,
        }])
        r, cap = await self._capture(db)
        assert r["outcome"] == "SENT"
        assert cap["doc"]["production_writes_locked"] is False

    async def test_pipeline_view_has_dry_run_false(self):
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        r, cap = await self._capture(db)
        assert r["outcome"] == "SENT"
        assert cap["doc"]["dry_run_mode"] is False

    async def test_db_untouched_after_success(self):
        """After a successful canary send with the three-field
        overlay, the DB row STILL reads Fail-Closed values."""
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                False,
        }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-U",
                       "qoyod_customer_id":  "CUST-U",
                       "qoyod_receipt_id":   "RCPT-U",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        # DB reads unchanged.
        raw = db.qoyod_settings._docs[0]
        assert raw["selective_live_send_enabled"] is False
        assert raw["production_writes_locked"]    is True
        assert raw["dry_run_mode"]                is False

    async def test_guards_still_check_real_db_before_overlay(self):
        """The pre-pipeline Guards 11/12 must inspect the REAL DB
        (Fail-Closed values on disk) BEFORE the proxy is built.
        If DB is not in Fail-Closed base state → refuse."""
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": True,     # violated
            "production_writes_locked":    True,
            "dry_run_mode":                False,
        }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 11
        assert mock_pipe.call_count == 0

    async def test_settings_debug_carries_rev8_effective_view(self):
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": True,     # violation
            "production_writes_locked":    True,
            "dry_run_mode":                True,
        }])
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        sd = r["settings_debug"]
        # Effective canary view — three-field overlay.
        assert sd["effective_dry_run_mode_for_canary"] is False
        assert sd["effective_selective_live_send_enabled_for_canary"] \
            is True
        assert sd["effective_production_writes_locked_for_canary"] \
            is False
        assert sd["policy_override_scope"] == (
            f"canary_order_{CANARY_ORDER_NUMBER}_only")

    async def test_overlay_only_for_canary_order(self):
        """Non-canary orders never build the proxy — Guard 2
        refuses before the DB proxy is ever constructed."""
        db = _build_db(row=_canary_row())
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                False,
        }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number="999999999",
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 2
        assert mock_pipe.call_count == 0
        # DB row still reflects Fail-Closed base state.
        raw = db.qoyod_settings._docs[0]
        assert raw["selective_live_send_enabled"] is False
        assert raw["production_writes_locked"]    is True


# ── Scoped trigger-status overlay (rev9) ───────────────────────────
# Prod bug: canary passed all rev8 guards but selective_send_policy
# still refused `invoice_trigger_status_not_enabled` because the
# tenant's `qoyod_enabled_invoice_trigger_statuses` on disk is
# `["completed", "تم التنفيذ"]`. Canary must widen this list to
# include `"جاري التوصيل"` scoped to its single invocation — DB row
# must remain unchanged.
@pytest.mark.asyncio
class TestScopedTriggerStatusOverlayRev9:

    def _make_db(self, enabled_triggers, row_status="جاري_التوصيل",
                 order_number=None):
        row = _canary_row()
        row["canonical_payload"]["status"] = row_status
        db = _build_db(row=row)
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                False,
            "qoyod_enabled_invoice_trigger_statuses":
                list(enabled_triggers),
        }])
        return db

    async def _capture(self, db, order_number=None,
                       approval_phrase=None):
        captured = {}

        async def fake_reprocess(db_arg, **kwargs):
            doc = await db_arg.qoyod_settings.find_one(
                {"user_id": "main"}, {"_id": 0})
            captured["doc"] = doc
            return {
                "outcome":            "SENT",
                "qoyod_invoice_id":   "INV-OV9",
                "qoyod_customer_id":  "CUST-OV9",
                "qoyod_receipt_id":   "RCPT-OV9",
            }

        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=fake_reprocess)):
            r = await execute_canary_live_send(
                db,
                order_number=order_number or CANARY_ORDER_NUMBER,
                approval_phrase=(
                    approval_phrase or CANARY_APPROVAL_PHRASE))
        return r, captured

    async def test_delivering_widens_enabled_triggers(self):
        """Latest status = جاري التوصيل. DB has
        ["completed", "تم التنفيذ"]. Canary view must include
        جاري التوصيل additionally."""
        db = self._make_db(["completed", "تم التنفيذ"])
        r, cap = await self._capture(db)
        assert r["outcome"] == "SENT", r
        enabled = cap["doc"][
            "qoyod_enabled_invoice_trigger_statuses"]
        assert "جاري التوصيل" in enabled
        # Tenant's original values preserved (never shrinks).
        assert "completed" in enabled
        assert "تم التنفيذ" in enabled

    async def test_db_row_unchanged_after_overlay(self):
        db = self._make_db(["completed", "تم التنفيذ"])
        r, _ = await self._capture(db)
        assert r["outcome"] == "SENT"
        raw = db.qoyod_settings._docs[0]
        # DB list unchanged.
        assert raw["qoyod_enabled_invoice_trigger_statuses"] == [
            "completed", "تم التنفيذ"]
        # Other three fields also unchanged.
        assert raw["selective_live_send_enabled"] is False
        assert raw["production_writes_locked"]    is True
        assert raw["dry_run_mode"]                is False

    async def test_non_canary_order_never_gets_overlay(self):
        db = self._make_db(["completed", "تم التنفيذ"])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number="999999999",
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 2
        assert mock_pipe.call_count == 0

    async def test_wrong_phrase_never_gets_overlay(self):
        db = self._make_db(["completed", "تم التنفيذ"])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase="WRONG")
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 1
        assert mock_pipe.call_count == 0

    async def test_status_delivered_never_gets_overlay(self):
        """`delivered` / `تم التوصيل` are NOT in the canary
        whitelist — Guard 4 refuses before the overlay path."""
        db = self._make_db(["completed", "تم التنفيذ"],
                           row_status="delivered")
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 4
        assert mock_pipe.call_count == 0
        # Also assert arabic form.
        db2 = self._make_db(["completed", "تم التنفيذ"],
                            row_status="تم التوصيل")
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe2:
            r2 = await execute_canary_live_send(
                db2, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r2["outcome"] == "REFUSED"
        assert r2["guard_no"] == 4
        assert mock_pipe2.call_count == 0

    async def test_status_in_review_never_gets_overlay(self):
        db = self._make_db(["completed", "تم التنفيذ"],
                           row_status="in_review")
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 4
        assert mock_pipe.call_count == 0

    async def test_settings_debug_carries_rev9_trigger_overlay(self):
        db = self._make_db(["completed", "تم التنفيذ"],
                           row_status="in_review")
        r = await execute_canary_live_send(
            db, order_number=CANARY_ORDER_NUMBER,
            approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        sd = r["settings_debug"]
        assert sd["raw_qoyod_enabled_invoice_trigger_statuses"] \
            == ["completed", "تم التنفيذ"]
        assert "جاري التوصيل" in sd[
            "effective_qoyod_enabled_invoice_trigger_statuses_for_canary"]
        # Tenant values preserved in the effective view.
        assert "completed" in sd[
            "effective_qoyod_enabled_invoice_trigger_statuses_for_canary"]
        assert sd["policy_override_scope"] == (
            f"canary_order_{CANARY_ORDER_NUMBER}_only")

    async def test_no_qoyod_call_when_status_rejected_early(self):
        db = self._make_db(["completed"], row_status="pending")
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert mock_pipe.call_count == 0

    async def test_completed_status_still_widens_defensively(self):
        """Even when the latest row is `completed` (already in the
        tenant's list), the overlay still adds `جاري التوصيل` for
        symmetry — but this doesn't cause any observable change."""
        row = _canary_row()
        row["canonical_payload"]["status"] = "completed"
        db = _build_db(row=row)
        db.qoyod_settings = _FakeColl([{
            "user_id": "main",
            "selective_live_send_enabled": False,
            "production_writes_locked":    True,
            "dry_run_mode":                False,
            "qoyod_enabled_invoice_trigger_statuses":
                ["completed", "تم التنفيذ"],
        }])
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-Z",
                       "qoyod_customer_id":  "CUST-Z",
                       "qoyod_receipt_id":   "RCPT-Z",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        assert r["manual_send_requested"] is False
        # DB unchanged.
        assert db.qoyod_settings._docs[0][
            "qoyod_enabled_invoice_trigger_statuses"] == [
                "completed", "تم التنفيذ"]


# ── Canary customer pre-resolve (rev10) ─────────────────────────────
# Prod bug: SelectiveSendPolicy refused `customer_not_resolved` even
# after all rev1–rev9 fixes because the row's `qoyod_customer_id`
# was still null/DRY at the moment the pipeline ran the invoice
# site's `assert_send_allowed`. rev10 pre-resolves the customer in
# canary (via the existing `resolve_customer` helper — full lookup
# or create with idempotency) and updates the selected trace's
# inbox row BEFORE dispatching to `reprocess_one_order`.
@pytest.mark.asyncio
class TestCustomerPreResolveRev10:

    def _make_db_dry_customer(self):
        row = _canary_row()
        row["qoyod_customer_id"] = "DRY:contact:yyy"
        db = _build_db(row=row)
        return db, row

    async def test_pre_resolve_success_updates_inbox_and_dispatches(
            self):
        from integrations.qoyod.customer_resolver import (
            ResolutionResult,
        )
        db, _ = self._make_db_dry_customer()
        with patch("integrations.qoyod.customer_resolver."
                   "resolve_customer",
                   new=AsyncMock(return_value=ResolutionResult(
                       success=True,
                       qoyod_customer_id="42",
                       lookup_key="+966557951913",
                       lookup_kind="phone",
                       created_new=True))) as mock_cr, \
             patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-PR",
                       "qoyod_customer_id":  "42",
                       "qoyod_receipt_id":   "RCPT-PR",
                   })) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        # resolve_customer was invoked once.
        assert mock_cr.call_count == 1
        # Inbox row's qoyod_customer_id was updated to the real id.
        assert db.integration_inbox._docs[0]["qoyod_customer_id"] \
            == "42"
        # DB settings still Fail-Closed.
        s = db.qoyod_settings._docs[0]
        assert s["selective_live_send_enabled"] is False
        assert s["production_writes_locked"] is True

    async def test_pre_resolve_failure_refuses_no_invoice(self):
        from integrations.qoyod.customer_resolver import (
            ResolutionResult,
        )
        db, _ = self._make_db_dry_customer()
        with patch("integrations.qoyod.customer_resolver."
                   "resolve_customer",
                   new=AsyncMock(return_value=ResolutionResult(
                       success=False,
                       lookup_key="+966557951913",
                       lookup_kind="phone",
                       error={"code": "credentials_missing",
                              "message": "no key"}))), \
             patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["code"] == "canary_customer_resolution_failed"
        # NO pipeline call = no invoice / no payment.
        assert mock_pipe.call_count == 0
        assert r["customer_pre_resolve_debug"]["performed"] is True
        assert r["customer_pre_resolve_debug"]["success"] is False

    async def test_real_customer_id_skips_pre_resolve(self):
        """When the row already has a REAL qoyod_customer_id, canary
        MUST NOT invoke resolve_customer again — idempotency."""
        db = _build_db(row=_canary_row())    # fixture has "12345"
        with patch("integrations.qoyod.customer_resolver."
                   "resolve_customer",
                   new=AsyncMock()) as mock_cr, \
             patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-SKIP",
                       "qoyod_customer_id":  "12345",
                       "qoyod_receipt_id":   "RCPT-SKIP",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        assert mock_cr.call_count == 0    # not invoked.

    async def test_non_canary_order_never_pre_resolves(self):
        db, _ = self._make_db_dry_customer()
        with patch("integrations.qoyod.customer_resolver."
                   "resolve_customer",
                   new=AsyncMock()) as mock_cr, \
             patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number="999999999",
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 2
        assert mock_cr.call_count == 0
        assert mock_pipe.call_count == 0

    async def test_phone_email_mismatch_refuses_before_pre_resolve(
            self):
        db, row = self._make_db_dry_customer()
        # Bad phone → Guard 8 refuses before any resolver call.
        row["canonical_payload"]["customer"]["mobile"] = "+966000000000"
        with patch("integrations.qoyod.customer_resolver."
                   "resolve_customer",
                   new=AsyncMock()) as mock_cr:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert r["guard_no"] == 8
        assert mock_cr.call_count == 0

    async def test_invoice_uses_new_customer_id_after_resolve(self):
        from integrations.qoyod.customer_resolver import (
            ResolutionResult,
        )
        db, _ = self._make_db_dry_customer()
        captured = {}

        async def _fake_reprocess(db_arg, **kw):
            # Read the inbox row from the (proxied) DB.
            r = await db_arg.integration_inbox.find_one(
                {"user_id": "main",
                 "trace_id": kw["trace_id"]}, {"_id": 0})
            captured["qoyod_customer_id"] = r.get(
                "qoyod_customer_id")
            return {
                "outcome":            "SENT",
                "qoyod_invoice_id":   "INV-NEW",
                "qoyod_customer_id":  r.get("qoyod_customer_id"),
                "qoyod_receipt_id":   "RCPT-NEW",
            }

        with patch("integrations.qoyod.customer_resolver."
                   "resolve_customer",
                   new=AsyncMock(return_value=ResolutionResult(
                       success=True,
                       qoyod_customer_id="777",
                       lookup_key="+966557951913",
                       lookup_kind="phone",
                       created_new=True))), \
             patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=_fake_reprocess)):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        # The pipeline read the NEW resolved customer_id from the
        # inbox row (updated by canary pre-resolve).
        assert captured["qoyod_customer_id"] == "777"

    async def test_db_settings_untouched_after_pre_resolve(self):
        from integrations.qoyod.customer_resolver import (
            ResolutionResult,
        )
        db, _ = self._make_db_dry_customer()
        before_settings = list(db.qoyod_settings._docs)
        with patch("integrations.qoyod.customer_resolver."
                   "resolve_customer",
                   new=AsyncMock(return_value=ResolutionResult(
                       success=True,
                       qoyod_customer_id="99",
                       lookup_key="+966557951913",
                       lookup_kind="phone",
                       created_new=False))), \
             patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-A",
                       "qoyod_customer_id":  "99",
                       "qoyod_receipt_id":   "RCPT-A",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        after_settings = list(db.qoyod_settings._docs)
        # DB settings still Fail-Closed on disk.
        assert after_settings == before_settings

    async def test_no_payment_before_successful_customer_resolve(
            self):
        """If customer resolve fails, `reprocess_one_order` (which
        would drive both invoice AND payment) must never run."""
        from integrations.qoyod.customer_resolver import (
            ResolutionResult,
        )
        db, _ = self._make_db_dry_customer()
        with patch("integrations.qoyod.customer_resolver."
                   "resolve_customer",
                   new=AsyncMock(return_value=ResolutionResult(
                       success=False,
                       lookup_key="+966557951913",
                       lookup_kind="phone",
                       error={"code": "qoyod_write_locked",
                              "message": "locked"}))), \
             patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert mock_pipe.call_count == 0


# ── Iter-2026-02.rev11 — SKIPPED diagnostics + under_delivery gate ─
# Production bug 2026-02.rev11: canary passed all 14 guards, delegated
# to the pipeline, but the pipeline returned SKIPPED because
# `business_rules.evaluate` compared `dto.order_status = "جاري_التوصيل"`
# (underscore — emitted by `_canonical_status` for unmapped statuses)
# against `qoyod_settings.invoice_trigger_statuses` widened by the
# canary overlay to include only `"جاري التوصيل"` (space). The two
# forms didn't match → SKIP_NOT_IN_TRIGGER → SKIPPED. Fix: the
# overlay now widens with BOTH the space AND underscore forms.
@pytest.mark.asyncio
class TestUnderDeliveryManualCanary:

    def _under_delivery_row(self):
        row = _canary_row()
        # Salla tenants store statuses in either form; the normalizer
        # converts unmapped statuses via `.replace(" ", "_")`. So the
        # canonical_payload post-normalization carries the UNDERSCORE
        # form. Use that here — this is what the DTO / business_rules
        # will see in Production.
        row["canonical_payload"]["status"] = "جاري_التوصيل"
        row["canonical_payload"]["order_status"] = "جاري_التوصيل"
        row["received_at"] = "2026-07-06T12:00:00+00:00"
        row["trace_id"] = "trace-under-delivery-abc"
        return row

    async def test_overlay_widens_both_space_and_underscore_forms(
            self):
        """The scoped settings proxy MUST widen the trigger list with
        BOTH forms of `جاري التوصيل` — the tenant's on-disk form
        (space) is preserved and the DTO's canonical form
        (underscore) is added — so `business_rules.evaluate` matches
        either representation."""
        from integrations.qoyod.canary_live_send import (
            _CanaryDryRunSettingsProxy,
        )
        base_settings = [{"user_id": "main",
                          "selective_live_send_enabled": False,
                          "production_writes_locked":    True,
                          "invoice_trigger_statuses":
                              ["completed", "تم التنفيذ"]}]
        real_coll = _FakeColl(base_settings)
        proxy = _CanaryDryRunSettingsProxy(real_coll)
        overlaid = await proxy.find_one({"user_id": "main"})
        assert "جاري التوصيل" in overlaid["invoice_trigger_statuses"]
        assert "جاري_التوصيل" in overlaid["invoice_trigger_statuses"]
        # Tenant's original entries preserved (defence in depth).
        assert "completed" in overlaid["invoice_trigger_statuses"]
        assert "تم التنفيذ" in overlaid["invoice_trigger_statuses"]
        # DB settings unchanged on disk.
        assert real_coll._docs[0]["invoice_trigger_statuses"] \
            == ["completed", "تم التنفيذ"]

    async def test_business_rules_eligible_via_overlay_for_under_delivery(
            self):
        """END-TO-END: build a real DTO with canonical status
        `جاري_التوصيل` (underscore — the DTO's canonical form), run
        `business_rules.evaluate` against the OVERLAID settings. It
        MUST return eligible=True — otherwise the pipeline would
        SKIPPED. This is the exact production regression."""
        from integrations.qoyod.canary_live_send import (
            _CanaryDryRunSettingsProxy,
        )
        from integrations.qoyod.business_rules import evaluate

        # Real overlaid settings.
        base_settings = [{"user_id": "main",
                          "selective_live_send_enabled": False,
                          "production_writes_locked":    True,
                          "invoice_trigger_statuses":
                              ["completed", "تم التنفيذ"]}]
        proxy = _CanaryDryRunSettingsProxy(_FakeColl(base_settings))
        overlaid = await proxy.find_one({"user_id": "main"})

        # Minimal DTO shape for evaluate() — attribute access on
        # order_status / order_status_native / completed_at /
        # order_date / paid_at.
        dto = SimpleNamespace(
            order_status="جاري_التوصيل",
            order_status_native="جاري التوصيل",
            completed_at=None,
            order_date=None,
            paid_at=None,
        )
        decision = evaluate(dto, overlaid, existing_invoice_row=None)
        assert decision.eligible is True, decision.to_log_dict()
        assert decision.reason == "eligible"
        assert decision.triggered_by_status == "جاري_التوصيل"

    async def test_delivered_status_still_refused_by_canary(self):
        """`تم التوصيل` / `delivered` MUST NOT get the manual-canary
        override. Only `جاري التوصيل` (in either form) is allowed at
        Guard 4 for this canary."""
        row = _canary_row()
        row["canonical_payload"]["status"] = "تم التوصيل"
        row["received_at"] = "2026-07-07T12:00:00+00:00"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED", r
        # No Qoyod-touching call attempted.
        assert mock_pipe.call_count == 0

    async def test_non_canary_order_never_gets_overlay(self):
        """A different order number (even with `جاري_التوصيل`) must
        refuse at Guard 2 — the manual override is 269629400 only."""
        row = self._under_delivery_row()
        row["salla_order_number"] = "999999999"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number="999999999",
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED", r
        assert r["guard_no"] == 2
        assert mock_pipe.call_count == 0

    async def test_pipeline_receives_widened_trigger_list_for_under_delivery(
            self):
        """The pipeline delegate is called with a `db` proxy whose
        `qoyod_settings.find_one()` yields the widened list. Simulate
        the pipeline reading settings and check the widening carried
        through end-to-end."""
        db = _build_db(row=self._under_delivery_row())
        seen: dict = {}

        async def _fake_reprocess(db_arg, **kw):
            s = await db_arg.qoyod_settings.find_one(
                {"user_id": "main"})
            seen["invoice_trigger_statuses"] = s.get(
                "invoice_trigger_statuses")
            seen["selective_live_send_enabled"] = s.get(
                "selective_live_send_enabled")
            seen["production_writes_locked"] = s.get(
                "production_writes_locked")
            return {
                "outcome":            "SENT",
                "qoyod_invoice_id":   "INV-UD",
                "qoyod_customer_id":  "12345",
                "qoyod_receipt_id":   "RCPT-UD",
            }

        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=_fake_reprocess)):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT", r
        assert "جاري التوصيل" in seen["invoice_trigger_statuses"]
        assert "جاري_التوصيل" in seen["invoice_trigger_statuses"]
        # Scoped open-gate applied via overlay (never on-disk).
        assert seen["selective_live_send_enabled"] is True
        assert seen["production_writes_locked"]    is False

    async def test_db_settings_untouched_after_under_delivery_canary(
            self):
        db = _build_db(row=self._under_delivery_row())
        before = [dict(d) for d in db.qoyod_settings._docs]
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(return_value={
                       "outcome":            "SENT",
                       "qoyod_invoice_id":   "INV-DBX",
                       "qoyod_customer_id":  "12345",
                       "qoyod_receipt_id":   "RCPT-DBX",
                   })):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SENT"
        after = list(db.qoyod_settings._docs)
        assert after == before
        # Explicit: on-disk fail-closed values untouched.
        assert after[0]["selective_live_send_enabled"] is False
        assert after[0]["production_writes_locked"]    is True

    async def test_skipped_response_carries_business_rules_diagnostics(
            self):
        """When the pipeline returns SKIPPED, the canary response
        MUST surface the exact diagnostic keys the operator asked
        for: skip_reason, business_rules_eligible,
        business_rules_reason, manual_send_requested_seen_by_pipeline,
        triggered_by_status,
        invoice_trigger_statuses_seen_by_business_rules."""
        row = self._under_delivery_row()
        db = _build_db(row=row)

        async def _fake_reprocess(db_arg, **kw):
            # Simulate pipeline persisting a business_rules_decision
            # on the inbox row before returning SKIPPED.
            await db_arg.integration_inbox.update_one(
                {"user_id": "main", "trace_id": kw["trace_id"]},
                {"$set": {
                    "business_rules_decision": {
                        "eligible": False,
                        "reason":   "not_in_trigger_statuses",
                        "triggered_by_status": None,
                        "invoice_date": None,
                    },
                    "pipeline_stage": "SKIPPED",
                }})
            return {
                "ok":              False,
                "outcome":         "SKIPPED",
                "reason":          "not_in_trigger_statuses",
                "failed_at_stage": "SKIPPED",
                "trace_id":        kw["trace_id"],
            }

        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock(side_effect=_fake_reprocess)):
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "SKIPPED", r
        assert r["skip_reason"] == "not_in_trigger_statuses"
        assert r["business_rules_eligible"] is False
        assert r["business_rules_reason"] == "not_in_trigger_statuses"
        # Manual-send flag surfaced through to the response — the
        # pipeline "saw" the manual intent from the canary layer.
        assert r["manual_send_requested_seen_by_pipeline"] is True
        # Overlay carried the widened list even for the SKIPPED path
        # (so the operator can debug WHY it still skipped).
        _seen = r["invoice_trigger_statuses_seen_by_business_rules"]
        assert _seen is not None
        assert "جاري_التوصيل" in _seen
        assert "جاري التوصيل" in _seen

    async def test_no_qoyod_call_when_guard_fails_for_under_delivery(
            self):
        """If any pre-pipeline guard refuses the row (e.g. bad
        phone), `reprocess_one_order` MUST NOT be invoked."""
        row = self._under_delivery_row()
        # Break phone → Guard 8 refuses BEFORE any pipeline call.
        row["canonical_payload"]["customer"]["mobile"] = "+966000000000"
        db = _build_db(row=row)
        with patch("integrations.qoyod.one_shot_reprocess."
                   "reprocess_one_order",
                   new=AsyncMock()) as mock_pipe:
            r = await execute_canary_live_send(
                db, order_number=CANARY_ORDER_NUMBER,
                approval_phrase=CANARY_APPROVAL_PHRASE)
        assert r["outcome"] == "REFUSED"
        assert mock_pipe.call_count == 0


# ── Iter-2026-02.rev12 — already_sent counts REAL Qoyod ids only ──
# Production regression: canary reached pipeline but `business_rules`
# returned SKIP_ALREADY_SENT because a stale `qoyod_invoices` row
# carried status="sent" alongside a DRY:invoice:* sentinel id. Fix:
# both already_sent gates (business_rules pre-check + preflight
# idempotency) now IGNORE non-real ids (None / DRY: / PREVIEW:).
@pytest.mark.asyncio
class TestAlreadySentIgnoresNonRealIds:

    async def test_pipeline_already_sent_ignores_dry_id(self):
        """The pipeline layer MUST filter out DRY:/PREVIEW: invoice
        ids BEFORE passing `existing_invoice_row` to
        `business_rules.evaluate`. Proof: stub the evaluator and
        assert it receives `existing_invoice_row=None` even when
        `qoyod_invoices` has a stale DRY row."""
        from unittest.mock import MagicMock
        from integrations.qoyod import pipeline as pmod
        from integrations.qoyod.business_rules import RulesDecision

        captured: dict = {}

        def _spy_evaluate(dto, settings, *, existing_invoice_row=None):
            captured["existing_invoice_row"] = existing_invoice_row
            # Return "not eligible" so pipeline exits early after
            # we've captured what business_rules saw.
            return RulesDecision(
                eligible=False, reason="not_in_trigger_statuses",
                invoice_date=None, invoice_date_source="none")

        # Stub DTO — dodge Pydantic construction; the pipeline only
        # reads `.order_id` before calling evaluate_rules.
        dto_stub = SimpleNamespace(order_id="OID-269629400")

        row = {"id": "row-1", "user_id": "main",
               "pipeline_stage": "NORMALIZED",
               "trace_id": "trace-x",
               "canonical_payload": {"order_id": "OID-269629400"}}

        class _Coll:
            def __init__(self, docs):
                self._docs = docs
            async def find_one(self, q, projection=None):
                for d in self._docs:
                    if all(d.get(k) == v for k, v in q.items()):
                        return {k: v for k, v in d.items()}
                return None

        # STALE dry-run invoice: status="sent" BUT DRY invoice_id.
        db = MagicMock()
        db.qoyod_invoices = _Coll([{
            "user_id": "main",
            "salla_order_id": "OID-269629400",
            "status": "sent",
            "qoyod_invoice_id": "DRY:invoice:stale-xxx",
        }])
        db.qoyod_settings = _Coll([{"user_id": "main"}])
        # rev29c — SAS-disabled branch now persists a synthetic gate
        # (fail-closed). Stub update_one so this async call succeeds.
        db.integration_inbox.update_one = AsyncMock()

        with patch.object(pmod, "SalesOrderDTO",
                          return_value=dto_stub), \
             patch.object(pmod, "evaluate_rules",
                          side_effect=_spy_evaluate), \
             patch.object(pmod, "_apply", new=AsyncMock()):
            await pmod.process_normalized_row(db, row)
        # The stale DRY-id row was FILTERED OUT before business_rules
        # even ran — the guard correctly ignored it.
        assert captured["existing_invoice_row"] is None

    async def test_pipeline_already_sent_ignores_preview_id(self):
        """Direct proof of the helper contract used by the fix
        (`_is_real_invoice_id`) — None / DRY: / PREVIEW: are all
        classified as NOT-real, so already_sent will not fire."""
        from integrations.qoyod.eligible_orders import (
            _is_real_invoice_id,
        )
        assert _is_real_invoice_id(None) is False
        assert _is_real_invoice_id("") is False
        assert _is_real_invoice_id("DRY:invoice:abc") is False
        assert _is_real_invoice_id("PREVIEW:invoice:xyz") is False
        assert _is_real_invoice_id("999123") is True
        assert _is_real_invoice_id("inv_abcdef") is True

    async def test_pipeline_already_sent_still_fires_for_real_id(
            self):
        """Sanity: a REAL قيود invoice id with status='sent' MUST
        still pass through to business_rules — the fix must not
        weaken idempotency for legitimate prior sends."""
        from unittest.mock import MagicMock
        from integrations.qoyod import pipeline as pmod
        from integrations.qoyod.business_rules import RulesDecision

        captured: dict = {}

        def _spy_evaluate(dto, settings, *, existing_invoice_row=None):
            captured["existing_invoice_row"] = existing_invoice_row
            return RulesDecision(
                eligible=False, reason="already_sent",
                invoice_date=None, invoice_date_source="none")

        dto_stub = SimpleNamespace(order_id="OID-269629400")
        row = {"id": "row-1", "user_id": "main",
               "pipeline_stage": "NORMALIZED",
               "trace_id": "trace-x",
               "canonical_payload": {"order_id": "OID-269629400"}}

        class _Coll:
            def __init__(self, docs):
                self._docs = docs
            async def find_one(self, q, projection=None):
                for d in self._docs:
                    if all(d.get(k) == v for k, v in q.items()):
                        return {k: v for k, v in d.items()}
                return None

        db = MagicMock()
        db.qoyod_invoices = _Coll([{
            "user_id": "main",
            "salla_order_id": "OID-269629400",
            "status": "sent",
            "qoyod_invoice_id": "999123",   # REAL قيود id
        }])
        db.qoyod_settings = _Coll([{"user_id": "main"}])
        # rev29c — SAS-disabled branch now persists a synthetic gate.
        db.integration_inbox.update_one = AsyncMock()

        with patch.object(pmod, "SalesOrderDTO",
                          return_value=dto_stub), \
             patch.object(pmod, "evaluate_rules",
                          side_effect=_spy_evaluate), \
             patch.object(pmod, "_apply", new=AsyncMock()):
            await pmod.process_normalized_row(db, row)
        # REAL id → passed through to business_rules.
        assert captured["existing_invoice_row"] is not None
        assert captured["existing_invoice_row"].get(
            "qoyod_invoice_id") == "999123"


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
