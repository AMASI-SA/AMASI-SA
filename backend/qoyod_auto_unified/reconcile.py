"""Adopt exact-reference invoices and reconcile the local mirror."""
from __future__ import annotations

import logging
from typing import Any

from integrations.qoyod.payment_methods import is_cod_family
from integrations.qoyod.unsent_orders import _is_real

from .common import _ORDER_NUMBER_RE, _TENANT, _now
from .invoice_state import (
    _invoice_financials, _resolve_order_exception, _write_exact_invoice_mirror,
)

logger = logging.getLogger(__name__)

_RECONCILIATION_REPAIR_LIMIT = 250


def _reconciliation_signature(
    invoice: dict[str, Any],
    *,
    reference: str,
    provenance: str,
    financials: dict[str, Any],
    is_cod: bool,
) -> str:
    """Return the local facts that make an invoice repair idempotent."""
    values = (
        invoice.get("qoyod_invoice_id"),
        invoice.get("invoice_number"),
        reference,
        provenance,
        financials.get("total"),
        financials.get("paid_amount"),
        financials.get("remaining"),
        financials.get("status"),
        bool(is_cod),
    )
    return "|".join("" if value is None else str(value) for value in values)


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
                    "Qoyod exact-reference detail read failed "
                    "order=%s invoice=%s",
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
            unified.get("payment_method_native")
            or unified.get("payment_method")
        )
        status = (
            "unpaid"
            if is_cod and not mirror["resolved_paid"]
            else mirror["status"]
        )
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
    repair_limit: int = _RECONCILIATION_REPAIR_LIMIT,
) -> dict[str, Any]:
    """Project and close only unique, official-reference Qoyod rows.

    A top-level local ``reference`` copied from external/source reference is
    not enough. The same strict provenance predicate used by the candidate
    universe decides whether Qoyod actually accepted the Salla order number.
    Duplicate references remain true accounting exceptions.
    """
    from integrations.qoyod.candidate_orders import official_qoyod_reference
    from qoyod_order_accounting_sync import sync_unified_order_accounting

    invoices: list[dict[str, Any]] = []
    cursor = db.qoyod_invoices.find(
        {
            "user_id": str(markers_user_id),
            "qoyod_invoice_id": {"$nin": [None, ""]},
        },
        {
            "_id": 0,
            "qoyod_invoice_id": 1,
            "invoice_number": 1,
            "reference": 1,
            "qoyod_official_reference": 1,
            "reference_provenance": 1,
            "raw_response.reference": 1,
            "source": 1,
            "salla_order_number": 1,
            "total": 1,
            "paid_amount": 1,
            "remaining": 1,
            "status": 1,
            "issue_date": 1,
            "qoyod_reconciliation_signature": 1,
        },
    ).sort([("issue_date", -1), ("qoyod_invoice_id", -1)]).limit(5000)
    async for invoice in cursor:
        invoice_id = invoice.get("qoyod_invoice_id")
        reference, provenance = official_qoyod_reference(invoice)
        reference = str(reference or "").strip()
        if (
            not _is_real(invoice_id)
            or not _ORDER_NUMBER_RE.fullmatch(reference)
        ):
            continue
        invoices.append({
            **invoice,
            "_strict_reference": reference,
            "_reference_provenance": provenance,
        })

    reference_counts: dict[str, int] = {}
    for invoice in invoices:
        reference = invoice["_strict_reference"]
        reference_counts[reference] = reference_counts.get(reference, 0) + 1

    unified_by_reference: dict[str, dict[str, Any]] = {}
    if reference_counts:
        unified_cursor = db.unified_orders.find(
            {
                "user_id": str(orders_user_id),
                "order_number": {"$in": list(reference_counts)},
            },
            {
                "_id": 0,
                "order_number": 1,
                "payment_method": 1,
                "payment_method_native": 1,
            },
        )
        async for unified in unified_cursor:
            reference = str(unified.get("order_number") or "").strip()
            if reference:
                unified_by_reference[reference] = unified

    projected = 0
    markers_updated = 0
    resolved = 0
    skipped_duplicates = 0
    skipped_missing_unified = 0
    skipped_already_reconciled = 0
    deferred_repair_count = 0
    processed_invoice_count = 0
    repair_limit = max(1, min(int(repair_limit or 1), 1000))
    for invoice in invoices:
        reference = invoice["_strict_reference"]
        if reference_counts.get(reference, 0) != 1:
            skipped_duplicates += 1
            continue
        unified = unified_by_reference.get(reference)
        if not unified:
            skipped_missing_unified += 1
            continue
        financials = _invoice_financials(invoice)
        is_cod = is_cod_family(
            unified.get("payment_method_native")
            or unified.get("payment_method")
        )
        reconciliation_signature = _reconciliation_signature(
            invoice,
            reference=reference,
            provenance=invoice["_reference_provenance"],
            financials=financials,
            is_cod=is_cod,
        )
        if invoice.get("qoyod_reconciliation_signature") == (
            reconciliation_signature
        ):
            skipped_already_reconciled += 1
            continue
        if processed_invoice_count >= repair_limit:
            deferred_repair_count += 1
            continue
        processed_invoice_count += 1
        status = (
            "unpaid"
            if is_cod and not financials["resolved_paid"]
            else financials["status"]
        )
        sync_result = await sync_unified_order_accounting(
            db,
            orders_user_id=str(orders_user_id),
            order_number=reference,
            invoice_id=invoice["qoyod_invoice_id"],
            invoice_number=(
                invoice.get("invoice_number") or invoice["qoyod_invoice_id"]
            ),
            total=financials["total"],
            paid_amount=financials["paid_amount"],
            remaining=financials["remaining"],
            status=status,
            source="qoyod_invoice_sync_reconciliation",
            actor="qoyod_invoice_sync_auto_reconcile",
        )
        if sync_result.get("updated"):
            projected += 1

        marker_result = await db.integration_inbox.update_many(
            {
                "user_id": {
                    "$in": [str(markers_user_id), str(orders_user_id)]
                },
                "salla_order_number": reference,
            },
            {"$set": {
                "qoyod_invoice_id": str(invoice["qoyod_invoice_id"]),
                "qoyod_invoice_number": str(
                    invoice.get("invoice_number")
                    or invoice["qoyod_invoice_id"]
                ),
                "qoyod_invoice_source": "qoyod_invoice_sync_reconciliation",
                "qoyod_marker_repaired_at": _now(),
                "qoyod_reference_match_provenance": invoice[
                    "_reference_provenance"
                ],
            }},
        )
        markers_updated += int(
            getattr(marker_result, "modified_count", 0) or 0
        )

        if financials["resolved_paid"] or is_cod:
            await _resolve_order_exception(
                db,
                order_number=reference,
                invoice_id=invoice["qoyod_invoice_id"],
                resolution=(
                    "paid_invoice_found_during_qoyod_sync"
                    if financials["resolved_paid"]
                    else "cod_invoice_found_during_qoyod_sync"
                ),
                actor="qoyod_invoice_sync_auto_reconcile",
            )
            resolved += 1
        await db.qoyod_invoices.update_one(
            {
                "user_id": str(markers_user_id),
                "qoyod_invoice_id": str(invoice["qoyod_invoice_id"]),
            },
            {"$set": {
                "qoyod_reconciliation_signature": reconciliation_signature,
                "qoyod_reconciled_at": _now(),
                "qoyod_reconciled_by": "qoyod_invoice_sync_auto_reconcile",
            }},
        )
    return {
        "ok": True,
        "strict_invoice_count": len(invoices),
        "repair_limit": repair_limit,
        "processed_invoice_count": processed_invoice_count,
        "skipped_already_reconciled": skipped_already_reconciled,
        "skipped_missing_unified_order": skipped_missing_unified,
        "deferred_repair_count": deferred_repair_count,
        "repair_backlog_drained": deferred_repair_count == 0,
        "unified_orders_projected": projected,
        "inbox_markers_updated": markers_updated,
        "resolved_exception_count": resolved,
        "duplicate_invoice_rows_not_auto_resolved": skipped_duplicates,
    }
