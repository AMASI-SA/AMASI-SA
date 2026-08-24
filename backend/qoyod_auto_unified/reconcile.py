"""Adopt exact-reference invoices and reconcile the local mirror."""
from __future__ import annotations

import logging
from typing import Any

from integrations.qoyod.payment_methods import is_cod_family
from integrations.qoyod.unsent_orders import _is_real

from .common import _ORDER_NUMBER_RE, _TENANT
from .invoice_state import (
    _invoice_financials, _resolve_order_exception, _write_exact_invoice_mirror,
)

logger = logging.getLogger(__name__)


async def _reconcile_existing_reference(
    db: Any,
    *,
    orders_user_id: str,
    order_number: str,
    actor: str,
) -> dict[str, Any]:
    """Adopt an exact Qoyod reference after the duplicate guard finds it."""
    try:
        from integrations.qoyod.credentials import get_api_key
        from integrations.qoyod_manual.client import ManualQoyodClient
        from qoyod_order_accounting_sync import sync_unified_order_accounting

        key = await get_api_key(db, _TENANT)
        if not key:
            return {"ok": False, "reason": "qoyod_credentials_missing"}
        client = ManualQoyodClient(api_key=key)
        invoice = await client.find_invoice_by_reference(str(order_number))
        if not invoice:
            return {"ok": False, "reason": "exact_reference_not_found"}
        invoice_id = invoice.get("id") or invoice.get("qoyod_invoice_id")
        if _is_real(invoice_id):
            try:
                details = await client.get_invoice(int(str(invoice_id)))
                if isinstance(details, dict) and details:
                    invoice = {**invoice, **details}
            except Exception:
                logger.exception(
                    "Qoyod exact-reference detail read failed order=%s invoice=%s",
                    order_number, invoice_id,
                )
        mirror = await _write_exact_invoice_mirror(
            db,
            order_number=str(order_number),
            invoice=invoice,
            source="auto_reference_reconciliation",
        )
        if not mirror.get("ok"):
            return mirror
        duplicate_count = 0
        duplicate_cursor = db.qoyod_invoices.find(
            {
                "user_id": _TENANT,
                "reference": str(order_number),
                "qoyod_invoice_id": {"$nin": [None, ""]},
            },
            {"_id": 0, "qoyod_invoice_id": 1},
        ).limit(3)
        async for candidate in duplicate_cursor:
            if _is_real(candidate.get("qoyod_invoice_id")):
                duplicate_count += 1
        if duplicate_count > 1:
            return {
                **mirror,
                "resolved": False,
                "duplicate": True,
                "reason": "duplicate_qoyod_reference",
                "invoice_count_for_reference": duplicate_count,
            }
        unified = await db.unified_orders.find_one(
            {
                "user_id": str(orders_user_id),
                "order_number": str(order_number),
            },
            {"_id": 0, "payment_method": 1, "payment_method_native": 1},
        ) or {}
        is_cod = is_cod_family(
            unified.get("payment_method_native") or unified.get("payment_method")
        )
        status = "unpaid" if is_cod and not mirror["resolved_paid"] else mirror["status"]
        await sync_unified_order_accounting(
            db,
            orders_user_id=str(orders_user_id),
            order_number=str(order_number),
            invoice_id=mirror["invoice_id"],
            invoice_number=mirror["invoice_number"],
            total=mirror["total"],
            paid_amount=mirror["paid_amount"],
            remaining=mirror["remaining"],
            status=status,
            source="qoyod_exact_reference_reconciliation",
            actor=actor,
        )
        if mirror["resolved_paid"] or is_cod:
            await _resolve_order_exception(
                db,
                order_number=str(order_number),
                invoice_id=mirror["invoice_id"],
                resolution=(
                    "already_sent_paid_reconciled"
                    if mirror["resolved_paid"]
                    else "already_sent_cod_invoice_reconciled"
                ),
                actor=actor,
            )
        return {
            **mirror,
            "resolved": bool(mirror["resolved_paid"] or is_cod),
            "invoice_only": bool(is_cod),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Qoyod exact-reference reconciliation failed order=%s: %s",
            order_number, exc,
        )
        return {
            "ok": False,
            "reason": "exact_reference_reconciliation_failed",
            "error": str(exc)[:300],
        }


async def _reconcile_local_mirror_after_sync(
    db: Any,
    *,
    orders_user_id: str,
    markers_user_id: str,
) -> dict[str, Any]:
    """Repair exact-reference markers, then close only unique paid rows.

    Duplicate references are genuine accounting exceptions. They are never
    auto-resolved merely because one of the duplicate invoices is paid.
    """
    from qoyod_order_accounting_sync import repair_qoyod_order_accounting

    repair = await repair_qoyod_order_accounting(
        db,
        orders_user_id=str(orders_user_id),
        markers_user_id=str(markers_user_id),
        actor="qoyod_invoice_sync_auto_reconcile",
    )
    invoices: list[dict[str, Any]] = []
    cursor = db.qoyod_invoices.find(
        {
            "user_id": str(markers_user_id),
            "qoyod_invoice_id": {"$nin": [None, ""]},
        },
        {
            "_id": 0,
            "qoyod_invoice_id": 1,
            "reference": 1,
            "salla_order_number": 1,
            "total": 1,
            "paid_amount": 1,
            "remaining": 1,
            "status": 1,
            "issue_date": 1,
        },
    ).sort([("issue_date", -1), ("qoyod_invoice_id", -1)]).limit(5000)
    async for invoice in cursor:
        invoice_id = invoice.get("qoyod_invoice_id")
        reference = str(
            invoice.get("reference")
            or invoice.get("salla_order_number")
            or ""
        ).strip()
        if not _is_real(invoice_id) or not _ORDER_NUMBER_RE.fullmatch(reference):
            continue
        invoices.append({**invoice, "_strict_reference": reference})

    reference_counts: dict[str, int] = {}
    for invoice in invoices:
        reference = invoice["_strict_reference"]
        reference_counts[reference] = reference_counts.get(reference, 0) + 1

    resolved = 0
    skipped_duplicates = 0
    for invoice in invoices:
        reference = invoice["_strict_reference"]
        if reference_counts.get(reference, 0) != 1:
            skipped_duplicates += 1
            continue
        financials = _invoice_financials(invoice)
        if not financials["resolved_paid"]:
            continue
        await _resolve_order_exception(
            db,
            order_number=reference,
            invoice_id=invoice["qoyod_invoice_id"],
            resolution="paid_invoice_found_during_qoyod_sync",
            actor="qoyod_invoice_sync_auto_reconcile",
        )
        resolved += 1
    return {
        "ok": True,
        "repair": repair,
        "resolved_exception_count": resolved,
        "duplicate_invoice_rows_not_auto_resolved": skipped_duplicates,
    }
