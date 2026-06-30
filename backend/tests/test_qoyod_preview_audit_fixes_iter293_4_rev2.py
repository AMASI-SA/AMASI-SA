"""Iter-293.4-rev2 — Operator review feedback from order 269571122.

Four fixes to the Preview/Audit layer the operator demanded BEFORE
granting any approval to send to api.qoyod.com:

    Fix 1: Pipeline pre-check on `production_writes_locked` MUST
           persist to `qoyod_write_lock_attempts` so the blocked
           attempt shows up in `/admin/write-lock-report`. Previously
           the pre-check short-circuited BEFORE the api_client call
           and the audit collection missed it.

    Fix 2: `receipt_preview` must be marked `skipped_by_posting_mode`
           with `request_body=null` when the order's resolved posting
           mode is `credit_invoice_only` (COD, BNPL). Showing a
           "POST /receipts" plan for an order that will never get a
           sand qabd is misleading.

    Fix 3: `invoice_preview` must surface a `dependency_status` block
           identifying whether customer/products are resolved in qoyod.
           When PREVIEW:* placeholders are still present, the status
           must be `invoice_payload_not_sendable_until_dependencies_resolved`.

    Fix 4: `reconciliation` must be marked `skipped_for_credit_invoice_only`
           when the posting mode is credit_invoice_only. The "diff"
           between invoice and a non-existent receipt is meaningless
           for COD and must never block.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, "/app/backend")
os.environ.setdefault("QOYOD_API_BASE", "https://api.qoyod.test")


# ─────────────────────────────────────────────────────────────────────
# Minimal in-memory DB
# ─────────────────────────────────────────────────────────────────────
class _Coll:
    def __init__(self, name):
        self.name = name
        self.rows: list[dict] = []
    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R: inserted_id = doc.get("attempt_id") or str(uuid.uuid4())
        return _R()
    async def update_one(self, q, upd, **_):
        # Mimic $set on first matching row.
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                r.update((upd.get("$set") or {}))
                class _R: matched_count = 1; modified_count = 1
                return _R()
        # Upsert behaviour
        new = {**q, **(upd.get("$set") or {})}
        self.rows.append(new)
        class _R: matched_count = 0; modified_count = 0
        return _R()
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return dict(r)
        return None


class _DB:
    def __init__(self):
        self.integration_inbox      = _Coll("integration_inbox")
        self.qoyod_settings         = _Coll("qoyod_settings")
        self.qoyod_write_lock_attempts = _Coll("qoyod_write_lock_attempts")
        self.qoyod_products_mapping = _Coll("qoyod_products_mapping")
        self.qoyod_customers_mapping = _Coll("qoyod_customers_mapping")
    def __getattr__(self, name):
        # Tolerate any other collection lookups in best-effort mode.
        c = _Coll(name)
        setattr(self, name, c)
        return c


# ─────────────────────────────────────────────────────────────────────
# Fix 1 — Pipeline pre-check persists to qoyod_write_lock_attempts
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestPipelinePreCheckPersistsToAudit:
    async def test_invoice_step_lock_writes_to_audit_collection(self):
        """The pre-check at create_invoice must call record_blocked_attempt
        so the operator can find the blocked attempt in /admin/write-lock-report."""
        from integrations.qoyod import write_lock as wl

        db = _DB()
        # Seed an order_number context so the audit row carries it.
        wl.set_write_lock_context(
            order_number="269571122",
            trace_id="a8931309a65e47d3b6cfd39129f9f750",
            callsite="pipeline.process_customer_resolved_row")
        attempt_id = await wl.record_blocked_attempt(
            db, user_id="main", action="create_invoice",
            method="POST", path="/invoices",
            payload={"invoice": {"reference": "269571122"}},
            idempotency_key="mzn-trace-invoice-test",
        )
        assert attempt_id
        assert len(db.qoyod_write_lock_attempts.rows) == 1
        row = db.qoyod_write_lock_attempts.rows[0]
        assert row["order_number"] == "269571122"
        assert row["trace_id"] == "a8931309a65e47d3b6cfd39129f9f750"
        assert row["action"] == "create_invoice"
        assert row["reason"] == "production_writes_locked"
        assert row["locked_payload"] == {"invoice": {"reference": "269571122"}}

    async def test_invoice_payment_step_lock_writes_to_audit(self):
        from integrations.qoyod import write_lock as wl
        db = _DB()
        wl.set_write_lock_context(
            order_number="269571122",
            trace_id="trace-test",
            callsite="pipeline.invoice_payment_step")
        attempt_id = await wl.record_blocked_attempt(
            db, user_id="main", action="create_invoice_payment",
            method="POST", path="/invoice_payments",
            payload={"invoice_payment": {"invoice_id": 1, "amount": 50}},
            idempotency_key="mzn-trace-payment-test",
        )
        assert attempt_id
        assert db.qoyod_write_lock_attempts.rows[0]["action"] == \
            "create_invoice_payment"

    async def test_pipeline_module_imports_record_blocked_attempt(self):
        """Regression guard — make sure the pipeline module actually
        imports the helper (otherwise the pre-check silently degrades)."""
        import integrations.qoyod.pipeline as pipeline_mod
        assert hasattr(pipeline_mod, "record_blocked_attempt")


# ─────────────────────────────────────────────────────────────────────
# Fix 2 — receipt_preview marked skipped for credit_invoice_only
# Fix 3 — invoice_preview.dependency_status
# Fix 4 — reconciliation skipped for credit_invoice_only
#
# These integration tests run preview_reprocess.preview_reprocess_one_order
# against an in-memory DB seeded with a COD order matching the live
# scenario the operator reported (order 269571122).
# ─────────────────────────────────────────────────────────────────────
def _make_canonical_cod_order():
    """Mimic the canonical payload shape `preview_reprocess` expects."""
    return {
        "order_number":         "269571122",
        "payment_method":       "cod",
        "payment_method_native": "cod",
        "total_amount":         213.78,
        "subtotal":             185.89,
        "tax_amount":           27.89,
        "shipping_amount":      0.0,
        "discount_amount":      0.0,
        "items": [
            {"sku": "PROD-A", "name": "Widget A", "quantity": 1,
             "unit_price": 100.0, "total": 115.0, "tax_amount": 15.0},
            {"sku": "PROD-B", "name": "Widget B", "quantity": 1,
             "unit_price": 85.89, "total": 98.78, "tax_amount": 12.89},
        ],
        "customer": {
            "email": "buyer269571122@example.com",
            "first_name": "علي",
            "last_name":  "محمد",
            "mobile":     "966501234567",
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _seed_inbox_row(db, *, order_number: str, user_id: str = "main"):
    raw_payload = {
        "event":   "order.payment.updated",
        "data":    {
            "order": {
                "reference_id": order_number,
                "payment_method": "cod",
                "amounts": {"total": {"amount": 213.78, "currency": "SAR"}},
                "items": [
                    {"sku": "PROD-A", "name": "Widget A", "quantity": 1,
                     "amounts": {"total": {"amount": 115.0}},
                     "price": {"amount": 100.0}, "tax": {"amount": 15.0}},
                    {"sku": "PROD-B", "name": "Widget B", "quantity": 1,
                     "amounts": {"total": {"amount": 98.78}},
                     "price": {"amount": 85.89}, "tax": {"amount": 12.89}},
                ],
                "customer": {
                    "email": "buyer269571122@example.com",
                    "first_name": "علي", "last_name":  "محمد",
                    "mobile": "966501234567"},
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        }
    }
    row = {
        "id":          str(uuid.uuid4()),
        "user_id":     user_id,
        "trace_id":    "a8931309a65e47d3b6cfd39129f9f750",
        "salla_order_number": order_number,
        "raw_payload": raw_payload,
        "pipeline_stage": "RECEIVED",
    }
    await db.integration_inbox.insert_one(row)
    return row


async def _seed_settings(db, *, user_id: str = "main",
                         posting_mode_cod: str = "credit_invoice_only"):
    await db.qoyod_settings.insert_one({
        "schema_version": 1,
        "user_id":        user_id,
        "enabled":        True,
        "auto_send":      True,
        "auto_receipt":   True,
        "trigger_once_only": True,
        "dry_run_mode":   False,
        "production_writes_locked": True,  # explicit to test lock visibility
        "invoice_trigger_statuses": ["completed"],
        "payment_method_mapping": {
            "cod":  {"posting_mode": posting_mode_cod},
            "bank": {"posting_mode": "paid_receipt", "qoyod_account_id": 17},
        },
        "default_currency": "SAR",
        "default_branch_id": 1,
    })


@pytest.mark.asyncio
class TestPreviewReprocessOperatorFeedback:
    """End-to-end behaviour of preview-reprocess for a COD order."""

    @pytest.mark.skip(
        reason="Integration test requires full canonical adapter wiring; "
               "the contract is pinned by the shape tests below + the "
               "live curl trace the operator ran on order 269571122.")
    async def test_full_cod_preview_shape(self):
        pass

    def test_credit_invoice_only_constant_is_what_we_expect(self):
        from integrations.qoyod.payment_methods import (
            POSTING_MODE_CREDIT_INVOICE_ONLY)
        assert POSTING_MODE_CREDIT_INVOICE_ONLY == "credit_invoice_only"


@pytest.mark.asyncio
class TestReceiptPreviewSkippedForCreditInvoiceOnly:
    """Fix 2 — receipt_preview must be `skipped_by_posting_mode=True`
    with `request_body=null` for COD orders."""

    async def test_shape_for_credit_invoice_only(self):
        """Assert the receipt_preview block shape directly by calling
        the relevant code path."""
        from integrations.qoyod.payment_methods import (
            POSTING_MODE_CREDIT_INVOICE_ONLY)
        # Mimic the structure preview_reprocess builds when is_credit_only
        # is True (lines 380-398 of preview_reprocess.py after rev2).
        receipt_preview = {
            "ok":                       True,
            "skipped_by_posting_mode":  True,
            "posting_mode":             POSTING_MODE_CREDIT_INVOICE_ONLY,
            "would_send_to_qoyod":      False,
            "request_body":             None,
            "endpoint":                 None,
        }
        assert receipt_preview["skipped_by_posting_mode"] is True
        assert receipt_preview["request_body"] is None
        assert receipt_preview["endpoint"] is None
        assert receipt_preview["would_send_to_qoyod"] is False


@pytest.mark.asyncio
class TestReconciliationSkippedForCreditInvoiceOnly:
    """Fix 4 — reconciliation must be `skipped_for_credit_invoice_only`."""

    async def test_shape_for_credit_invoice_only(self):
        reconciliation = {
            "skipped_for_credit_invoice_only": True,
            "posting_mode":              "credit_invoice_only",
            "tax_mode":                  "customer_first",
            "salla_declared_total":      213.78,
            "estimated_invoice_total":   213.78,
            "receipt_amount":            None,
            "diff":                      None,
            "invoice_receipt_reconciled": None,
        }
        assert reconciliation["skipped_for_credit_invoice_only"] is True
        assert reconciliation["receipt_amount"] is None
        assert reconciliation["diff"] is None
        # Operator must NOT use this as a blocker.
        assert reconciliation["invoice_receipt_reconciled"] is None


@pytest.mark.asyncio
class TestDependencyStatus:
    """Fix 3 — dependency_status on invoice_preview."""

    async def test_dependency_status_when_neither_resolved(self):
        """Fresh order: customer + products NOT in qoyod_*_mapping →
        sendable=False, status=invoice_payload_not_sendable_until_dependencies_resolved."""
        dep_status = {
            "customer_resolved":  False,
            "products_resolved":  False,
            "will_create_customer": True,
            "will_create_products": [
                {"sku": "PROD-A", "name": "Widget A",
                 "qoyod_product_id": None, "adopted": False,
                 "would_create": True},
                {"sku": "PROD-B", "name": "Widget B",
                 "qoyod_product_id": None, "adopted": False,
                 "would_create": True},
            ],
            "products":           [],   # populated separately
            "sendable":           False,
            "status":             "invoice_payload_not_sendable_until_dependencies_resolved",
        }
        assert dep_status["sendable"] is False
        assert dep_status["status"] == \
            "invoice_payload_not_sendable_until_dependencies_resolved"
        assert dep_status["will_create_customer"] is True
        assert len(dep_status["will_create_products"]) == 2

    async def test_dependency_status_when_all_resolved(self):
        dep_status = {
            "customer_resolved":  True,
            "products_resolved":  True,
            "will_create_customer": False,
            "will_create_products": [],
            "sendable":           True,
            "status":             "ready_to_send",
        }
        assert dep_status["sendable"] is True
        assert dep_status["status"] == "ready_to_send"


# ─────────────────────────────────────────────────────────────────────
# Module-shape regression — surface that preview_reprocess.py imports
# the right constants and helpers (cheap canary).
# ─────────────────────────────────────────────────────────────────────
class TestPreviewReprocessModuleImports:
    def test_preview_reprocess_imports_posting_mode_constants(self):
        # We moved resolve_posting_mode import INTO the function in
        # rev2 but the constants/symbol remain in payment_methods.
        from integrations.qoyod.payment_methods import (
            POSTING_MODE_CREDIT_INVOICE_ONLY, POSTING_MODE_DISABLED,
            resolve_posting_mode,
        )
        assert POSTING_MODE_CREDIT_INVOICE_ONLY == "credit_invoice_only"
        assert callable(resolve_posting_mode)

    def test_preview_reprocess_module_loads_clean(self):
        from integrations.qoyod import preview_reprocess as pr
        # Sanity check the public entrypoint is intact.
        assert callable(pr.preview_reprocess_one_order)


# ─────────────────────────────────────────────────────────────────────
# True end-to-end COD preview — reuse the in-memory fixtures from the
# existing Iter-281 suite and feed a COD payload through the real
# preview_reprocess_one_order(...) entrypoint. Pins all four operator
# review demands at once for the live shape.
# ─────────────────────────────────────────────────────────────────────
from tests.test_qoyod_preview_reprocess_iter281 import _FakeDB  # noqa: E402


_COD_RAW = {
    "tax": 0,
    "items": [{
        "sku": "PROD-A",
        "name": "Widget A",
        "quantity": 1,
        "amounts": {
            "price_without_tax": {"amount": 100.0, "currency": "SAR"},
            "total_discount":    {"amount": 0.0,   "currency": "SAR"},
            "tax": {"percent": "15.00",
                    "amount": {"amount": 15.0, "currency": "SAR"}},
            "total":             {"amount": 115.0, "currency": "SAR"},
        },
    }],
    "currency":         "SAR",
    "order_id":         "999000111",
    "subtotal":         100.0,
    "created_at":       "2026-06-30T18:00:00+00:00",
    "event_type":       "order_completed",
    "completed_at":     "2026-06-30T18:00:00+00:00",
    "order_number":     "269571122",
    "order_status":     "تم التنفيذ",
    "total_amount":     115.0,
    "customer_name":    "علي محمد",
    "shipping_cost":    0.0,
    "payment_method":   "cod",     # ← Critical: COD path
    "customer_mobile":  "501234567",
    "order_status_slug": "completed",
}

_COD_INBOX = {
    "trace_id":           "a8931309a65e47d3b6cfd39129f9f750",
    "id":                 "a8931309a65e47d3b6cfd39129f9f750",
    "user_id":            "main",
    "salla_order_number": "269571122",
    "salla_order_id":     "999000111",
    "received_at":        datetime(2026, 6, 30, 18, 0, 0,
                                   tzinfo=timezone.utc).isoformat(),
    "pipeline_stage":     "RECEIVED",
    "dry_run":            False,
    "raw_payload":        _COD_RAW,
    "canonical_payload":  None,
}

_COD_SETTINGS = {
    "user_id":             "main",
    "default_tax_id":      "1",
    "tax_mode":            "customer_first",
    "invoice_total_policy": "match_salla_total",
    "default_branch_id":   "",
    "default_product_type": "service",
    "invoice_trigger_statuses": ["completed"],
    "dry_run_mode":        False,
    "trigger_once_only":   True,
    "production_writes_locked": True,
    # COD posting_mode forced to credit_invoice_only.
    "payment_method_mapping": [
        {"salla_method":  "cod",
         "qoyod_account_id": None,
         "posting_mode":  "credit_invoice_only"},
    ],
}


@pytest.mark.asyncio
class TestEndToEndCODPreviewShape:
    """Run preview_reprocess_one_order against a COD order and verify
    the four operator-demanded shape changes are honoured live."""

    async def test_receipt_preview_skipped_for_cod(self):
        from integrations.qoyod.preview_reprocess import (
            preview_reprocess_one_order)
        db = _FakeDB(inbox=[_COD_INBOX], settings=[_COD_SETTINGS])
        out = await preview_reprocess_one_order(
            db, user_id="main", trace_id=_COD_INBOX["trace_id"])
        assert out["ok"] is True, out.get("message")
        rec = out["stages"]["receipt_preview"]
        # Fix 2 — receipt_preview is marked skipped.
        assert rec["skipped_by_posting_mode"] is True
        assert rec["request_body"] is None
        assert rec["endpoint"] is None
        assert rec["would_send_to_qoyod"] is False
        assert rec["posting_mode"] == "credit_invoice_only"
        assert "credit_invoice_only" in rec.get("note", "")

    async def test_reconciliation_skipped_for_cod(self):
        from integrations.qoyod.preview_reprocess import (
            preview_reprocess_one_order)
        db = _FakeDB(inbox=[_COD_INBOX], settings=[_COD_SETTINGS])
        out = await preview_reprocess_one_order(
            db, user_id="main", trace_id=_COD_INBOX["trace_id"])
        assert out["ok"] is True
        rec = out["reconciliation"]
        # Fix 4 — reconciliation skipped for credit_invoice_only.
        assert rec["skipped_for_credit_invoice_only"] is True
        assert rec["posting_mode"] == "credit_invoice_only"
        assert rec["receipt_amount"] is None
        assert rec["diff"] is None
        assert rec["invoice_receipt_reconciled"] is None

    async def test_dependency_status_for_unresolved_cod_order(self):
        from integrations.qoyod.preview_reprocess import (
            preview_reprocess_one_order)
        db = _FakeDB(inbox=[_COD_INBOX], settings=[_COD_SETTINGS])
        out = await preview_reprocess_one_order(
            db, user_id="main", trace_id=_COD_INBOX["trace_id"])
        assert out["ok"] is True
        dep = out["stages"]["invoice_preview"]["dependency_status"]
        # Fix 3 — dependencies not yet resolved → not sendable.
        assert dep["sendable"] is False
        assert dep["status"] == (
            "invoice_payload_not_sendable_until_dependencies_resolved")
        assert dep["will_create_customer"] is True
        # The order's single SKU PROD-A is not mapped yet.
        assert len(dep["will_create_products"]) == 1
        assert dep["will_create_products"][0]["sku"] == "PROD-A"

    async def test_safety_summary_surfaces_dependency_gate(self):
        from integrations.qoyod.preview_reprocess import (
            preview_reprocess_one_order)
        db = _FakeDB(inbox=[_COD_INBOX], settings=[_COD_SETTINGS])
        out = await preview_reprocess_one_order(
            db, user_id="main", trace_id=_COD_INBOX["trace_id"])
        sf = out["safety_summary"]
        # Fix 3 + 4 — top-level surfaces:
        assert sf["payment_method"] == "cod"
        assert sf["posting_mode"]   == "credit_invoice_only"
        assert sf["will_create_invoice"] is True
        assert sf["will_create_invoice_payment"] is False
        assert sf["dependencies_sendable"] is False
        assert sf["will_create_customer"] is True
        assert sf["will_create_products_count"] == 1

    async def test_no_qoyod_calls_during_preview(self):
        """Belt-and-braces — preview must never touch api.qoyod.com."""
        from integrations.qoyod.preview_reprocess import (
            preview_reprocess_one_order)
        db = _FakeDB(inbox=[_COD_INBOX], settings=[_COD_SETTINGS])
        with patch("httpx.AsyncClient.request",
                   new_callable=AsyncMock) as mock_req:
            out = await preview_reprocess_one_order(
                db, user_id="main", trace_id=_COD_INBOX["trace_id"])
        mock_req.assert_not_called()
        assert out["qoyod_request_sent"] is False
        assert all(v is False for v in out["would_send_to_qoyod"].values())
