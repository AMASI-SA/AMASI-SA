"""Iter-2026-02.rev21 — Approve a LOCKED_AWAITING_APPROVAL payment.

Scope
─────
When `retry_payment_only` or `process_customer_resolved_row` hits
`production_writes_locked=True`, the outbound `/invoice_payments`
POST is parked (payload saved to `qoyod_write_lock_attempts` and to
the row at `qoyod_payloads.invoice_payment_locked_payload`) with
`pipeline_stage=LOCKED_AWAITING_APPROVAL`.

This endpoint replays THAT parked payload — nothing else. No
invoice creation, no customer creation, no product creation, no
recompute. The confirm token is scoped to the exact order.

Contract (STRICT — user directive 2026-02-27)
─────────────────────────────────────────────
  • Refuses if `lock_attempt_id` doesn't exist.
  • Refuses if attempt.action != "create_invoice_payment".
  • Refuses if `confirm_token != "APPROVE-PAYMENT-<order_number>"`.
  • Refuses if the row already has a real `qoyod_invoice_payment_id`
    for this fingerprint (ALREADY_PAID via ledger).
  • ONE قيود API call: `POST /invoice_payments` with the SAVED
    payload — never re-built.
  • Uses `write_lock_enabled=False` on the api_client, scoped to
    THIS approval only. DB `production_writes_locked` stays TRUE.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _NOW() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApproveLockedPaymentRefused(Exception):
    def __init__(self, code: str, human: str, **extra):
        super().__init__(human)
        self.code  = code
        self.extra = extra


async def approve_locked_payment(
    db, *,
    user_id:         str,
    lock_attempt_id: str,
    confirm_token:   str,
    actor:           str = "operator",
) -> dict:
    """Replay a parked invoice_payment payload. Returns a rich
    dict with the قيود response id and updated row state."""
    # ── 1. Load the parked attempt ────────────────────────────────
    attempt = await db.qoyod_write_lock_attempts.find_one(
        {"attempt_id": lock_attempt_id})
    if attempt is None:
        raise ApproveLockedPaymentRefused(
            "lock_attempt_not_found",
            f"No qoyod_write_lock_attempts row with attempt_id="
            f"{lock_attempt_id!r}.")
    if str(attempt.get("user_id")) != str(user_id):
        raise ApproveLockedPaymentRefused(
            "tenant_mismatch",
            "Attempt belongs to a different tenant.")

    action = attempt.get("action")
    if action != "create_invoice_payment":
        raise ApproveLockedPaymentRefused(
            "wrong_action",
            f"This endpoint approves invoice_payment only. "
            f"Attempt action = {action!r}.",
            attempt_action=action)

    order_number = attempt.get("order_number")
    expected     = f"APPROVE-PAYMENT-{order_number}"
    if confirm_token != expected:
        raise ApproveLockedPaymentRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{expected}' to approve this attempt.")

    payload = attempt.get("locked_payload") or {}
    inv_pay = (payload.get("invoice_payment")
               if isinstance(payload, dict) else None) or {}
    invoice_id = inv_pay.get("invoice_id")
    amount     = inv_pay.get("amount")
    if not invoice_id or amount is None:
        raise ApproveLockedPaymentRefused(
            "malformed_payload",
            "Parked payload missing invoice_id or amount — refusing "
            "to send a partial POST.",
            invoice_id=invoice_id, amount=amount)

    # ── 2. Load the row + already-paid guard ──────────────────────
    row = await db.integration_inbox.find_one(
        {"user_id": user_id,
         "salla_order_number": str(order_number)})
    if row is None:
        raise ApproveLockedPaymentRefused(
            "row_not_found",
            f"No integration_inbox row for order {order_number}.")
    trace_id = row.get("trace_id")

    if row.get("qoyod_invoice_payment_id"):
        return {
            "ok":      True,
            "outcome": "ALREADY_PAID",
            "code":    "already_paid_on_row",
            "qoyod_invoice_id":          row.get("qoyod_invoice_id"),
            "qoyod_invoice_payment_id":  row.get(
                "qoyod_invoice_payment_id"),
            "detail": "Row already carries a qoyod_invoice_payment_id "
                      "— skipping the parked replay to avoid a "
                      "duplicate POST.",
            "no_qoyod_api_calls": True,
        }

    # Ledger idempotency (same shape retry_payment_only uses).
    canonical = row.get("canonical_payload") or {}
    ledger_q = {
        "user_id":          user_id,
        "salla_order_id":   canonical.get("order_id"),
        "qoyod_invoice_id": invoice_id,
    }
    led = await db.qoyod_invoice_payments.find_one(
        ledger_q, {"_id": 0})
    if led and led.get("qoyod_invoice_payment_id"):
        return {
            "ok":      True,
            "outcome": "ALREADY_PAID",
            "code":    "already_paid_on_ledger",
            "qoyod_invoice_id":         invoice_id,
            "qoyod_invoice_payment_id": led.get(
                "qoyod_invoice_payment_id"),
            "detail": "Ledger already carries a receipt for this "
                      "invoice — skipping the parked replay.",
            "no_qoyod_api_calls": True,
        }

    # ── 3. Build a SCOPED live api client ─────────────────────────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    from integrations.qoyod.api_client import (
        QoyodAPIClient, get_api_key,
    )
    api_key = await get_api_key(db, user_id)
    if not api_key:
        raise ApproveLockedPaymentRefused(
            "missing_api_key",
            "Tenant has no قيود API key configured.",
            user_id=user_id)
    api_client = QoyodAPIClient(
        api_key, db=db, user_id=user_id,
        write_lock_enabled=False,   # scoped bypass — DB stays LOCKED
    )

    # ── 4. Replay POST /invoice_payments (saved payload verbatim) ─
    idem = attempt.get("idempotency_key") or (
        f"mzn-{trace_id}-invoice-payment-{invoice_id}")
    try:
        resp = await api_client.create_invoice_payment(
            payload, idem=idem)
    except Exception as exc:
        return {
            "ok":       False,
            "outcome":  "POST_FAILED",
            "code":     type(exc).__name__,
            "detail":   str(exc)[:500],
            "attempt_id":                lock_attempt_id,
            "payload_replayed":          payload,
            "invoice_id":                invoice_id,
            "no_qoyod_api_calls":        False,
        }

    r = (resp.get("invoice_payment")
         if isinstance(resp, dict)
         and isinstance(resp.get("invoice_payment"), dict)
         else (resp if isinstance(resp, dict) else {}))
    qoyod_invoice_payment_id = (
        str(r.get("id")) if r.get("id") is not None else None)
    if not qoyod_invoice_payment_id:
        return {
            "ok":                False,
            "outcome":           "NO_PAYMENT_ID_IN_RESPONSE",
            "detail":            "قيود response carried no `id`.",
            "raw_response":      resp,
            "attempt_id":        lock_attempt_id,
        }

    now = _NOW()

    # ── 5. Persist ledger + row + attempt approval ────────────────
    await db.qoyod_invoice_payments.update_one(
        ledger_q,
        {"$set": {
            "user_id":                  user_id,
            "trace_id":                 trace_id,
            "salla_order_id":           canonical.get("order_id"),
            "salla_order_number":       canonical.get("order_number"),
            "qoyod_invoice_id":         invoice_id,
            "qoyod_invoice_payment_id": qoyod_invoice_payment_id,
            "amount":                   amount,
            "currency":                 canonical.get("currency")
                                        or "SAR",
            "payment_method":           inv_pay.get("payment_method"),
            "source":                   "approve_locked_payment",
            "approved_by":              actor,
            "approved_at":              now,
            "updated_at":               now,
        },
         "$setOnInsert": {
             "id":         uuid.uuid4().hex,
             "created_at": now,
         }},
        upsert=True,
    )

    row_res = await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {
            "qoyod_invoice_payment_id":  qoyod_invoice_payment_id,
            "pipeline_stage":            "COMPLETED",
            "lock_approved_at":          now,
            "lock_approved_by":          actor,
            "lock_approval_source":      "approve_locked_payment",
         },
         "$push": {"stage_history": {
             "from_stage": "LOCKED_AWAITING_APPROVAL",
             "to_stage":   "COMPLETED",
             "at":         now,
             "actor":      actor,
             "note": (f"approve_locked_payment attempt="
                      f"{lock_attempt_id} → payment id="
                      f"{qoyod_invoice_payment_id}"),
         }}})

    await db.qoyod_write_lock_attempts.update_one(
        {"attempt_id": lock_attempt_id},
        {"$set": {
            "approved":                 True,
            "approved_by":              actor,
            "approved_at":              now,
            "qoyod_invoice_payment_id": qoyod_invoice_payment_id,
        }})

    return {
        "ok":                        True,
        "outcome":                   "COMPLETED",
        "attempt_id":                lock_attempt_id,
        "qoyod_invoice_id":          invoice_id,
        "qoyod_invoice_payment_id":  qoyod_invoice_payment_id,
        "amount":                    amount,
        "payload_replayed":          payload,
        "raw_response":              resp,
        "final_stage":               "COMPLETED",
        "db_row_update_result": {
            "matched":  getattr(row_res, "matched_count", None),
            "modified": getattr(row_res, "modified_count", None),
        },
        "human_message": (
            "تم اعتماد السداد المُقفل وإرسال POST /invoice_payments "
            "فقط. الفاتورة الحقيقية لم تُنشأ من جديد. الرصيد في قيود "
            "أصبح 0.00 ورقم السداد محفوظ في ميزان."),
    }
