"""Iter-2026-02.rev30 — Payment continuation + diagnostic surfacing.

Production trace `4dc65ba6eb5646e5afbf915268c70fcc` (order 270166208,
Tabby installment) landed at `pipeline_stage=INVOICE_CREATED` with a
DRY invoice id AND no `qoyod_invoice_payment_id`. The row sat there
silently — no downstream transition, no clear reason.

Root cause: two short-circuit branches in
`process_customer_resolved_row` (posting_mode=disabled and
`auto_receipt=false / create_receipts=false`) returned "INVOICE_CREATED"
outcome WITHOUT transitioning the inbox row's `pipeline_stage`. The
row then had no worker to pick it up (`process_pending_customer_resolved`
only queries CUSTOMER_RESOLVED) — silent-stuck.

rev30 fixes:

  A. Both short-circuit sites now transition the inbox row via atomic
     CAS from INVOICE_CREATED → `COMPLETED_INVOICE_ONLY` (a new
     definitive terminal stage). Persist `payment_stage_blocker_code`,
     `payment_stage_blocker_reason`, `payment_stage_expected`, and
     `invoice_payment_required_for_method` on the row.

  B. `row_diagnostics` surfaces:
       - `invoice_payment_required_for_method`
       - `payment_stage_expected`
       - `payment_stage_blocker_code`
       - `payment_stage_blocker_reason`
       - `payment_payload_preview_exists`
     When a row is at `INVOICE_CREATED` with no persisted blocker,
     diagnostics synthesises `silent_stuck_at_invoice_created`.

  C. New build marker `rev30_payment_continuation`.

The regular happy-path (posting_mode=paid_receipt with
auto_receipt=true) is UNCHANGED: it still transitions to
INVOICE_PAYMENT_CREATED and then COMPLETED in the same worker tick.
"""
from __future__ import annotations

import inspect
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "/app/backend")


# ── Test 1: rev30 marker registered + present in build ───────────
def test_1_rev30_marker_registered_and_present():
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )
    assert "rev30_payment_continuation" in REQUIRED_MARKERS
    r = build_diagnostics_report()
    m = r["marker_check"]["markers"]["rev30_payment_continuation"]
    assert m["present"] is True
    assert m["count"] >= 1
    assert r["acceptance"]["code_matches_expected"] is True


# ── Test 2: helper detects prepaid vs COD ────────────────────────
def test_2_is_pm_expecting_payment_helper():
    from integrations.qoyod.pipeline import _is_pm_expecting_payment
    # Prepaid → payment expected.
    assert _is_pm_expecting_payment("tabby_installment") is True
    assert _is_pm_expecting_payment("mada") is True
    assert _is_pm_expecting_payment("apple_pay") is True
    # COD-family → NOT expected.
    assert _is_pm_expecting_payment("cash_on_delivery") is False
    assert _is_pm_expecting_payment("cod") is False
    # None → False.
    assert _is_pm_expecting_payment(None) is False


# ── helper: diagnostics on a synthetic row ───────────────────────
async def _diag(row, settings=None):
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=row)
    db.qoyod_settings.find_one   = AsyncMock(return_value=settings or {})
    return await row_diagnostics(db, row["trace_id"])


