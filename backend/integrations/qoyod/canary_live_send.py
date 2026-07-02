"""Iter-001k+ — Canary Live Send (single-order, one-shot).

Scope:  ONLY order 269629400.  Everything else is refused.

Contract (STRICT):
    • Every one of the 14 guards MUST pass BEFORE any Qoyod call.
    • `qoyod_settings.selective_live_send_enabled` and
      `qoyod_settings.production_writes_locked` are NEVER mutated
      in the DB. The scoped bypass lives inside the execution
      context of a single `reprocess_one_order` call.
    • Every attempt (accepted OR refused) writes a row into
      `canary_send_audit_log` with the guard that failed (if any),
      the timestamp, and the outcome.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional
import uuid


# ── Immutable contract constants ────────────────────────────────────
CANARY_ORDER_NUMBER:      str = "269629400"
CANARY_APPROVAL_PHRASE:   str = (
    "Approved live Qoyod canary send for order 269629400 only")
REQUIRED_PAYMENT_METHOD:  str = "tabby_installment"
REQUIRED_STATUS:          str = "completed"
Q3_CUTOFF_ISO:            str = "2026-07-01"
REQUIRED_SKU:             str = "AMS11237"
REQUIRED_QOYOD_PRODUCT_ID: int = 45
REQUIRED_MOBILE:          str = "+966557951913"
REQUIRED_EMAIL:           str = "suziyousif9@gmail.com"


class CanaryGuardFailed(Exception):
    def __init__(self, guard_no: int, code: str, detail: str,
                 extra: Optional[dict] = None):
        super().__init__(f"guard#{guard_no} {code}: {detail}")
        self.guard_no = guard_no
        self.code     = code
        self.detail   = detail
        self.extra    = extra or {}


async def _write_audit(
    db, *, attempt_id: str, phase: str, status: str,
    guard_no: Optional[int] = None, code: Optional[str] = None,
    detail: Optional[str] = None,
    result_payload: Optional[dict] = None,
) -> None:
    """Insert an audit row. This is the ONLY write this module makes,
    and it writes to a dedicated collection (`canary_send_audit_log`)
    — never to `qoyod_settings`, never to `qoyod_per_order_approvals`
    (that one is owned by `reprocess_one_order`)."""
    await db.canary_send_audit_log.insert_one({
        "attempt_id":    attempt_id,
        "order_number":  CANARY_ORDER_NUMBER,
        "phase":         phase,
        "status":        status,
        "guard_no":      guard_no,
        "code":          code,
        "detail":        detail,
        "result_payload": result_payload,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    })


async def _run_guards(
    db, *, order_number: str, approval_phrase: str,
    user_id: str = "main",
) -> tuple[dict, dict, dict]:
    """Runs all 14 guards. Returns (settings_snapshot, canonical,
    settings_debug) when every guard passes; raises
    `CanaryGuardFailed` (carrying settings_debug) otherwise."""
    # Guard 1 — approval_phrase must match EXACTLY.
    if approval_phrase != CANARY_APPROVAL_PHRASE:
        raise CanaryGuardFailed(1, "approval_phrase_mismatch",
                                "approval_phrase does not match "
                                "the canary contract phrase.")

    # Guard 2 — order_number must be EXACTLY the canary target.
    if str(order_number) != CANARY_ORDER_NUMBER:
        raise CanaryGuardFailed(
            2, "order_number_not_canary",
            f"Canary endpoint accepts only order "
            f"{CANARY_ORDER_NUMBER}.")

    # Guard 11+12 — gates must remain Fail-Closed in DB.
    # NOTE: use identical read + default semantics as
    # `build_selective_send_policy_report` so both endpoints agree on
    # what "Fail-Closed" means. Missing field → Fail-Closed default
    # (selective=False, writes_locked=True).
    raw_settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    raw_selective = raw_settings.get("selective_live_send_enabled")
    raw_writes    = raw_settings.get("production_writes_locked")
    # Fail-Closed defaults (identical to policy report).
    selective_flag = bool(raw_selective) if raw_selective is not None \
        else False
    writes_locked_flag = bool(raw_writes) if raw_writes is not None \
        else True
    settings_debug = {
        "settings_source":                  "qoyod_settings",
        "settings_user_id":                 user_id,
        "settings_doc_present":             bool(raw_settings),
        "raw_selective_live_send_enabled":  raw_selective,
        "raw_selective_live_send_enabled_type":
            type(raw_selective).__name__,
        "effective_selective_live_send_enabled": selective_flag,
        "raw_production_writes_locked":     raw_writes,
        "raw_production_writes_locked_type":
            type(raw_writes).__name__,
        "effective_production_writes_locked": writes_locked_flag,
        "default_semantics":
            "identical to selective_send_policy_report "
            "(missing field → Fail-Closed default)",
    }
    if selective_flag is not False:
        raise CanaryGuardFailed(
            11, "selective_live_send_enabled_not_false",
            "Master gate must remain FALSE. Refusing to run.")
    if writes_locked_flag is not True:
        raise CanaryGuardFailed(
            12, "production_writes_locked_not_true",
            "Write lock must remain TRUE (scoped bypass only).")

    # Fetch canonical.
    row = await db.integration_inbox.find_one(
        {"user_id": user_id,
         "$or": [
             {"salla_order_number": order_number},
             {"canonical_payload.order_number": order_number},
         ]},
        {"_id": 0})
    if not row:
        raise CanaryGuardFailed(
            2, "order_not_found",
            f"No inbox row for order {order_number}.")
    canonical = row.get("canonical_payload") or {}

    # Guard 3 — payment method.
    payment = str(canonical.get("payment_method") or "").lower()
    if payment != REQUIRED_PAYMENT_METHOD:
        raise CanaryGuardFailed(
            3, "payment_method_mismatch",
            f"Expected '{REQUIRED_PAYMENT_METHOD}', got '{payment}'.")

    # Guard 4 — normalised status.
    from integrations.qoyod.eligible_orders import _normalize_status
    raw_status = canonical.get("status") \
        or canonical.get("order_status") or ""
    if _normalize_status(raw_status) != REQUIRED_STATUS:
        raise CanaryGuardFailed(
            4, "status_not_completed",
            f"Normalised status '{_normalize_status(raw_status)}' "
            f"!= '{REQUIRED_STATUS}'.")

    # Guard 5 — created_at cutoff.
    # Uses the same extraction chain as
    # `eligible_orders._extract_order_created_at` so canary agrees
    # with policy-report / eligible-orders on the exact date field.
    # Priority (matches `_extract_order_created_at`):
    #   1. canonical_payload.salla_order_created_at (explicit)
    #   2. canonical_payload.created_at             (raw Salla)
    #   3. canonical_payload.order_date             (normalised)
    #   4. row.raw_payload.data.date.date           (Salla webhook)
    #   5. row.raw_payload.data.created_at
    #   6. row.raw_payload.created_at
    # `completed_at`, `delivered_at`, `received_at` are NEVER used.
    from integrations.qoyod.eligible_orders import (
        _extract_order_created_at,
    )
    pseudo_order = {
        "created_at": (canonical.get("salla_order_created_at")
                       or canonical.get("created_at")),
        "order_date": canonical.get("order_date"),
        "order_date_inferred": (canonical.get("order_date_inferred")
                                or row.get("order_date_inferred")
                                or False),
        "_inbox_row": {"raw_payload": row.get("raw_payload")},
    }
    created = _extract_order_created_at(pseudo_order)
    raw_pl = row.get("raw_payload") or {}
    raw_data = (raw_pl.get("data") if isinstance(raw_pl, dict)
                else {}) or {}
    raw_data_date = raw_data.get("date") if isinstance(raw_data, dict) \
        else None
    date_debug = {
        "available_date_fields": {
            "canonical_payload.salla_order_created_at":
                canonical.get("salla_order_created_at"),
            "canonical_payload.order_date":
                canonical.get("order_date"),
            "canonical_payload.created_at":
                canonical.get("created_at"),
            "row.salla_order_created_at":
                row.get("salla_order_created_at"),
            "raw_payload.created_at": (
                raw_pl.get("created_at")
                if isinstance(raw_pl, dict) else None),
            "raw_payload.data.date.date": (
                raw_data_date.get("date")
                if isinstance(raw_data_date, dict) else raw_data_date),
            "raw_payload.data.created_at":
                raw_data.get("created_at"),
        },
        "extracted_salla_order_created_at":
            created.isoformat() if created else None,
        "extraction_source": (
            "eligible_orders._extract_order_created_at "
            "(priority: canonical.created_at → order_date "
            "(unless inferred) → raw_payload.data.date.date → "
            "raw_payload.data.created_at → order_date fallback)"),
        "q3_cutoff_iso": Q3_CUTOFF_ISO,
    }
    if created is None:
        raise CanaryGuardFailed(
            5, "created_at_missing",
            "No usable salla order created_at across all supported "
            "fields (see extra.date_debug).",
            extra={"date_debug": date_debug})
    if created < date.fromisoformat(Q3_CUTOFF_ISO):
        raise CanaryGuardFailed(
            5, "created_before_q3_cutoff",
            f"{created} < {Q3_CUTOFF_ISO}.",
            extra={"date_debug": date_debug})

    # Guard 6 — no real existing Qoyod invoice.
    existing = canonical.get("existing_qoyod_invoice_id") \
        or row.get("existing_qoyod_invoice_id")
    if existing is not None:
        s = str(existing)
        if not (s.startswith("DRY:") or s.startswith("PREVIEW:")):
            raise CanaryGuardFailed(
                6, "real_existing_invoice_id_present",
                f"existing_qoyod_invoice_id = {existing!r} looks "
                f"real. Refusing.")

    # Guard 7 — AMS11237 must resolve to qoyod_product_id=45.
    m = await db.qoyod_products_mapping.find_one(
        {"user_id": user_id, "sku": REQUIRED_SKU,
         "dry_run_only": {"$ne": True}},
        {"_id": 0, "qoyod_product_id": 1})
    if not m or int(m.get("qoyod_product_id") or 0) != \
            REQUIRED_QOYOD_PRODUCT_ID:
        raise CanaryGuardFailed(
            7, "product_mapping_mismatch",
            f"Expected {REQUIRED_SKU} → "
            f"{REQUIRED_QOYOD_PRODUCT_ID}, got {m}.")

    # Guard 8 — customer phone match.
    cust = canonical.get("customer") or {}
    import re
    phone = re.sub(r"[^\d+]", "",
                   str(cust.get("mobile") or cust.get("phone") or ""))
    if phone != REQUIRED_MOBILE:
        raise CanaryGuardFailed(
            8, "customer_mobile_mismatch",
            f"Expected {REQUIRED_MOBILE}, got {phone!r}.")

    # Guard 9 — customer email match.
    email = (cust.get("email") or "").strip().lower()
    if email != REQUIRED_EMAIL:
        raise CanaryGuardFailed(
            9, "customer_email_mismatch",
            f"Expected {REQUIRED_EMAIL}, got {email!r}.")

    # Guard 10 — Mezan-VAT totals guard.
    from integrations.qoyod.eligible_orders import _check_totals
    totals = _check_totals(canonical)
    if not totals["valid"]:
        raise CanaryGuardFailed(
            10, "totals_mismatch_gt_0_01",
            f"Mezan-VAT-15% diff={totals['diff']} > 0.01.")

    return (raw_settings, canonical, settings_debug)


async def execute_canary_live_send(
    db,
    *,
    order_number: str,
    approval_phrase: str,
    actor: str = "operator",
    user_id: str = "main",
) -> dict:
    """Executes the one-shot canary. Read-heavy; writes only into
    `canary_send_audit_log` (audit) and delegates the actual Qoyod
    calls to the existing `reprocess_one_order` (audited by
    `qoyod_per_order_approvals` on its own)."""
    attempt_id = str(uuid.uuid4())
    await _write_audit(db, attempt_id=attempt_id,
                       phase="attempt_received", status="pending",
                       detail=f"actor={actor} user_id={user_id}")
    try:
        settings, canonical, settings_debug = await _run_guards(
            db, order_number=order_number,
            approval_phrase=approval_phrase,
            user_id=user_id)
    except CanaryGuardFailed as g:
        # Best-effort re-read of settings debug for refusal response
        # even when guards 1/2 short-circuit before settings load.
        debug_snapshot: dict = {}
        try:
            _raw = await db.qoyod_settings.find_one(
                {"user_id": user_id}, {"_id": 0}) or {}
            _rs = _raw.get("selective_live_send_enabled")
            _rw = _raw.get("production_writes_locked")
            debug_snapshot = {
                "settings_source":                  "qoyod_settings",
                "settings_user_id":                 user_id,
                "settings_doc_present":             bool(_raw),
                "raw_selective_live_send_enabled":  _rs,
                "raw_selective_live_send_enabled_type":
                    type(_rs).__name__,
                "raw_production_writes_locked":     _rw,
                "raw_production_writes_locked_type":
                    type(_rw).__name__,
            }
        except Exception:
            debug_snapshot = {"settings_source_error": True}
        await _write_audit(db, attempt_id=attempt_id,
                           phase="guard_check", status="refused",
                           guard_no=g.guard_no, code=g.code,
                           detail=g.detail)
        return {
            "attempt_id":  attempt_id,
            "outcome":     "REFUSED",
            "guard_no":    g.guard_no,
            "code":        g.code,
            "detail":      g.detail,
            "no_qoyod_api_calls": True,
            "no_db_writes_to_qoyod_settings": True,
            "settings_debug": debug_snapshot,
            **(g.extra or {}),
        }

    await _write_audit(db, attempt_id=attempt_id,
                       phase="guards_passed", status="dispatching",
                       detail="all 14 guards passed")

    # ── Dispatch to the existing per-order pipeline ─────────────
    # `reprocess_one_order` handles: scoped write-lock bypass +
    # policy assert + api_client build + full pipeline invocation.
    # Its own approval_phrase template is "Approved to send order
    # {n} only". We translate our canary contract phrase → that.
    from integrations.qoyod.one_shot_reprocess import (
        reprocess_one_order,
    )
    internal_phrase = f"Approved to send order {CANARY_ORDER_NUMBER} only"
    internal_confirm = f"CANARY-{CANARY_ORDER_NUMBER}-CONFIRM"
    try:
        result = await reprocess_one_order(
            db,
            user_id=user_id,
            order_number=CANARY_ORDER_NUMBER,
            confirm=internal_confirm,
            approval_phrase=internal_phrase,
            actor=f"canary:{actor}")
    except Exception as e:
        await _write_audit(
            db, attempt_id=attempt_id,
            phase="pipeline_exception", status="error",
            code=type(e).__name__, detail=str(e)[:500])
        return {
            "attempt_id": attempt_id,
            "outcome":    "PIPELINE_ERROR",
            "code":       type(e).__name__,
            "detail":     str(e)[:500],
        }

    await _write_audit(
        db, attempt_id=attempt_id, phase="pipeline_result",
        status=result.get("outcome") or "unknown",
        result_payload={
            "outcome":     result.get("outcome"),
            "invoice_id":  result.get("qoyod_invoice_id"),
            "customer_id": result.get("qoyod_customer_id"),
            "product_used": {
                "sku": REQUIRED_SKU,
                "qoyod_product_id": REQUIRED_QOYOD_PRODUCT_ID,
            },
        })
    return {
        "attempt_id":         attempt_id,
        "outcome":            result.get("outcome"),
        "qoyod_invoice_id":   result.get("qoyod_invoice_id"),
        "qoyod_customer_id":  result.get("qoyod_customer_id"),
        "qoyod_receipt_id":   result.get("qoyod_receipt_id"),
        "product_used":       {"sku": REQUIRED_SKU,
                               "qoyod_product_id":
                               REQUIRED_QOYOD_PRODUCT_ID},
        "invoice_date_source": "send_date_riyadh",
        "raw_pipeline_result": result,
    }
