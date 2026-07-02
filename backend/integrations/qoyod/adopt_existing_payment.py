"""Iter-2026-02.rev15 — Adopt an EXISTING قيود invoice-payment / receipt.

Purpose
───────
When the operator creates the receipt manually inside قيود (e.g.
`PYT2` for order 269629400 / invoice 186), Mezan must record the
receipt id AND write an idempotency record so ANY future automatic
attempt (retry_payment_only, canary, worker) returns `ALREADY_PAID`
instead of POSTing a second receipt.

Contract (strict)
─────────────────
  • ZERO Qoyod API calls. This is a book-keeping operation.
  • ONE integration_inbox `update_one` — the row identified by
    `salla_order_number` (or its trace, when passed).
  • ONE `qoyod_invoice_payments` upsert carrying the same
    `fingerprint` shape `retry_payment_only` uses for its idempotency
    guard, so subsequent retry attempts short-circuit to
    `ALREADY_PAID`.
  • ONE `qoyod_invoices` upsert marking status=sent + payment id.
  • Refuses if the row has no `qoyod_invoice_id` (nothing to adopt
    against), refuses on `confirm_token_mismatch`, refuses on
    duplicate adopt attempts (idempotent — surface `ALREADY_ADOPTED`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from integrations.qoyod.invoice_builder import (
    build_invoice_payment_payload,
)


_CONFIRM_PREFIX = "ADOPT-PAYMENT-"


def _NOW() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdoptPaymentRefused(Exception):
    def __init__(self, code: str, human: str, **extra):
        super().__init__(human)
        self.code = code
        self.extra = extra


async def adopt_existing_payment(
    db,
    *,
    user_id:                   str,
    salla_order_number:        str,
    qoyod_invoice_payment_id:  str,
    confirm_token:             str,
    qoyod_invoice_id:          Optional[str] = None,
    qoyod_customer_id:         Optional[str] = None,
    actor:                     str            = "operator",
) -> dict:
    """Record an already-created قيود receipt in Mezan.

    Returns a JSON-ready dict with:
      • `ok`, `outcome` ∈ {"ADOPTED", "ALREADY_ADOPTED"}
      • `qoyod_invoice_payment_id`
      • `qoyod_invoice_id`
      • `existing_idempotency_record`  (bool)
      • `fingerprint`  (for auditability)
      • `db_row_update_result`, `db_ledger_upsert_result`,
        `db_invoices_upsert_result`
    """
    expected_token = f"{_CONFIRM_PREFIX}{salla_order_number}"
    if confirm_token != expected_token:
        raise AdoptPaymentRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{expected_token}' to authorise "
            "this adopt.",
        )
    if not qoyod_invoice_payment_id:
        raise AdoptPaymentRefused(
            "missing_qoyod_invoice_payment_id",
            "`qoyod_invoice_payment_id` is required (e.g. 'PYT2').",
        )

    row = await db.integration_inbox.find_one(
        {"user_id": user_id,
         "salla_order_number": str(salla_order_number)})
    if not row:
        row = await db.integration_inbox.find_one(
            {"user_id": user_id,
             "canonical_payload.order_number":
                 str(salla_order_number)})
    if not row:
        raise AdoptPaymentRefused(
            "row_not_found",
            f"No integration_inbox row for order "
            f"{salla_order_number}.")

    # `qoyod_invoice_id` — accept the caller's explicit value (used
    # when the operator adopts BOTH invoice + payment in one go)
    # otherwise fall back to the row's existing binding.
    resolved_invoice_id = qoyod_invoice_id or row.get(
        "qoyod_invoice_id")
    if not resolved_invoice_id:
        raise AdoptPaymentRefused(
            "missing_qoyod_invoice_id",
            "Row has no `qoyod_invoice_id` and none was supplied — "
            "cannot adopt a payment without a target invoice.",
            row_id=row.get("id"),
            pipeline_stage=row.get("pipeline_stage"))

    resolved_customer_id = qoyod_customer_id or row.get(
        "qoyod_customer_id")

    canonical = row.get("canonical_payload") or {}
    inv_date_raw = (row.get("business_rules_decision")
                    or {}).get("invoice_date")
    inv_date: Optional[datetime] = None
    if inv_date_raw:
        try:
            inv_date = datetime.fromisoformat(
                str(inv_date_raw).replace("Z", "+00:00"))
        except ValueError:
            inv_date = None
    if inv_date is None:
        inv_date = datetime.now(timezone.utc)

    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}

    # Compute the fingerprint using the SAME builder retry_payment
    # uses — guarantees future retries idempotency guard matches.
    _payment_payload, fingerprint = build_invoice_payment_payload(
        qoyod_invoice_id=resolved_invoice_id,
        dto_dict=canonical,
        invoice_date=inv_date,
        settings=settings,
    )

    # Idempotency check — if a ledger record already exists with a
    # payment id, surface ALREADY_ADOPTED (no double-book).
    existing = await db.qoyod_invoice_payments.find_one({
        "user_id":          user_id,
        "salla_order_id":   fingerprint["order_id"],
        "qoyod_invoice_id": fingerprint["qoyod_invoice_id"],
        "payment_method":   fingerprint["payment_method"],
        "amount":           fingerprint["amount"],
    }, {"_id": 0, "qoyod_invoice_payment_id": 1, "source": 1})
    already_adopted = bool(
        existing and existing.get("qoyod_invoice_payment_id"))

    now = _NOW()

    ledger_res = await db.qoyod_invoice_payments.update_one(
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
            "qoyod_invoice_payment_id":  str(qoyod_invoice_payment_id),
            "payment_method":            fingerprint["payment_method"],
            "payment_method_id":         fingerprint["payment_method_id"],
            "amount":                    fingerprint["amount"],
            "currency":                  canonical.get("currency") or "SAR",
            "dry_run":                   False,
            "source":                    "adopt_existing_payment",
            "adopted_by":                actor,
            "updated_at":                now,
        },
         "$setOnInsert": {
             "id":         uuid.uuid4().hex,
             "created_at": now,
         }},
        upsert=True,
    )

    # Row update — set both bindings + payment id + stage.
    row_res = await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {
            "qoyod_invoice_id":         str(resolved_invoice_id),
            "qoyod_customer_id":        (str(resolved_customer_id)
                                         if resolved_customer_id
                                         else None),
            "qoyod_invoice_payment_id": str(qoyod_invoice_payment_id),
            "pipeline_stage":           "COMPLETED",
            "adopted_payment":          True,
            "adopted_payment_at":       now,
            "adopted_payment_source":
                "adopt_existing_payment",
            "outcome":                  "invoice_and_payment_adopted",
         },
         "$push": {"stage_history": {
             "from_stage": row.get("pipeline_stage")
                           or "PARTIAL_FAILURE",
             "to_stage":   "COMPLETED",
             "at":         now,
             "actor":      actor,
             "note": (f"adopt_existing_payment — "
                      f"qoyod_invoice_payment_id="
                      f"{qoyod_invoice_payment_id}"),
         }}})

    # قيود invoices projection — status sent + payment id.
    invoices_res = await db.qoyod_invoices.update_one(
        {"user_id":            user_id,
         "salla_order_number": str(salla_order_number)},
        {"$set": {
            "user_id":                  user_id,
            "salla_order_number":       str(salla_order_number),
            "salla_order_id":           canonical.get("order_id"),
            "qoyod_invoice_id":         str(resolved_invoice_id),
            "qoyod_customer_id":        (str(resolved_customer_id)
                                         if resolved_customer_id
                                         else None),
            "qoyod_invoice_payment_id": str(qoyod_invoice_payment_id),
            "status":                   "sent",
            "source":                   "adopt_existing_payment",
            "adopted_at":               now,
         }},
        upsert=True,
    )

    return {
        "ok":                        True,
        "outcome": ("ALREADY_ADOPTED" if already_adopted
                    else "ADOPTED"),
        "qoyod_invoice_payment_id":  str(qoyod_invoice_payment_id),
        "qoyod_invoice_id":          str(resolved_invoice_id),
        "qoyod_customer_id":         (str(resolved_customer_id)
                                      if resolved_customer_id
                                      else None),
        "existing_idempotency_record": already_adopted,
        "no_qoyod_api_calls":        True,
        "fingerprint":               fingerprint,
        "db_ledger_upsert_result": {
            "matched":  getattr(ledger_res, "matched_count", None),
            "modified": getattr(ledger_res, "modified_count", None),
            "upserted_id":
                getattr(ledger_res, "upserted_id", None) is not None,
        },
        "db_row_update_result": {
            "matched":  getattr(row_res, "matched_count", None),
            "modified": getattr(row_res, "modified_count", None),
        },
        "db_invoices_upsert_result": {
            "matched":  getattr(invoices_res, "matched_count", None),
            "modified": getattr(invoices_res, "modified_count", None),
            "upserted_id":
                getattr(invoices_res, "upserted_id", None) is not None,
        },
        "human_message": (
            "تم اعتماد السند الموجود مسبقاً في قيود وحفظه في ميزان. "
            "أي محاولة سداد لاحقة لهذا الطلب سترجع ALREADY_PAID."),
    }