# ── Test 3: prod trace 4dc65ba6 replay flags silent_stuck ───────
@pytest.mark.asyncio
async def test_3_prod_trace_4dc65ba6_replay_flags_silent_stuck():
    """Replay of the exact prod symptom: Tabby dry row stuck at
    INVOICE_CREATED with no persisted blocker fields. rev30
    diagnostics MUST synthesise
    `payment_stage_blocker_code=silent_stuck_at_invoice_created`."""
    row = {
        "id":              "row-4dc65ba6",
        "trace_id":        "4dc65ba6eb5646e5afbf915268c70fcc",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id": "DRY:contact:aaa",
        "qoyod_invoice_id":  "DRY:invoice:e111b4db",
        "qoyod_invoice_payment_id": None,
        "canonical_payload": {"payment_method": "tabby_installment"},
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible",
            "resolved_payment_key": "tabby_installment",
        },
        "selective_auto_send_gate_at":     "2026-02-03T10:00:00+00:00",
        "selective_auto_send_gate_source": "sas_enabled_at_worker",
        "stage_history": [],
    }
    out = await _diag(row, settings={
        "dry_run_mode": True,
        "selective_live_send_enabled": False,
        "selective_auto_send_enabled": True,
    })
    d = out["diagnosis"]
    # Tabby → prepaid → expects invoice_payment step.
    assert d["invoice_payment_required_for_method"] is True
    # Row at INVOICE_CREATED + no persisted blocker → silent_stuck.
    assert d["payment_stage_blocker_code"] == "silent_stuck_at_invoice_created"
    assert "INVOICE_CREATED with no downstream transition" in \
        (d["payment_stage_blocker_reason"] or "")
    # payment_stage_expected is derived from the payment method here.
    assert d["payment_stage_expected"] is True
    # No preview persisted → False.
    assert d["payment_payload_preview_exists"] is False


# ── Test 4: row at COMPLETED_INVOICE_ONLY exposes blocker fields
@pytest.mark.asyncio
async def test_4_completed_invoice_only_exposes_blocker_fields():
    """A row that intentionally stopped due to
    posting_mode=disabled lands at COMPLETED_INVOICE_ONLY with the
    blocker fields persisted. Diagnostics reflect them verbatim."""
    row = {
        "id":              "row-cod",
        "trace_id":        "cod-trace",
        "user_id":         "main",
        "pipeline_stage":  "COMPLETED_INVOICE_ONLY",
        "qoyod_customer_id": "DRY:contact:cod",
        "qoyod_invoice_id":  "DRY:invoice:cod",
        "canonical_payload": {"payment_method": "cash_on_delivery"},
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible"},
        "selective_auto_send_gate_at": "2026-02-03T10:00:00+00:00",
        # Persisted by the pipeline short-circuit branch.
        "payment_stage_blocker_code":   "posting_mode_disabled",
        "payment_stage_blocker_reason": "cod family",
        "stage_history": [],
    }
    out = await _diag(row, settings={"dry_run_mode": True})
    d = out["diagnosis"]
    # COD → payment NOT required.
    assert d["invoice_payment_required_for_method"] is False
    # Persisted blocker surfaces verbatim.
    assert d["payment_stage_blocker_code"] == "posting_mode_disabled"
    # Terminal state — payment intentionally NOT expected.
    assert d["payment_stage_expected"] is False


# ── Test 5: COMPLETED row (happy path) has no blocker ────────────
@pytest.mark.asyncio
async def test_5_completed_row_no_blocker():
    row = {
        "id":              "row-happy",
        "trace_id":        "happy-trace",
        "user_id":         "main",
        "pipeline_stage":  "COMPLETED",
        "qoyod_customer_id": "DRY:contact:x",
        "qoyod_invoice_id":  "DRY:invoice:x",
        "qoyod_invoice_payment_id": "DRY:invoice_payment:x",
        "canonical_payload": {"payment_method": "tabby_installment"},
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible"},
        "selective_auto_send_gate_at": "2026-02-03T10:00:00+00:00",
        "stage_history": [],
    }
    out = await _diag(row, settings={"dry_run_mode": True})
    d = out["diagnosis"]
    assert d["payment_stage_expected"] is True
    assert d["payment_stage_blocker_code"] is None
    assert d["invoice_payment_required_for_method"] is True


# ── Test 6: payment_payload_preview_exists reflects the preview ──
@pytest.mark.asyncio
async def test_6_payment_payload_preview_flag():
    row = {
        "id":              "row-preview",
        "trace_id":        "preview-trace",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "canonical_payload": {"payment_method": "mada"},
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible"},
        "selective_auto_send_gate_at": "2026-02-03T10:00:00+00:00",
        "qoyod_payloads": {
            "invoice_payment": {"invoice_payment": {"amount": 100}},
        },
        "stage_history": [],
    }
    out = await _diag(row, settings={"dry_run_mode": True})
    d = out["diagnosis"]
    assert d["payment_payload_preview_exists"] is True


