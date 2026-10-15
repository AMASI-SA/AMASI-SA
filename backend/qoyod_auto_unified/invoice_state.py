"""Qoyod invoice financial state and local exception closure."""
from __future__ import annotations

from typing import Any

from integrations.qoyod.unsent_orders import _is_real

from .common import _TENANT, _money, _now


def _invoice_financials(invoice: dict[str, Any]) -> dict[str, Any]:
    total = _money(
        invoice.get("total")
        or invoice.get("total_amount")
        or invoice.get("grand_total")
    )
    paid = _money(
        invoice.get("paid_amount")
        or invoice.get("amount_paid")
        or invoice.get("total_paid")
    )
    remaining = _money(
        invoice.get("remaining")
        or invoice.get("outstanding")
        or invoice.get("balance")
    )
    status = str(invoice.get("status") or "").strip().lower()
    paid_status = status in {
        "paid", "fully_paid", "fully paid", "دفعت", "مدفوعة", "مدفوع",
    }
    partial_status = status in {
        "partial", "partially_paid", "partially paid", "مدفوعة جزئياً",
    }
    if total is not None and remaining is not None and paid is None:
        paid = round(max(0.0, total - remaining), 2)
    if total is not None and paid is not None and remaining is None:
        remaining = round(max(0.0, total - paid), 2)
    if paid_status and total is not None:
        paid = total
        remaining = 0.0
    resolved_paid = bool(
        paid_status
        or (
            total is not None
            and paid is not None
            and paid + 0.01 >= total
            and (remaining is None or remaining <= 0.01)
        )
    )
    return {
        "total": total,
        "paid_amount": paid or 0.0,
        "remaining": remaining,
        "status": "paid" if resolved_paid else (
            "partial" if partial_status or (paid or 0) > 0 else (status or "unpaid")
        ),
        "resolved_paid": resolved_paid,
    }


async def _resolve_order_exception(
    db: Any,
    *,
    order_number: str,
    invoice_id: Any,
    resolution: str,
    actor: str,
) -> None:
    now = _now()
    await db.qoyod_manual_auto_quarantines.update_many(
        {
            "user_id": _TENANT,
            "order_number": str(order_number),
            "status": {"$in": ["open", "retryable", "reopened"]},
        },
        {"$set": {
            "status": "resolved",
            "resolved_at": now,
            "resolved_by": actor,
            "resolution": resolution,
            "resolved_invoice_id": str(invoice_id),
            "last_manual_retry_error": None,
        }},
    )
    await db.qoyod_manual_send_locks.update_many(
        {
            "user_id": _TENANT,
            "order_number": str(order_number),
            "status": {"$in": [
                "failed", "partial_payment_failed", "already_present",
            ]},
        },
        {"$set": {
            "status": "reconciled",
            "finished_at": now,
            "manual_qoyod_invoice_id": str(invoice_id),
            "last_error": None,
            "reconciled_at": now,
            "reconciled_by": actor,
        }},
    )


async def _write_exact_invoice_mirror(
    db: Any,
    *,
    order_number: str,
    invoice: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    invoice_id = invoice.get("id") or invoice.get("qoyod_invoice_id")
    if not _is_real(invoice_id):
        return {"ok": False, "reason": "invoice_id_not_real"}
    financials = _invoice_financials(invoice)
    invoice_number = (
        invoice.get("invoice_number")
        or invoice.get("number")
        or invoice_id
    )
    reference = str(invoice.get("reference") or order_number).strip()
    now = _now()
    raw = {
        key: invoice.get(key)
        for key in (
            "id", "invoice_number", "number", "reference", "issue_date",
            "due_date", "total", "total_amount", "paid_amount",
            "amount_paid", "outstanding", "balance", "status", "currency",
        )
        if key in invoice
    }
    raw["reference"] = reference
    await db.qoyod_invoices.update_one(
        {"user_id": _TENANT, "qoyod_invoice_id": str(invoice_id)},
        {
            "$set": {
                "user_id": _TENANT,
                "qoyod_invoice_id": str(invoice_id),
                "invoice_number": str(invoice_number),
                "reference": reference,
                "qoyod_official_reference": reference,
                "reference_provenance": "qoyod.reference",
                "salla_order_number": str(order_number),
                "issue_date": invoice.get("issue_date"),
                "currency": invoice.get("currency") or "SAR",
                "total": financials["total"],
                "paid_amount": financials["paid_amount"],
                "remaining": financials["remaining"],
                "status": financials["status"],
                "source": source,
                "last_sync_at": now,
                "raw_response": raw,
            },
            "$setOnInsert": {
                "created_at": now,
                "salla_order_id": f"qoyod-sync:{invoice_id}",
            },
        },
        upsert=True,
    )
    return {
        "ok": True,
        "invoice_id": str(invoice_id),
        "invoice_number": str(invoice_number),
        "reference": reference,
        **financials,
    }
