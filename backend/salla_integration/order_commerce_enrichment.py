"""Salla-compliant single-order enrichment without deprecated expanded responses.

Fetches the light order, Order Details, List Order Items and List Shipments,
then stores one merged Salla snapshot in Mezan. This module never calls Qoyod.
"""
from __future__ import annotations

from typing import Any, Optional

from orders_db import upsert_order

from .service import SallaError, call_salla
from .sync import _refresh_plan_b_status_snapshot, _salla_order_to_doc


def _rows(response: Any) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("items", "shipments", "rows", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [dict(row) for row in value if isinstance(row, dict)]
    return []


async def _resolve_order(
    db: Any,
    user_id: str,
    order_number: str,
) -> tuple[str, dict[str, Any]] | None:
    response = await call_salla(
        db,
        user_id,
        "GET",
        "/orders",
        params={
            "keyword": order_number,
            "format": "light",
            "per_page": 10,
        },
    )
    rows = _rows(response)
    match: Optional[dict[str, Any]] = None
    for row in rows:
        reference = str(row.get("reference_id") or "").strip()
        internal_id = str(row.get("id") or "").strip()
        if reference == order_number or internal_id == order_number:
            match = row
            break
    if match is None and len(rows) == 1:
        match = rows[0]
    if match is None:
        return None
    internal_id = str(match.get("id") or "").strip()
    if not internal_id:
        raise RuntimeError("Salla light order is missing internal id")
    return internal_id, match


async def _fetch_collection(
    db: Any,
    user_id: str,
    path: str,
    internal_order_id: str,
    *,
    parameter_name: str = "order_id",
) -> list[dict[str, Any]]:
    response = await call_salla(
        db,
        user_id,
        "GET",
        path,
        params={parameter_name: internal_order_id},
    )
    return _rows(response)


async def enrich_single_order_commerce(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Fetch and persist one full order snapshot using supported endpoints."""
    order_number = str(order_number or "").strip()
    if not order_number:
        return {"ok": False, "found": False, "error": "missing_order_number"}

    stage = "resolve_light_order"
    try:
        resolved = await _resolve_order(db, user_id, order_number)
        if resolved is None:
            return {
                "ok": True,
                "found": False,
                "stage": stage,
                "error": "not_found_in_salla",
            }
        internal_id, light_order = resolved

        stage = "fetch_order_details"
        details_response = await call_salla(
            db,
            user_id,
            "GET",
            f"/orders/{internal_id}",
            params={"format": "light"},
        )
        details = details_response.get("data") if isinstance(details_response, dict) else None
        if not isinstance(details, dict):
            raise RuntimeError("Salla Order Details returned invalid payload")

        stage = "fetch_order_items"
        items = await _fetch_collection(
            db,
            user_id,
            "/orders/items",
            internal_id,
            parameter_name="order_id",
        )

        stage = "fetch_order_shipments"
        shipments = await _fetch_collection(
            db,
            user_id,
            "/orders/shipments",
            internal_id,
            parameter_name="order",
        )

        stage = "merge_payload"
        merged = dict(light_order)
        merged.update(details)
        merged["items"] = items
        merged["shipments"] = shipments

        actual_reference = str(
            merged.get("reference_id") or merged.get("order_number") or ""
        ).strip()
        if actual_reference and actual_reference != order_number:
            raise RuntimeError(
                "Salla order reference mismatch: "
                f"expected={order_number} actual={actual_reference}"
            )

        stage = "map_order"
        doc = _salla_order_to_doc(merged)
        if not doc.get("order_number"):
            raise RuntimeError("Mapped order is missing order number")

        stage = "upsert_order"
        result = await upsert_order(
            db,
            user_id,
            doc["order_number"],
            doc,
            source="salla_direct",
            raw=merged,
        )

        stage = "plan_b_snapshot"
        await _refresh_plan_b_status_snapshot(
            db,
            user_id,
            doc["order_number"],
            doc,
        )

        first_shipment_keys = (
            sorted(str(key) for key in shipments[0].keys())
            if shipments
            else []
        )
        return {
            "ok": True,
            "found": True,
            "order_number": doc["order_number"],
            "internal_order_id": internal_id,
            "created": bool(result.get("created")),
            "updated": not bool(result.get("created")),
            "items_count": len(items),
            "shipments_count": len(shipments),
            "first_shipment_keys": first_shipment_keys,
            "shipping_company": doc.get("shipping_company") or None,
            "payment_method": doc.get("payment_method") or None,
            "payment_status": doc.get("payment_status") or None,
            "no_qoyod_calls": True,
            "used_deprecated_expanded": False,
        }
    except SallaError as exc:
        return {
            "ok": False,
            "found": False,
            "stage": stage,
            "error": str(exc),
            "exception_type": type(exc).__name__,
            "needs_reauth": exc.needs_reauth,
            "no_qoyod_calls": True,
        }
    except Exception as exc:
        return {
            "ok": False,
            "found": False,
            "stage": stage,
            "error": str(exc),
            "exception_type": type(exc).__name__,
            "no_qoyod_calls": True,
        }