# ── Test 7: E2E — POSTING_MODE_DISABLED transitions to terminal ──
@pytest.mark.asyncio
async def test_7_posting_mode_disabled_transitions_to_completed_invoice_only():
    """The exact bug from prod: a Tabby dry row with
    posting_mode='disabled' on the mapping used to sit silently at
    INVOICE_CREATED. rev30 MUST transition it to
    COMPLETED_INVOICE_ONLY with persisted blocker fields."""
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    class _Coll:
        def __init__(self, docs): self._docs = list(docs)
        async def find_one(self, q, projection=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    return dict(d)
            return None
        async def insert_one(self, doc):
            self._docs.append(dict(doc))
            return MagicMock(inserted_id="fake")
        async def update_one(self, q, u, upsert=False):
            m = 0
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    m = 1
                    for k, v in (u.get("$set") or {}).items():
                        _set_dotted(d, k, v)
                    for k, v in (u.get("$push") or {}).items():
                        _set_dotted_push(d, k, v)
                    break
            if not m and upsert:
                new = {**q, **(u.get("$set") or {})}
                new.update(u.get("$setOnInsert") or {})
                self._docs.append(new)
                m = 1
            return MagicMock(matched_count=m, modified_count=m)

    def _set_dotted(d, key, value):
        parts = key.split(".")
        cur = d
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    def _set_dotted_push(d, key, value):
        parts = key.split(".")
        cur = d
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur.setdefault(parts[-1], []).append(value)

    row = {
        "id": "row-tabby-disabled",
        "user_id": "main",
        "trace_id": "trace-tabby-dis",
        "salla_order_number": "270166208",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "qoyod_customer_id": "DRY:contact:tabby",
        "canonical_payload": {
            "order_id":       "OID-TABBY",
            "order_number":   "270166208",
            "order_status":   "completed",
            "payment_method": "tabby_installment",
            "salla_order_created_at": "2026-02-03T09:00:00+00:00",
            "customer": {"name": "T", "phone": "+966500000000"},
            "items": [{"sku": "SKU-1", "name": "X", "quantity": 1,
                       "unit_price_ex_tax": 100.0, "tax_amount": 15.0,
                       "line_total": 115.0, "vat_rate": 15}],
            "totals": {"subtotal_ex_tax": 100.0, "tax_amount": 15.0,
                       "shipping_ex_tax": 0.0, "shipping_tax": 0.0,
                       "discount_ex_tax": 0.0, "discount_tax": 0.0,
                       "order_total": 115.0},
        },
        "business_rules_decision": {
            "eligible": True, "invoice_date": "2026-02-03",
            "invoice_date_source": "salla",
            "triggered_by_status": "completed",
        },
        "pipeline_started_at": "2026-02-03T10:00:00+00:00",
        "stage_history": [],
        # rev29c/rev29d — gate persisted.
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible",
            "resolved_payment_key": "tabby_installment",
        },
        "selective_auto_send_gate_at": "2026-02-03T10:00:00+00:00",
        "selective_auto_send_gate_source": "sas_enabled_at_worker",
    }
    settings = {
        "user_id": "main",
        "dry_run_mode": True,
        "selective_auto_send_enabled": True,
        "selective_live_send_enabled": False,
        "selective_auto_send_cutover_at": "2026-01-01T00:00:00+00:00",
        "selective_auto_send_allowed_payment_methods": ["tabby_installment"],
        "invoice_trigger_statuses": ["completed"],
        # Explicitly disable payment_method → posting_mode=disabled.
        "payment_method_mapping": [
            {"salla_method": "tabby_installment", "qoyod_account_id": "92",
             "posting_mode": "disabled"},
        ],
        # Product defaults so the product-preflight passes and we
        # reach the posting_mode branch we want to test.
        "default_product_category_id":  "cat-1",
        "default_product_tax_id":       "tax-1",
        "default_product_unit_type_id": "unit-1",
        "default_sales_account_id":     "acc-1",
        # Invoice-preflight defaults.
        "default_inventory_id":         "inv-1",
        "default_shipping_product_id":  "ship-1",
        "default_tax_id":               "tax-1",
        "tax_mode":                     "customer_first",
        "invoice_total_policy":         "match_salla_total",
        "production_writes_locked": False,
    }
    db = MagicMock()
    db.integration_inbox     = _Coll([dict(row)])
    db.qoyod_settings        = _Coll([dict(settings)])
    db.qoyod_invoices        = _Coll([])
    db.qoyod_invoice_payments = _Coll([])
    db.qoyod_customers       = _Coll([])
    db.qoyod_products        = _Coll([])
    db.qoyod_products_mapping = _Coll([])
    db.qoyod_customers_mapping = _Coll([])
    db.qoyod_write_lock_attempts = _Coll([])

    out = await pmod.process_customer_resolved_row(
        db, dict(row), api_client=DryRunQoyodClient())
    assert out.get("outcome") == "COMPLETED_INVOICE_ONLY", out
    assert out.get("reason") == "posting_mode_disabled", out
    # Row transitioned atomically.
    updated = await db.integration_inbox.find_one({"id": row["id"]})
    assert updated["pipeline_stage"] == "COMPLETED_INVOICE_ONLY"
    # Blocker fields persisted.
    assert updated.get("payment_stage_blocker_code") == "posting_mode_disabled"
    assert updated.get("payment_stage_expected") is False
    assert updated.get("invoice_payment_required_for_method") is True
    # stage_history has the terminal transition and no misleading
    # "invoice_payment recorded ON invoice in Qoyod" note.
    notes = [e.get("note") for e in (updated.get("stage_history") or [])]
    assert any("posting_mode=disabled" in (n or "") for n in notes), notes
    assert not any("invoice_payment recorded ON invoice in Qoyod"
                   in (n or "") for n in notes)


