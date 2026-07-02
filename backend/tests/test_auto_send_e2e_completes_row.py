"""Iter-2026-02.rev22 — End-to-end Auto-Send completion test.

Invariant under test (user directive 2026-02-27, post-order-270075325)
─────────────────────────────────────────────────────────────────────
A single SAS-eligible tabby_installment row at CUSTOMER_RESOLVED must
transition to COMPLETED inside ONE call to
`process_customer_resolved_row` — WITHOUT:

  ▸ opening `production_writes_locked` in the DB
  ▸ opening `dry_run_mode`             in the DB
  ▸ opening `selective_live_send_enabled` in the DB
  ▸ any manual `approve-locked-payment` step
  ▸ landing at `LOCKED_AWAITING_APPROVAL`
  ▸ landing at `PARTIAL_FAILURE`
  ▸ any bank_transfer / COD path

The two Qoyod writes (`POST /invoices` + `POST /invoice_payments`)
must both fire on the SAME scoped api_client (write_lock_enabled=False)
that the SAS gate produces per-row. DB flags stay `production_writes_locked=True`,
`dry_run_mode=True`, `selective_live_send_enabled=False` on disk before
AND after the pipeline call.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")

CUTOVER_ISO   = "2026-07-01T00:00:00+00:00"
AFTER_CUTOVER = "2026-07-05T10:00:00+00:00"
ORDER_NO      = "999001"
ORDER_ID      = f"MZN-{ORDER_NO}"
TRACE_ID      = "tr-auto-e2e-01"


class _Coll:
    def __init__(self, docs=None):
        self._docs = list(docs or [])
    async def find_one(self, q, projection=None):
        for d in self._docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None
    async def update_one(self, q, u, upsert=False):
        for d in self._docs:
            if all(d.get(k) == v for k, v in q.items()):
                for k, v in (u.get("$set") or {}).items():
                    d[k] = v
                for k in (u.get("$unset") or {}):
                    d.pop(k, None)
                for k, v in (u.get("$push") or {}).items():
                    arr = d.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        arr.extend(v["$each"])
                    else:
                        arr.append(v)
                return MagicMock(matched_count=1, modified_count=1)
        if upsert:
            new = {**q, **(u.get("$set") or {}),
                   **(u.get("$setOnInsert") or {})}
            self._docs.append(new)
            return MagicMock(matched_count=0, modified_count=0,
                             upserted_id="new")
        return MagicMock(matched_count=0, modified_count=0)
    async def insert_one(self, d):
        self._docs.append(d)
        return MagicMock(inserted_id="new")
    def find(self, q=None, sort=None, limit=None, projection=None):
        docs = list(self._docs)
        class _Cur:
            def __init__(self, ds): self.ds = ds
            def __aiter__(self):
                async def _gen():
                    for d in self.ds:
                        yield d
                return _gen()
        return _Cur(docs)


def _make_settings() -> dict:
    return {
        "user_id":                                 "main",
        # ── HARD LOCKS ON DISK (must stay this way) ──
        "production_writes_locked":                True,
        "dry_run_mode":                            True,
        "selective_live_send_enabled":             False,
        # ── SAS ON, tabby_installment allow-listed ──
        "selective_auto_send_enabled":             True,
        "selective_auto_send_cutover_at":          CUTOVER_ISO,
        "selective_auto_send_allowed_payment_methods":
            ["tabby_installment"],
        # ── Payment-method mapping (tabby → قيود account 92) ──
        "payment_method_mapping": [
            {"salla_method":      "tabby",
             "qoyod_account_id":  "92",
             "qoyod_payment_method_id": "92"},
            {"salla_method":      "tabby_installment",
             "qoyod_account_id":  "92",
             "qoyod_payment_method_id": "92"},
        ],
        # Defaults so preflight / builder don't refuse.
        "default_customer_id":  "230",
        "invoice_trigger_statuses": ["completed"],
        "invoice_date_source":  "send_date",
        "auto_receipt":         True,
        "capabilities":         {"create_receipts": True},
        "trigger_once_only":    True,
    }


def _make_canonical() -> dict:
    return {
        "order_id":             ORDER_ID,
        "order_number":         ORDER_NO,
        "order_status":         "completed",
        "order_status_native":  "completed",
        "payment_method":       "tabby_installment",
        "payment_method_native": "tabby_installment",
        "currency":             "SAR",
        "total_amount":         260.98,
        "salla_order_created_at": AFTER_CUTOVER,
        "items": [
            {"sku": "SKU-1", "name": "Test", "qty": 1,
             "unit_price": 260.98, "line_total": 260.98,
             "vat_rate": 15},
        ],
        "customer": {
            "name": "Test Customer", "phone": "+966500000000",
        },
    }


def _make_row() -> dict:
    return {
        "id":                          "row-auto-e2e-01",
        "user_id":                     "main",
        "salla_order_number":          ORDER_NO,
        "trace_id":                    TRACE_ID,
        "pipeline_stage":              "CUSTOMER_RESOLVED",
        "qoyod_customer_id":           "230",
        "canonical_payload":           _make_canonical(),
        "business_rules_decision":     {
            "eligible":              True,
            "invoice_date":          AFTER_CUTOVER,
            "invoice_date_source":   "salla",
            "triggered_by_status":   "completed",
        },
        "pipeline_started_at":         "2026-07-05T10:00:00+00:00",
        "stage_history":               [],
    }


class _StubQoyodClient:
    """A real-shape scoped client. `write_lock_enabled=False` because
    the pipeline builds it with `scoped_write_allowance=True`."""
    write_lock_enabled = False

    def __init__(self):
        self.invoice_posts:         list[dict] = []
        self.invoice_payment_posts: list[dict] = []

    async def create_invoice(self, payload, *, idem):
        self.invoice_posts.append(
            {"payload": payload, "idem": idem})
        return {"invoice": {"id": "187", "reference": ORDER_NO,
                             "number": "INV-187",
                             "total": "260.98"}}

    async def create_invoice_payment(self, payload, *, idem):
        self.invoice_payment_posts.append(
            {"payload": payload, "idem": idem})
        return {"invoice_payment": {"id": "159",
                                      "amount": "260.98"}}

    # Should NEVER be called from process_customer_resolved_row.
    async def create_customer(self, *a, **kw):
        raise AssertionError("must not create customer at this stage")

    async def get_invoice(self, invoice_id):
        # Post-create totals verification reads this. Return matching
        # totals so the guard passes cleanly.
        return {"invoice": {
            "id": str(invoice_id), "reference": ORDER_NO,
            "total": "260.98",
            "sub_total": "226.94",
            "tax": "34.04",
        }}


@pytest.mark.asyncio
async def test_auto_send_completes_invoice_and_payment_in_one_call():
    """Fresh SAS-eligible row @ CUSTOMER_RESOLVED → COMPLETED with
    BOTH invoice + payment POSTed in the same scoped client."""
    from integrations.qoyod import pipeline as pmod

    settings = _make_settings()
    row      = _make_row()

    db = MagicMock()
    db.qoyod_settings         = _Coll([dict(settings)])
    db.integration_inbox      = _Coll([dict(row)])
    db.qoyod_invoices         = _Coll([])
    db.qoyod_invoice_payments = _Coll([])
    db.qoyod_write_lock_attempts = _Coll([])
    db.qoyod_customers        = _Coll([])
    db.qoyod_products         = _Coll([])

    scoped_client = _StubQoyodClient()

    # Stub product resolver → success with one qoyod-mapped item.
    from integrations.qoyod.product_resolver import (
        ProductsResolutionResult, ProductResolutionItem,
    )
    prod_res_ok = ProductsResolutionResult(
        success=True,
        items=[ProductResolutionItem(
            sku="SKU-1",
            qoyod_product_id="P-1",
            created_new=False,
            trust_source="mezan",
        )],
        error=None,
    )

    # Stub preflight → passed.
    from integrations.qoyod.preflight import PreflightResult
    preflight_ok = PreflightResult(passed=True, failures=[])

    # Stub invoice_builder outputs. build_invoice_payload returns
    # {"invoice": {...}, "_diagnostics": {...}}.
    invoice_payload = {
        "invoice": {
            "reference":     ORDER_NO,
            "contact_id":    "230",
            "issue_date":    "2026-07-05",
            "line_items": [
                {"product_id":  "P-1",
                 "quantity":    1,
                 "unit_price":  260.98,
                 "tax_percent": 15,
                 "description": "Test"},
            ],
        },
        "_diagnostics": {
            "pricing_mode":         "match_salla_total",
            "salla_total":          260.98,
            "expected_qoyod_total": 260.98,
            "difference":           0.0,
        },
    }
    payment_payload = {
        "invoice_payment": {
            "invoice_id":     "187",
            "amount":         260.98,
            "date":           "2026-07-05",
            "account_id":     "92",
            "reference":      ORDER_NO,
            "description":    f"Mezan · Salla order {ORDER_NO}",
            "payment_method": "tabby_installment",
        }
    }
    idem_fingerprint = {
        "order_id":          ORDER_ID,
        "qoyod_invoice_id":  "187",
        "payment_method":    "tabby_installment",
        "payment_method_id": "92",
        "amount":            260.98,
    }

    # Stub totals_guard OK.
    totals_ok = MagicMock(
        ok=True, code="ok", message="ok", details={},
        to_log_dict=lambda: {"ok": True})

    # Stub selective_send_guard → allow with a decision.
    decision_stub = MagicMock(
        allowed=True,
        blocker_code=None,
        send_timestamp=datetime(
            2026, 7, 5, 10, 0, 0, tzinfo=timezone.utc),
        send_date_riyadh="2026-07-05",
    )

    with patch.object(pmod, "resolve_products",
                      new=AsyncMock(return_value=prod_res_ok)), \
         patch.object(pmod, "preflight_run",
                      return_value=preflight_ok), \
         patch.object(pmod, "build_invoice_payload",
                      return_value=invoice_payload), \
         patch.object(pmod, "build_invoice_payment_payload",
                      return_value=(payment_payload, idem_fingerprint)), \
         patch.object(pmod, "validate_totals",
                      return_value=totals_ok), \
         patch.object(pmod, "assert_send_allowed",
                      return_value=decision_stub), \
         patch.object(pmod, "apply_send_date_to_qoyod_payload",
                      side_effect=lambda p, d: p), \
         patch.object(pmod, "_get_api_client",
                      new=AsyncMock(return_value=(scoped_client, False))):

        out = await pmod.process_customer_resolved_row(
            db, dict(row))

    # ── PRIMARY INVARIANT: outcome is COMPLETED ──
    assert out.get("outcome") == "COMPLETED", (
        f"pipeline did NOT complete auto — outcome={out.get('outcome')}, "
        f"reason={out.get('reason')}, step={out.get('step')}. "
        f"Full result: {out}")

    # BOTH POSTs fired — invoice AND payment — on the SAME client.
    assert len(scoped_client.invoice_posts) == 1, (
        f"expected exactly 1 POST /invoices, got "
        f"{len(scoped_client.invoice_posts)}")
    assert len(scoped_client.invoice_payment_posts) == 1, (
        f"expected exactly 1 POST /invoice_payments, got "
        f"{len(scoped_client.invoice_payment_posts)}. This is the "
        f"regression that manifested as order 270075325 needing "
        f"manual approve-locked-payment.")

    # Row stage is COMPLETED, no locked payload traces.
    updated_row = await db.integration_inbox.find_one({"id": row["id"]})
    assert updated_row["pipeline_stage"] == "COMPLETED"
    assert "invoice_locked_payload" not in (
        updated_row.get("qoyod_payloads") or {})
    assert "invoice_payment_locked_payload" not in (
        updated_row.get("qoyod_payloads") or {})
    assert updated_row.get("lock_reason") is None
    assert updated_row.get("qoyod_invoice_id")         == "187"
    assert updated_row.get("qoyod_invoice_payment_id") == "159"

    # DB flags UNCHANGED on disk.
    disk_settings = await db.qoyod_settings.find_one({"user_id": "main"})
    assert disk_settings["production_writes_locked"]    is True
    assert disk_settings["dry_run_mode"]                is True
    assert disk_settings["selective_live_send_enabled"] is False

    # No blocked-write attempt was recorded.
    blocked = await db.qoyod_write_lock_attempts.find_one(
        {"order_number": ORDER_NO})
    assert blocked is None, (
        "SAS auto-send must not enter write_lock audit path")
