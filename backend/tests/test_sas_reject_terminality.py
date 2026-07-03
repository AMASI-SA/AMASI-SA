"""Iter-2026-02.rev26 — SAS reject terminality invariants.

After Salla order 270212453 (mada, trace 380e76d8...) was rejected by
the SAS gate with `payment_method_not_in_allow_list` yet still advanced
to `INVOICE_CREATED` (with `DRY:invoice:11f8547e`), the user demanded
these 9 invariants — enforced here.

Requirements exactly (verbatim from user directive):

  1. mada not in allow-list → final pipeline_stage = SKIPPED only.
  2. After PM_NOT_ALLOWED, customer_resolver is NOT called.
  3. After PM_NOT_ALLOWED, product_resolver is NOT called.
  4. After PM_NOT_ALLOWED, invoice payload is NOT built.
  5. stage_history contains NO RULES_APPLIED after SKIPPED.
  6. NORMALIZED → RULES_APPLIED transition FAILS if current stage is
     already SKIPPED (atomic CAS).
  7. Stale-worker / concurrency: A writes SKIPPED. B has stale
     NORMALIZED snapshot. B cannot advance to RULES_APPLIED.
  8. diagnostics.control_flow_violation=true when SAS eligible=false
     AND row is at INVOICE_CREATED.
  9. Any rejected SAS gate blocks ALL Qoyod side-effects — customer,
     product, invoice, payment.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")


# ── Shared fixtures ──────────────────────────────────────────────────
CUTOVER_ISO   = "2026-07-01T00:00:00+00:00"
AFTER_CUTOVER = "2026-07-05T10:00:00+00:00"


def _mada_canonical() -> dict:
    return {
        "order_id":              "MZN-999-mada",
        "order_number":          "999002",
        "order_status":          "completed",
        "order_status_native":   "completed",
        "order_date":            AFTER_CUTOVER,
        "payment_method":        "mada",
        "payment_method_native": "mada",
        "currency":              "SAR",
        "subtotal":              130.43,
        "tax_amount":            19.57,
        "total_amount":          150.00,
        "items": [{
            "sku":         "SKU-M",
            "name":        "T",
            "quantity":    1,
            "unit_price":  130.43,
            "tax_amount":  19.57,
            "total":       150.00,
        }],
        "customer": {"name": "T", "phone": "+966500000000"},
    }


def _sas_settings_tabby_only() -> dict:
    return {
        "user_id":                                 "main",
        "production_writes_locked":                True,
        "dry_run_mode":                            True,
        "selective_live_send_enabled":             False,
        "selective_auto_send_enabled":             True,
        "selective_auto_send_cutover_at":          CUTOVER_ISO,
        "selective_auto_send_allowed_payment_methods":
            ["tabby_installment"],
        "payment_method_mapping": [
            {"salla_method": "tabby_installment",
             "qoyod_account_id": "92",
             "qoyod_payment_method_id": "92"},
        ],
        "default_customer_id":  "230",
        "invoice_trigger_statuses": ["completed"],
        "invoice_date_source":  "send_date",
        "auto_receipt":         True,
        "capabilities":         {"create_receipts": True},
        "trigger_once_only":    True,
    }


class _AtomicColl:
    """Realistic-enough mock: honours filter fields when matching."""
    def __init__(self, docs):
        self._docs = list(docs)

    async def find_one(self, q, projection=None):
        for d in self._docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None

    async def update_one(self, q, u, upsert=False):
        matched = 0
        modified = 0
        for d in self._docs:
            if all(d.get(k) == v for k, v in q.items()):
                matched = 1
                for k, v in (u.get("$set") or {}).items():
                    d[k] = v
                for k in (u.get("$unset") or {}):
                    d.pop(k, None)
                for k, v in (u.get("$push") or {}).items():
                    arr = d.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        arr.extend(v["$each"])
                        if "$slice" in v:
                            s = v["$slice"]
                            arr[:] = arr[s:] if s < 0 else arr[:s]
                    else:
                        arr.append(v)
                for k, v in (u.get("$inc") or {}).items():
                    d[k] = (d.get(k) or 0) + v
                modified = 1
                break
        return MagicMock(matched_count=matched, modified_count=modified)

    async def insert_one(self, d):
        self._docs.append(d); return MagicMock(inserted_id="new")

    def find(self, q=None, **kw):
        class _Cur:
            def __init__(self, ds): self.ds = ds
            def __aiter__(self):
                async def _gen():
                    for d in self.ds: yield d
                return _gen()
        return _Cur(list(self._docs))


def _row_at_normalized_mada() -> dict:
    return {
        "id":                     "row-mada-1",
        "user_id":                "main",
        "salla_order_number":     "999002",
        "trace_id":               "trace-mada-1",
        "pipeline_stage":         "NORMALIZED",
        "canonical_payload":      _mada_canonical(),
        "pipeline_started_at":    AFTER_CUTOVER,
        "stage_history":          [],
    }


def _fresh_db(rows=None, settings=None):
    db = MagicMock()
    db.qoyod_settings         = _AtomicColl([dict(settings or _sas_settings_tabby_only())])
    db.integration_inbox      = _AtomicColl(list(rows or []))
    db.qoyod_invoices         = _AtomicColl([])
    db.qoyod_invoice_payments = _AtomicColl([])
    db.qoyod_write_lock_attempts = _AtomicColl([])
    db.qoyod_customers        = _AtomicColl([])
    db.qoyod_products         = _AtomicColl([])
    return db


# ── Test 1: mada → final stage MUST be SKIPPED only ─────────────────
@pytest.mark.asyncio
async def test_1_mada_rejected_ends_at_skipped():
    from integrations.qoyod import pipeline as pmod
    row = _row_at_normalized_mada()
    db  = _fresh_db(rows=[row])

    out = await pmod.process_normalized_row(db, dict(row))

    assert out["outcome"] == "SKIPPED"
    assert out["reason"] == "payment_method_not_in_allow_list"

    updated = await db.integration_inbox.find_one({"id": row["id"]})
    assert updated["pipeline_stage"] == "SKIPPED"
    # No further stages recorded.
    stages_visited = [
        e.get("to_stage") for e in (updated.get("stage_history") or [])]
    for forbidden in ("RULES_APPLIED", "CUSTOMER_RESOLVED",
                      "PRODUCT_RESOLVED", "INVOICE_CREATED",
                      "INVOICE_PAYMENT_CREATED", "COMPLETED"):
        assert forbidden not in stages_visited, (
            f"forbidden stage {forbidden!r} in history {stages_visited!r}")


# ── Test 2: customer_resolver NOT called after PM_NOT_ALLOWED ───────
@pytest.mark.asyncio
async def test_2_customer_resolver_not_called_after_reject():
    from integrations.qoyod import pipeline as pmod
    row = _row_at_normalized_mada()
    db  = _fresh_db(rows=[row])

    with patch.object(pmod, "resolve_customer",
                      new=AsyncMock()) as mocked_resolver:
        out = await pmod.process_normalized_row(db, dict(row))

    assert out["outcome"] == "SKIPPED"
    assert mocked_resolver.call_count == 0, (
        "resolve_customer MUST NOT be called after SAS rejects")


# ── Test 3: product_resolver NOT called after PM_NOT_ALLOWED ────────
@pytest.mark.asyncio
async def test_3_product_resolver_not_called_after_reject():
    from integrations.qoyod import pipeline as pmod
    row = _row_at_normalized_mada()
    db  = _fresh_db(rows=[row])

    with patch.object(pmod, "resolve_products",
                      new=AsyncMock()) as mocked:
        out = await pmod.process_normalized_row(db, dict(row))

    assert out["outcome"] == "SKIPPED"
    assert mocked.call_count == 0


# ── Test 4: invoice payload NOT built after PM_NOT_ALLOWED ──────────
@pytest.mark.asyncio
async def test_4_invoice_payload_not_built_after_reject():
    from integrations.qoyod import pipeline as pmod
    row = _row_at_normalized_mada()
    db  = _fresh_db(rows=[row])

    with patch.object(pmod, "build_invoice_payload") as mocked:
        out = await pmod.process_normalized_row(db, dict(row))

    assert out["outcome"] == "SKIPPED"
    assert mocked.call_count == 0


# ── Test 5: stage_history contains NO RULES_APPLIED after SKIPPED ───
@pytest.mark.asyncio
async def test_5_no_rules_applied_after_skipped():
    from integrations.qoyod import pipeline as pmod
    row = _row_at_normalized_mada()
    db  = _fresh_db(rows=[row])

    await pmod.process_normalized_row(db, dict(row))
    updated = await db.integration_inbox.find_one({"id": row["id"]})
    hist = updated.get("stage_history") or []
    to_stages = [e.get("to_stage") for e in hist]
    assert "SKIPPED" in to_stages
    assert "RULES_APPLIED" not in to_stages


# ── Test 6: atomic CAS on NORMALIZED → RULES_APPLIED ────────────────
@pytest.mark.asyncio
async def test_6_atomic_cas_normalized_to_rules_applied_fails_when_stale():
    """Directly exercise `_apply_atomic`: if pipeline_stage is not
    the expected `from_stage`, the write raises `_StaleStageError`."""
    from integrations.qoyod.pipeline import (
        _apply_atomic, _StaleStageError,
    )
    row = _row_at_normalized_mada()
    row["pipeline_stage"] = "SKIPPED"  # already terminal in DB
    db = _fresh_db(rows=[row])

    with pytest.raises(_StaleStageError) as ei:
        await _apply_atomic(
            db, row["id"],
            {"$set": {"pipeline_stage": "RULES_APPLIED"}},
            expected_from_stage="NORMALIZED",
        )
    assert ei.value.expected_from == "NORMALIZED"
    assert ei.value.actual == "SKIPPED"


# ── Test 7: stale-worker concurrency simulation ─────────────────────
@pytest.mark.asyncio
async def test_7_stale_worker_cannot_advance_past_skipped():
    """Worker A already wrote SKIPPED. Worker B has stale snapshot at
    NORMALIZED. B calls process_normalized_row → must abort with
    STALE_STAGE_ABORT and never touch resolvers/builders."""
    from integrations.qoyod import pipeline as pmod

    # Row is CURRENTLY SKIPPED in the "DB" (worker A already wrote).
    persisted = _row_at_normalized_mada()
    persisted["pipeline_stage"] = "SKIPPED"
    persisted["selective_auto_send_gate"] = {
        "eligible": False, "reason": "payment_method_not_in_allow_list",
    }
    db = _fresh_db(rows=[persisted])

    # But worker B holds a STALE in-memory snapshot at NORMALIZED.
    stale_snapshot = dict(persisted)
    stale_snapshot["pipeline_stage"] = "NORMALIZED"
    stale_snapshot.pop("selective_auto_send_gate", None)

    resolver = AsyncMock()
    builder  = MagicMock()
    with patch.object(pmod, "resolve_customer", new=resolver), \
         patch.object(pmod, "resolve_products", new=AsyncMock()), \
         patch.object(pmod, "build_invoice_payload", new=builder):
        out = await pmod.process_normalized_row(db, stale_snapshot)

    # Whether B's SAS re-eval rejects again OR the CAS aborts, the
    # outcome MUST NOT be advancement past SKIPPED.
    assert out["outcome"] in ("STALE_STAGE_ABORT", "SKIPPED"), (
        f"stale worker advanced past SKIPPED — outcome={out.get('outcome')}")
    # And NONE of the Qoyod side-effect functions were called.
    assert resolver.call_count == 0
    assert builder.call_count  == 0


# ── Test 8: diagnostics surfaces control_flow_violation ─────────────
@pytest.mark.asyncio
async def test_8_diagnostics_reports_violation_when_advanced_past_reject():
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics

    # Simulate the exact production bug: SAS rejected but row is at
    # INVOICE_CREATED with DRY id.
    corrupted_row = {
        "id":                "row-viol",
        "trace_id":          "trace-viol",
        "pipeline_stage":    "INVOICE_CREATED",
        "selective_auto_send_gate": {
            "eligible": False,
            "reason":   "payment_method_not_in_allow_list",
        },
        "qoyod_invoice_id":  "DRY:invoice:11f8547e",
        "canonical_payload": {"payment_method": "mada"},
        "stage_history":     [],
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=corrupted_row)

    out = await row_diagnostics(db, "trace-viol")
    assert out["found"] is True
    assert out["diagnosis"]["control_flow_violation"] is True
    assert "SAS rejected but row advanced" in out["diagnosis"]["violation_reason"]


# ── Test 9: any rejected SAS gate blocks ALL Qoyod side-effects ─────
@pytest.mark.asyncio
async def test_9_rejected_sas_blocks_all_side_effects():
    """Full end-to-end: mada order → process_normalized_row →
    zero Qoyod-side calls made."""
    from integrations.qoyod import pipeline as pmod
    row = _row_at_normalized_mada()
    db  = _fresh_db(rows=[row])

    resolve_customer_mock = AsyncMock()
    resolve_products_mock = AsyncMock()
    build_invoice_mock    = MagicMock()
    build_payment_mock    = MagicMock()

    with patch.object(pmod, "resolve_customer",
                      new=resolve_customer_mock), \
         patch.object(pmod, "resolve_products",
                      new=resolve_products_mock), \
         patch.object(pmod, "build_invoice_payload",
                      new=build_invoice_mock), \
         patch.object(pmod, "build_invoice_payment_payload",
                      new=build_payment_mock):
        out = await pmod.process_normalized_row(db, dict(row))

    assert out["outcome"] == "SKIPPED"
    assert resolve_customer_mock.call_count == 0
    assert resolve_products_mock.call_count == 0
    assert build_invoice_mock.call_count    == 0
    assert build_payment_mock.call_count    == 0
