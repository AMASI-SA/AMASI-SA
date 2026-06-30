"""Iter-290h.5 — Surgical payment-link retry endpoint.

Why this exists (read carefully)
────────────────────────────────
The general-purpose `one-shot-reprocess` re-runs the entire pipeline
from NORMALIZED. For a row that has already created its قيود invoice
(invoice_id=63 in production order 269048975) and only needs the
payment-link step to succeed, that re-run carries unnecessary risk:

  • The product resolver may interact with قيود on every attempt
    (find_all_products_by_sku, auto-adopt, etc.).
  • Dry-run-flag mappings can short-circuit the row before reaching
    the payment step.
  • The stale `last_failed_stage` and `pipeline_error` from previous
    attempts can confuse the failure-response builder.

This module exposes ONE narrow operation:

    POST /invoice_payments  for an existing qoyod_invoice_id

…with explicit guardrails:
  • Refuses unless the row has `qoyod_invoice_id` populated.
  • Refuses if `qoyod_invoice_payments` already carries a successful
    payment for this fingerprint (idempotency on real success only).
  • Never touches `/customers`, `/products`, `/invoices`, `/receipts`.
  • Returns a structured diagnostic block with the live قيود response.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.write_lock import (
    QoyodWriteLockedError, set_write_lock_context,
)
from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.invoice_builder import build_invoice_payment_payload
from integrations.qoyod.state_machine import transition

logger = logging.getLogger(__name__)

def _NOW() -> datetime:
    return datetime.now(timezone.utc)
_CONFIRM_PREFIX = "RETRY-PAYMENT-"


class RetryPaymentRefused(Exception):
    """Raised when the retry preconditions are not met."""
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict:
        out = {"ok": False, "code": self.code, "message": self.message}
        out.update(self.extra)
        return out


async def _apply_transition(db, row_id: str, t: dict) -> None:
    """Atomic transition writer — keeps stage_history + pipeline_stage
    in sync exactly like the worker does."""
    set_ops    = t.get("$set") or {}
    push_ops   = t.get("$push") or {}
    update: dict[str, Any] = {}
    if set_ops:
        update["$set"]  = set_ops
    if push_ops:
        update["$push"] = push_ops
    if update:
        await db.integration_inbox.update_one({"id": row_id}, update)


async def retry_payment_only(
    db, *, user_id: str, salla_order_number: str,
    confirm_token: str, actor: str,
) -> dict:
    """Surgical retry of `POST /invoice_payments` for an existing
    قيود invoice. Side-effect map:

      ─ READS ───────────────────────────────────────────────────────
        • integration_inbox row by salla_order_number
        • qoyod_settings   (payment_method_mapping + API key)
        • qoyod_invoice_payments (idempotency check)

      ─ WRITES ──────────────────────────────────────────────────────
        • integration_inbox.{pipeline_stage, last_*_stage, stage_history,
                             qoyod_responses.invoice_payment.*,
                             qoyod_payloads.invoice_payment.*,
                             pipeline_error, qoyod_invoice_payment_id}
        • qoyod_invoice_payments  (only on POST success)
        • qoyod_invoices.{pipeline_stage, status, qoyod_invoice_payment_id}

      ─ DOES NOT ────────────────────────────────────────────────────
        • Re-create customer / products / invoice / receipt
        • Touch the row's canonical_payload
        • Fall back to /receipts on failure
    """
    expected_token = f"{_CONFIRM_PREFIX}{salla_order_number}"
    if confirm_token != expected_token:
        raise RetryPaymentRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{expected_token}' to authorise this retry.",
        )

    row = await db.integration_inbox.find_one(
        {"user_id": user_id, "salla_order_number": str(salla_order_number)})
    if not row:
        # Sometimes order_number is stored only inside canonical_payload.
        row = await db.integration_inbox.find_one({
            "user_id": user_id,
            "canonical_payload.order_number": str(salla_order_number),
        })
    if not row:
        raise RetryPaymentRefused(
            "row_not_found",
            f"No integration_inbox row for order {salla_order_number}.",
        )

    qoyod_invoice_id = row.get("qoyod_invoice_id")
    if not qoyod_invoice_id:
        raise RetryPaymentRefused(
            "missing_existing_invoice_id",
            "Row has no `qoyod_invoice_id` — there is no invoice to settle.",
            row_id=row.get("id"),
            pipeline_stage=row.get("pipeline_stage"),
        )

    canonical = row.get("canonical_payload") or {}
    inv_date_raw = (row.get("business_rules_decision") or {}).get("invoice_date")
    inv_date: Optional[datetime] = None
    if inv_date_raw:
        try:
            inv_date = datetime.fromisoformat(str(inv_date_raw).replace("Z", "+00:00"))
        except ValueError:
            inv_date = None
    if inv_date is None:
        inv_date = _NOW()

    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}

    payment_payload, fingerprint = build_invoice_payment_payload(
        qoyod_invoice_id=qoyod_invoice_id,
        dto_dict=canonical,
        invoice_date=inv_date,
        settings=settings,
    )

    # Pre-flight guard 1 — payment_method mapping must resolve.
    # Iter-290h.6 — wire field is `account_id` (not `account`).
    if payment_payload["invoice_payment"].get("account_id") is None:
        return {
            "ok": False,
            "outcome": "REFUSED",
            "skip_reason": "payment_method_mapping_missing",
            "payment_post_attempted": False,
            "request_sent_to_qoyod":  False,
            "qoyod_status_code":      None,
            "qoyod_response":         None,
            "request_body_json":      payment_payload,
            "existing_qoyod_invoice_id": qoyod_invoice_id,
            "human_message": (
                "لم يتم ضبط حساب قيود لطريقة الدفع المستخدمة في الإعدادات. "
                f"طريقة الدفع من الطلب: '{canonical.get('payment_method')}'. "
                "افتح إعدادات قيود ← طرق الدفع وضبط الحساب ثم أعد المحاولة."),
        }

    # Pre-flight guard 2 — idempotency on REAL successes only.
    existing = await db.qoyod_invoice_payments.find_one({
        "user_id":          user_id,
        "salla_order_id":   fingerprint["order_id"],
        "qoyod_invoice_id": fingerprint["qoyod_invoice_id"],
        "payment_method":   fingerprint["payment_method"],
        "amount":           fingerprint["amount"],
    }, {"_id": 0, "qoyod_invoice_payment_id": 1})
    if existing and existing.get("qoyod_invoice_payment_id"):
        return {
            "ok": True,
            "outcome": "ALREADY_PAID",
            "skip_reason": "idempotency_success_record_exists",
            "payment_post_attempted": False,
            "request_sent_to_qoyod":  False,
            "qoyod_status_code":      None,
            "qoyod_response":         None,
            "qoyod_invoice_payment_id": existing["qoyod_invoice_payment_id"],
            "existing_qoyod_invoice_id": qoyod_invoice_id,
            "request_body_json":      payment_payload,
            "human_message": (
                "هذه الفاتورة لديها سداد ناجح مسجَّل مسبقاً — لا حاجة لإعادة المحاولة."),
        }

    # ── Snapshot the payload BEFORE we POST so the operator can audit ─
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {
            "qoyod_payloads.invoice_payment":           payment_payload,
            "qoyod_payloads.invoice_payment_fingerprint": fingerprint,
            "qoyod_payloads.invoice_payment_snapshot_at": _NOW(),
        }})

    # ── The ONE Qoyod call this endpoint makes ─────────────────────
    api_key = await get_api_key(db, user_id)
    if not api_key:
        return {
            "ok": False,
            "outcome": "REFUSED",
            "skip_reason": "qoyod_api_key_missing",
            "payment_post_attempted": False,
            "request_sent_to_qoyod":  False,
            "request_body_json":      payment_payload,
            "human_message": "مفتاح API قيود غير مُهيّأ في الإعدادات.",
        }
    api = QoyodAPIClient(
        api_key,
        db=db, user_id=user_id,
        write_lock_enabled=bool(settings.get("production_writes_locked", False)),
    )
    # Iter-294 — audit context for any write-block record.
    set_write_lock_context(
        order_number=str(salla_order_number),
        trace_id=row.get("trace_id"),
        callsite="retry_payment_only",
    )
    idem_key = (f"mzn-retry-payment-{qoyod_invoice_id}-"
                f"{fingerprint['amount']}-{fingerprint['payment_method']}")
    started_ms = int(_NOW().timestamp() * 1000)
    try:
        resp = await api.create_invoice_payment(payment_payload, idem=idem_key)
    except QoyodWriteLockedError as exc:
        # Iter-294 — Global Write Lock refused the POST. Surface a
        # clean structured response with the locked payload + attempt_id.
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {
                "qoyod_payloads.invoice_payment_locked_payload": payment_payload,
                "qoyod_payloads.invoice_payment_locked_at":      _NOW(),
                "pipeline_stage":                                "LOCKED_AWAITING_APPROVAL",
                "lock_reason":                                   "production_writes_locked",
                "lock_step":                                     "invoice_payment",
                "lock_attempt_id":                               exc.attempt_id,
            }})
        return {
            "ok": False,
            "outcome": "LOCKED_AWAITING_APPROVAL",
            "skip_reason": "production_writes_locked",
            "payment_post_attempted": False,
            "request_sent_to_qoyod":  False,
            "qoyod_status_code":      None,
            "qoyod_response":         None,
            "request_body_json":      payment_payload,
            "existing_qoyod_invoice_id": qoyod_invoice_id,
            "lock_attempt_id":           exc.attempt_id,
            "human_message": (
                "إنتاج قيود مقفول حالياً (production_writes_locked=True). "
                "تم حفظ payload الكامل للسداد للمراجعة، ولم يُرسَل أي "
                "طلب لـ api.qoyod.com. راجع تقرير قفل الإنتاج ثم وافق "
                "صراحةً قبل الإرسال."),
        }
    except QoyodAPIError as exc:
        err_log = exc.to_log_dict()
        err_log["request_body_json"] = payment_payload
        duration_ms = int(_NOW().timestamp() * 1000) - started_ms
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {
                "qoyod_responses.invoice_payment.error":      err_log,
                "qoyod_responses.invoice_payment.received_at": _NOW(),
                "qoyod_responses.invoice_payment.duration_ms": duration_ms,
                "pipeline_error":                             err_log,
                "last_failed_stage":                          "PAYMENT_LINK_FAILED",
            }})
        # Failure response — CARRIES the live قيود verdict, not a stale one.
        return {
            "ok": False,
            "outcome": "PAYMENT_LINK_FAILED",
            "payment_post_attempted": True,
            "request_sent_to_qoyod":  True,
            "qoyod_status_code":      err_log.get("status_code"),
            "qoyod_response":         err_log.get("qoyod_response_excerpt"),
            "skip_reason":            None,
            "request_body_json":      payment_payload,
            "existing_qoyod_invoice_id": qoyod_invoice_id,
            "duration_ms":            duration_ms,
            "human_message": (
                "قيود رفض السداد. راجع رد قيود أعلاه لمعرفة الحقل المطلوب."),
        }

    # ── Success path ───────────────────────────────────────────────
    duration_ms = int(_NOW().timestamp() * 1000) - started_ms
    qoyod_payment_id: Optional[str] = None
    if isinstance(resp, dict):
        inner = (resp.get("invoice_payment") if isinstance(
            resp.get("invoice_payment"), dict) else resp)
        if inner.get("id") is not None:
            qoyod_payment_id = str(inner["id"])

    # Ledger upsert + idempotency store.
    await db.qoyod_invoice_payments.update_one(
        {
            "user_id":          user_id,
            "salla_order_id":   fingerprint["order_id"],
            "qoyod_invoice_id": fingerprint["qoyod_invoice_id"],
            "payment_method":   fingerprint["payment_method"],
            "amount":           fingerprint["amount"],
        },
        {"$set": {
            "user_id":                   user_id,
            "trace_id":                  row.get("trace_id"),
            "salla_order_id":            fingerprint["order_id"],
            "salla_order_number":        canonical.get("order_number"),
            "qoyod_invoice_id":          fingerprint["qoyod_invoice_id"],
            "qoyod_invoice_payment_id":  qoyod_payment_id,
            "payment_method":            fingerprint["payment_method"],
            "payment_method_id":         fingerprint["payment_method_id"],
            "amount":                    fingerprint["amount"],
            "currency":                  canonical.get("currency") or "SAR",
            "dry_run":                   False,
            "source":                    "retry_payment_only",
            "updated_at":                _NOW(),
        },
         "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": _NOW()}},
        upsert=True,
    )

    # Transition: INVOICE_CREATED → INVOICE_PAYMENT_CREATED → COMPLETED
    # We DON'T re-run the state machine's "from must equal current
    # pipeline_stage" check because this endpoint is operator-driven
    # and the row may sit at PARTIAL_FAILURE. Instead we force the
    # stage forward atomically.
    now = _NOW()
    history_entry_1 = {
        "from_stage": row.get("pipeline_stage") or "PARTIAL_FAILURE",
        "to_stage":   "INVOICE_PAYMENT_CREATED",
        "at":         now, "actor": actor,
        "note":       "retry_payment_only — invoice_payment posted",
    }
    history_entry_2 = {
        "from_stage": "INVOICE_PAYMENT_CREATED",
        "to_stage":   "COMPLETED",
        "at":         now, "actor": actor,
        "note":       "retry_payment_only — completion",
    }
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {
            "$set": {
                "pipeline_stage":             "COMPLETED",
                "last_success_stage":         "COMPLETED",
                "last_failed_stage":          None,
                "qoyod_invoice_payment_id":   qoyod_payment_id,
                "pipeline_error":             None,
                "qoyod_responses.invoice_payment.body":         resp,
                "qoyod_responses.invoice_payment.received_at":  now,
                "qoyod_responses.invoice_payment.duration_ms":  duration_ms,
                "qoyod_responses.invoice_payment.qoyod_id":     qoyod_payment_id,
                "qoyod_responses.invoice_payment.error":        None,
            },
            "$push": {"stage_history": {"$each": [history_entry_1,
                                                  history_entry_2]}},
        })
    await db.qoyod_invoices.update_one(
        {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
        {"$set": {
            "qoyod_invoice_payment_id": qoyod_payment_id,
            "pipeline_stage":           "COMPLETED",
            "status":                   "sent",
            "sent_at":                  now,
            "updated_at":               now,
        }})

    return {
        "ok": True,
        "outcome": "COMPLETED",
        "payment_post_attempted":   True,
        "request_sent_to_qoyod":    True,
        "qoyod_status_code":        200,
        "qoyod_response":           resp,
        "request_body_json":        payment_payload,
        "existing_qoyod_invoice_id": qoyod_invoice_id,
        "qoyod_invoice_payment_id":  qoyod_payment_id,
        "duration_ms":               duration_ms,
        "human_message": (
            f"تم تسجيل السداد بنجاح. الفاتورة {qoyod_invoice_id} يجب أن تظهر "
            "الآن في قيود بحالة مدفوعة (الرصيد = 0)."),
    }