# ── Test 8: rev27 live-write gate + rev29 CAS intact ─────────────
def test_8_upstream_invariants_intact():
    from integrations.qoyod.pipeline import _live_write_permitted
    ok, _ = _live_write_permitted({
        "dry_run_mode": False,
        "selective_live_send_enabled": True,
        "production_writes_locked": False,
        "selective_auto_send_enabled": True,
    })
    assert ok is True


# ── Test 9: rev30 uses CAS transition (source-side proof) ────────
def test_9_rev30_uses_cas_transition():
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    # The two short-circuits transition via _apply_atomic with
    # expected_from_stage="INVOICE_CREATED" so no other worker can
    # advance the row twice.
    assert src.count("expected_from_stage=\"INVOICE_CREATED\"") >= 3, (
        "rev30 requires CAS transitions from INVOICE_CREATED for BOTH "
        "the posting_mode=disabled site and the auto_receipt=false "
        "site (in addition to the existing rev29 CAS sites).")
    assert "COMPLETED_INVOICE_ONLY" in src


# ── Test 10: dry_run_wording — no misleading dry payment note ────
def test_10_no_misleading_payment_note_in_disabled_dry_run():
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    # The disabled-branch DRY note must NOT say
    # "invoice_payment recorded ON invoice in Qoyod".
    disabled_block_start = src.find(
        "if _posting_mode == POSTING_MODE_DISABLED:")
    disabled_block_end = src.find(
        "# auto_receipt / create_receipts capability gate",
        disabled_block_start)
    block = src[disabled_block_start:disabled_block_end]
    assert "invoice_payment recorded ON invoice in Qoyod" not in block
    # And the block includes a clear DRY-RUN description.
    assert "DRY-RUN: posting_mode=disabled" in block
