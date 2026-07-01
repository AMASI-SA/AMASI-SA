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

    async def test_guard10_totals_invalid(self):
        # Break the totals so Mezan-VAT-guard rejects.
        with patch("integrations.qoyod.canary_live_send"
                   "._run_guards.__globals__") as _:
            pass   # sentinel — real patch below via monkeypatch fn.

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
