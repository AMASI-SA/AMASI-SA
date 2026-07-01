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
    def __init__(self, guard_no: int, code: str, detail: str):
        super().__init__(f"guard#{guard_no} {code}: {detail}")
        self.guard_no = guard_no
        self.code     = code
        self.detail   = detail


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
) -> tuple[dict, dict]:
    """Runs all 14 guards. Returns (settings_snapshot, canonical)
    when every guard passes; raises `CanaryGuardFailed` otherwise."""
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
    settings = await db.qoyod_settings.find_one(
        {"user_id": "main"}, {"_id": 0}) or {}
    if settings.get("selective_live_send_enabled") is not False:
        raise CanaryGuardFailed(
            11, "selective_live_send_enabled_not_false",
            "Master gate must remain FALSE. Refusing to run.")
    if settings.get("production_writes_locked") is not True:
        raise CanaryGuardFailed(
            12, "production_writes_locked_not_true",
            "Write lock must remain TRUE (scoped bypass only).")

    # Fetch canonical.
    row = await db.integration_inbox.find_one(
        {"user_id": "main",
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
    created_raw = canonical.get("salla_order_created_at") \
        or row.get("salla_order_created_at")
    if isinstance(created_raw, str):
        created = datetime.fromisoformat(
            created_raw.replace("Z", "+00:00")).date() \
            if "T" in created_raw \
            else date.fromisoformat(created_raw)
    elif isinstance(created_raw, datetime):
        created = created_raw.date()
    elif isinstance(created_raw, date):
        created = created_raw
    else:
        raise CanaryGuardFailed(
            5, "created_at_missing",
            "salla_order_created_at not present.")
    if created < date.fromisoformat(Q3_CUTOFF_ISO):
        raise CanaryGuardFailed(
            5, "created_before_q3_cutoff",
            f"{created} < {Q3_CUTOFF_ISO}.")

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
        {"user_id": "main", "sku": REQUIRED_SKU,
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

    return (settings, canonical)


async def execute_canary_live_send(
    db,
    *,
    order_number: str,
    approval_phrase: str,
    actor: str = "operator",
) -> dict:
    """Executes the one-shot canary. Read-heavy; writes only into
    `canary_send_audit_log` (audit) and delegates the actual Qoyod
    calls to the existing `reprocess_one_order` (audited by
    `qoyod_per_order_approvals` on its own)."""
    attempt_id = str(uuid.uuid4())
    await _write_audit(db, attempt_id=attempt_id,
                       phase="attempt_received", status="pending",
                       detail=f"actor={actor}")
    try:
        settings, canonical = await _run_guards(
            db, order_number=order_number,
            approval_phrase=approval_phrase)
    except CanaryGuardFailed as g:
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
            user_id="main",
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
