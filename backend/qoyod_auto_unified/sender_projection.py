"""Project unified_orders into the compatibility row consumed by Plan-B."""
from __future__ import annotations

import uuid
from typing import Any

from integrations.qoyod.candidate_orders import (
    PAYMENT_INELIGIBLE, PAYMENT_NEEDS_LIVE_VERIFICATION, payment_eligibility,
)
from integrations.qoyod.unsent_orders import _is_real

from .canonical import _canonical_from_unified
from .common import _SENDER_CONNECTOR, _TENANT, _now, _raw_salla, _text
from .live_source import _promote_snapshot_to_unified


async def _marker_fields(
    db: Any,
    *,
    orders_user_id: str,
    legacy_user_id: str,
    order_number: str,
    unified: dict[str, Any],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    accounting = unified.get("accounting") or {}
    accounting = accounting if isinstance(accounting, dict) else {}
    invoice_id = (
        unified.get("qoyod_invoice_id")
        or accounting.get("invoice_id")
    )
    invoice_number = (
        unified.get("qoyod_invoice_number")
        or accounting.get("invoice_number")
    )
    payment_id = unified.get("qoyod_payment_id") or accounting.get("payment_id")
    source = unified.get("qoyod_invoice_source") or accounting.get("source")
    if _is_real(invoice_id):
        fields["qoyod_invoice_id"] = str(invoice_id)
        fields["qoyod_invoice_number"] = str(invoice_number or invoice_id)
        fields["qoyod_invoice_source"] = str(source or "unified_orders")
        if "manual_plan_b" in str(source or ""):
            fields["manual_qoyod_invoice_id"] = str(invoice_id)
            fields["manual_qoyod_invoice_number"] = str(invoice_number or invoice_id)
    if _is_real(payment_id):
        fields["manual_qoyod_payment_id"] = str(payment_id)

    cursor = db.integration_inbox.find(
        {
            "user_id": {"$in": [str(legacy_user_id), str(orders_user_id)]},
            "salla_order_number": str(order_number),
        },
        {
            "_id": 0,
            "received_at": 1,
            "manual_qoyod_invoice_id": 1,
            "manual_qoyod_invoice_number": 1,
            "manual_qoyod_payment_id": 1,
            "qoyod_invoice_id": 1,
            "qoyod_invoice_number": 1,
            "qoyod_invoice_source": 1,
        },
    ).sort("received_at", -1).limit(100)
    async for row in cursor:
        for key in (
            "manual_qoyod_invoice_id",
            "manual_qoyod_invoice_number",
            "manual_qoyod_payment_id",
            "qoyod_invoice_id",
            "qoyod_invoice_number",
            "qoyod_invoice_source",
        ):
            if fields.get(key) in (None, "") and row.get(key) not in (None, ""):
                fields[key] = str(row[key])
    return fields


async def _upsert_sender_projection(
    db: Any,
    *,
    orders_user_id: str,
    legacy_user_id: str,
    order_number: str,
    unified: dict[str, Any],
) -> dict[str, Any]:
    canonical = _canonical_from_unified(unified)
    raw_salla = _raw_salla(unified)
    now = _now()
    selector = {
        "user_id": str(legacy_user_id),
        "connector_key": _SENDER_CONNECTOR,
        "salla_order_number": str(order_number),
    }
    markers = await _marker_fields(
        db,
        orders_user_id=orders_user_id,
        legacy_user_id=legacy_user_id,
        order_number=order_number,
        unified=unified,
    )
    patch: dict[str, Any] = {
        "trace_id": f"qoyod-unified-{uuid.uuid4().hex}",
        "user_id": str(legacy_user_id),
        "connector_key": _SENDER_CONNECTOR,
        "idempotency_key": f"qoyod:unified:auto:{order_number}",
        "salla_order_id": _text(unified.get("order_id")),
        "salla_order_number": str(order_number),
        "source": _SENDER_CONNECTOR,
        "received_at": now,
        "updated_at": now,
        "canonical_payload": canonical,
        "raw_payload": raw_salla,
        "pipeline_stage": "UNIFIED_AUTO_READY",
        "no_qoyod_send": False,
        "eligibility_only": False,
        "manual_send_allowed": True,
        "auto_send_allowed": True,
        "unified_sender_projection": {
            "source_collection": "unified_orders",
            "orders_user_id": str(orders_user_id),
            "projected_at": now,
            "payment_eligibility": payment_eligibility(unified),
        },
        **markers,
    }
    result = await db.integration_inbox.update_one(
        selector,
        {
            "$set": patch,
            "$setOnInsert": {
                "id": f"qoyod-unified-{order_number}",
                "created_at": now,
            },
        },
        upsert=True,
    )
    matched = int(getattr(result, "matched_count", 0) or 0)
    upserted = getattr(result, "upserted_id", None)
    if not matched and upserted is None:
        return {
            "ok": False,
            "code": "unified_sender_row_upsert_failed",
            "order_number": str(order_number),
        }
    return {
        "ok": True,
        "order_number": str(order_number),
        "row_id": f"qoyod-unified-{order_number}",
        "payment_method": unified.get("payment_method"),
        "payment_status": (
            unified.get("payment_collection_status")
            or unified.get("payment_status")
        ),
        "paid_amount": unified.get("paid_amount"),
        "remaining_amount": unified.get("remaining_amount"),
        "item_count": len(canonical.get("items") or []),
        "source_authority": "unified_orders",
    }


async def sync_authoritative_payment_to_inbox(
    db: Any,
    *,
    orders_user_id: str,
    legacy_user_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Prepare the unchanged sender from unified_orders without a legacy row."""
    order_number = str(order_number or "").strip()
    orders_user_id = str(orders_user_id or "").strip()
    legacy_user_id = str(legacy_user_id or _TENANT).strip() or _TENANT

    await _promote_snapshot_to_unified(
        db,
        orders_user_id=orders_user_id,
        order_number=order_number,
    )
    unified = await db.unified_orders.find_one(
        {"user_id": orders_user_id, "order_number": order_number},
        {"_id": 0},
    )
    if not unified:
        return {
            "ok": False,
            "code": "authoritative_order_missing_after_resync",
            "order_number": order_number,
            "orders_user_id": orders_user_id,
        }

    state = payment_eligibility(unified)
    if state == PAYMENT_INELIGIBLE:
        return {
            "ok": False,
            "code": "authoritative_payment_not_eligible",
            "order_number": order_number,
            "payment_method": unified.get("payment_method"),
            "payment_status": (
                unified.get("payment_collection_status")
                or unified.get("payment_status")
            ),
            "paid_amount": unified.get("paid_amount"),
            "remaining_amount": unified.get("remaining_amount"),
        }
    if state == PAYMENT_NEEDS_LIVE_VERIFICATION:
        return {
            "ok": False,
            "code": "authoritative_payment_needs_verification",
            "order_number": order_number,
            "payment_method": unified.get("payment_method"),
            "payment_status": (
                unified.get("payment_collection_status")
                or unified.get("payment_status")
            ),
            "paid_amount": unified.get("paid_amount"),
            "remaining_amount": unified.get("remaining_amount"),
        }

    return await _upsert_sender_projection(
        db,
        orders_user_id=orders_user_id,
        legacy_user_id=legacy_user_id,
        order_number=order_number,
        unified=unified,
    )
