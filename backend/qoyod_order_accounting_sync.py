"""Local Orders V2 accounting projection for Qoyod Plan-B sends.

This module never calls Qoyod. It projects already-confirmed invoice/payment
facts into Mezan's unified order and can repair missing local markers from the
read-only `qoyod_invoices` mirror.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.unsent_orders import _is_real

logger = logging.getLogger(__name__)
_ORDER_NUMBER_RE = re.compile(r"^\d{8,12}$")


def _money(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _result_count(result: Any, name: str) -> int:
    try:
        return int(getattr(result, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _strict_order_number(invoice: dict[str, Any]) -> Optional[str]:
    for field in (
        "reference",
        "salla_order_number",
        "external_reference",
        "source_reference",
    ):
        value = str(invoice.get(field) or "").strip()
        if _ORDER_NUMBER_RE.fullmatch(value):
            return value
    return None


async def sync_unified_order_accounting(
    db: Any,
    *,
    orders_user_id: str,
    order_number: str,
    invoice_id: Any,
    invoice_number: Any = None,
    payment_id: Any = None,
    total: Any = None,
    paid_amount: Any = None,
    remaining: Any = None,
    status: Optional[str] = None,
    source: str = "manual_plan_b",
    actor: Optional[str] = None,
) -> dict[str, Any]:
    """Write confirmed Qoyod facts into the Orders V2 accounting card."""
    if not _is_real(invoice_id):
        return {
            "ok": False,
            "updated": False,
            "reason": "invoice_id_not_real",
        }

    order_number = str(order_number or "").strip()
    if not order_number:
        return {
            "ok": False,
            "updated": False,
            "reason": "order_number_missing",
        }

    invoice_id = str(invoice_id)
    invoice_number = str(invoice_number or invoice_id)
    payment_id = str(payment_id) if payment_id not in (None, "") else None
    total_value = _money(total)
    paid_value = _money(paid_amount)
    remaining_value = _money(remaining)

    if paid_value is None:
        paid_value = 0.0
    if remaining_value is None and total_value is not None:
        remaining_value = round(max(0.0, total_value - paid_value), 2)

    normalized_status = str(status or "").strip().lower()
    if not normalized_status:
        if payment_id and (remaining_value is None or remaining_value <= 0):
            normalized_status = "paid"
        elif paid_value > 0:
            normalized_status = "partial"
        else:
            normalized_status = "unpaid"

    now = datetime.now(timezone.utc)
    patch: dict[str, Any] = {
        "accounting.status": normalized_status,
        "accounting.qoyod_status": normalized_status,
        "accounting.invoice_id": invoice_id,
        "accounting.invoice_number": invoice_number,
        "accounting.total": total_value,
        "accounting.paid_amount": paid_value,
        "accounting.remaining": remaining_value,
        "accounting.source": source,
        "accounting.synced_at": now,
        "qoyod_status": normalized_status,
        "qoyod_invoice_id": invoice_id,
        "qoyod_invoice_number": invoice_number,
        "qoyod_total": total_value,
        "qoyod_paid_amount": paid_value,
        "qoyod_remaining": remaining_value,
        "qoyod_invoice_source": source,
        "qoyod_synced_at": now,
    }
    if actor:
        patch["qoyod_synced_by"] = str(actor)
    update: dict[str, Any] = {"$set": patch}
    if payment_id:
        patch["accounting.payment_id"] = payment_id
        patch["qoyod_payment_id"] = payment_id
    else:
        update["$unset"] = {
            "accounting.payment_id": "",
            "qoyod_payment_id": "",
        }

    try:
        result = await db.unified_orders.update_one(
            {
                "user_id": str(orders_user_id),
                "order_number": order_number,
            },
            update,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Qoyod accounting projection failed order=%s invoice=%s: %s",
            order_number,
            invoice_id,
            exc,
        )
        return {
            "ok": False,
            "updated": False,
            "reason": "unified_order_update_failed",
            "error": str(exc)[:300],
        }

    matched = _result_count(result, "matched_count")
    modified = _result_count(result, "modified_count")
    return {
        "ok": True,
        "updated": matched > 0,
        "matched": matched,
        "modified": modified,
        "order_number": order_number,
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "payment_id": payment_id,
        "status": normalized_status,
        "remaining": remaining_value,
    }


async def sync_unified_order_accounting_from_result(
    db: Any,
    *,
    orders_user_id: str,
    order_number: str,
    result: dict[str, Any],
    source: str = "manual_plan_b",
) -> dict[str, Any]:
    """Project one successful Plan-B result without another Qoyod call."""
    salla_total = _money(result.get("salla_total"))
    difference = _money(result.get("difference"))
    qoyod_total = _money(result.get("qoyod_total"))
    if qoyod_total is None and salla_total is not None:
        qoyod_total = round(salla_total + (difference or 0.0), 2)
    if qoyod_total is None:
        qoyod_total = _money(result.get("expected_total"))

    payment_amount = _money(result.get("payment_amount")) or 0.0
    invoice_only = bool(result.get("invoice_only"))
    remaining = (
        qoyod_total
        if invoice_only
        else (
            round(max(0.0, qoyod_total - payment_amount), 2)
            if qoyod_total is not None
            else None
        )
    )
    payment_id = result.get("payment_id")
    if invoice_only:
        status = "unpaid"
    elif payment_id and (remaining is None or remaining <= 0):
        status = "paid"
    elif payment_amount > 0:
        status = "partial"
    else:
        status = "unpaid"

    return await sync_unified_order_accounting(
        db,
        orders_user_id=orders_user_id,
        order_number=order_number,
        invoice_id=result.get("invoice_id"),
        invoice_number=result.get("invoice_number"),
        payment_id=payment_id,
        total=qoyod_total,
        paid_amount=payment_amount,
        remaining=remaining,
        status=status,
        source=source,
    )


async def repair_qoyod_order_accounting(
    db: Any,
    *,
    orders_user_id: str,
    markers_user_id: str,
    actor: str,
) -> dict[str, Any]:
    """Repair local markers/accounting from real mirrored Qoyod invoices."""
    inbox_user_ids = list(dict.fromkeys(
        value for value in (
            str(markers_user_id or "").strip(),
            str(orders_user_id or "").strip(),
        ) if value
    ))
    if not inbox_user_ids:
        raise ValueError("At least one Qoyod marker owner is required")
    inbox_owner_query: str | dict = inbox_user_ids[0]
    if len(inbox_user_ids) > 1:
        inbox_owner_query = {"$in": inbox_user_ids}
    cursor = db.qoyod_invoices.find(
        {"user_id": str(markers_user_id)},
        {
            "_id": 0,
            "qoyod_invoice_id": 1,
            "invoice_number": 1,
            "reference": 1,
            "salla_order_number": 1,
            "external_reference": 1,
            "source_reference": 1,
            "issue_date": 1,
            "total": 1,
            "paid_amount": 1,
            "remaining": 1,
            "status": 1,
        },
    ).sort([("issue_date", -1), ("qoyod_invoice_id", -1)])

    scanned = 0
    skipped_not_real = 0
    skipped_no_order_number = 0
    duplicate_references = 0
    unified_orders_updated = 0
    inbox_markers_updated = 0
    affected_orders: list[str] = []
    seen: set[str] = set()

    async for invoice in cursor:
        scanned += 1
        invoice_id = invoice.get("qoyod_invoice_id")
        if not _is_real(invoice_id):
            skipped_not_real += 1
            continue

        order_number = _strict_order_number(invoice)
        if order_number is None:
            skipped_no_order_number += 1
            continue
        if order_number in seen:
            duplicate_references += 1
            continue
        seen.add(order_number)

        sync_result = await sync_unified_order_accounting(
            db,
            orders_user_id=str(orders_user_id),
            order_number=order_number,
            invoice_id=invoice_id,
            invoice_number=invoice.get("invoice_number"),
            total=invoice.get("total"),
            paid_amount=invoice.get("paid_amount"),
            remaining=invoice.get("remaining"),
            status=invoice.get("status"),
            source="qoyod_reconciliation_repair",
            actor=actor,
        )
        if int(sync_result.get("modified") or 0) > 0:
            unified_orders_updated += 1
            if len(affected_orders) < 200:
                affected_orders.append(order_number)

        marker_patch = {
            "qoyod_invoice_id": str(invoice_id),
            "qoyod_invoice_number": str(
                invoice.get("invoice_number") or invoice_id
            ),
            "qoyod_invoice_source": "qoyod_reconciliation_repair",
            "qoyod_marker_repaired_at": datetime.now(timezone.utc),
            "qoyod_marker_repaired_by": str(actor),
        }
        try:
            marker_result = await db.integration_inbox.update_many(
                {
                    "user_id": inbox_owner_query,
                    "salla_order_number": order_number,
                    "$or": [
                        {"qoyod_invoice_id": {"$exists": False}},
                        {"qoyod_invoice_id": None},
                        {"qoyod_invoice_id": ""},
                        {
                            "qoyod_invoice_id": {
                                "$regex": r"^(DRY:|PREVIEW:)",
                                "$options": "i",
                            }
                        },
                    ],
                },
                {"$set": marker_patch},
                upsert=False,
            )
            inbox_markers_updated += _result_count(
                marker_result,
                "modified_count",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Qoyod inbox marker repair failed order=%s invoice=%s: %s",
                order_number,
                invoice_id,
                exc,
            )

    return {
        "ok": True,
        "counts": {
            "qoyod_invoices_scanned": scanned,
            "skipped_not_real": skipped_not_real,
            "skipped_no_strict_order_number": skipped_no_order_number,
            "duplicate_references": duplicate_references,
            "unified_orders_updated": unified_orders_updated,
            "inbox_markers_updated": inbox_markers_updated,
        },
        "affected_orders_sample": affected_orders,
        "actor": str(actor),
        "at": datetime.now(timezone.utc).isoformat(),
    }
