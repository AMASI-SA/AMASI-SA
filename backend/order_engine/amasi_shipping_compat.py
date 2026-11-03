"""Amasi-compatible, order-first Salla shipment retrieval.

The historical Amasi APK exposed order lookup/status, a fetchshippings action,
and a native print screen, but its compiled Dart call graph cannot be recovered
with certainty. This adapter reproduces the observed Salla workflow: complete
the order once on explicit issue, then read its original carrier AWB.

No POST /shipments; no substitute PDF for an external courier.
Access and preparation permissions remain at the fulfillment route boundary.
"""
from __future__ import annotations

import asyncio
from typing import Any

from salla_integration.service import SallaError, call_salla
from . import shipping_label_service as legacy

_READ_SECONDS = 8.0
_READ_ATTEMPTS_AFTER_STATUS = 4
_DONE = {"shipped", "delivering", "delivered"}
_CANCELLED = {"cancelled", "canceled", "void", "deleted"}


def _rows(response: Any) -> list[dict[str, Any]]:
    data = response.get("data", response) if isinstance(response, dict) else response
    if isinstance(data, dict):
        data = data.get("shipments", data.get("data", data))
    if isinstance(data, dict):
        data = [data] if data.get("id") else []
    return [dict(row) for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _current(rows: list[dict[str, Any]], internal_id: str) -> dict[str, Any]:
    verified = []
    for row in rows:
        nested = row.get("order")
        source_id = row.get("order_id") or (nested.get("id") if isinstance(nested, dict) else None)
        if source_id not in (None, "") and legacy._text(source_id) != internal_id:
            continue
        if legacy._status(row.get("type") or row.get("shipment_type")) in {"return", "return_shipment", "reverse"}:
            continue
        if legacy._status(row.get("status")) in _CANCELLED:
            continue
        if not legacy._text(row.get("id")):
            continue
        verified.append(row)
    current = legacy._active_outbound(verified)
    return current[0] if current else {}


def _printable(row: dict[str, Any]) -> dict[str, Any]:
    snap = legacy._snapshot(row)
    # A carrier-supplied PDF is sufficient even if "created" or no tracking
    # number accompanies it. No synthetic PDF is created for external couriers.
    snap["ready"] = bool(snap["shipment_id"] and snap["label_url"]
                         and snap["status"] not in _CANCELLED)
    return snap


async def _get(db: Any, owner: str, path: str, *, params: dict | None = None):
    try:
        return await asyncio.wait_for(
            call_salla(db, owner, "GET", path, params=params),
            timeout=_READ_SECONDS,
        )
    except (SallaError, TimeoutError, ConnectionError):
        return None


async def _order(db: Any, owner: str, internal_id: str, number: str) -> dict[str, Any] | None:
    response = await _get(db, owner, f"/orders/{internal_id}")
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        return None
    if (legacy._text(data.get("id")) != internal_id or
            legacy._text(data.get("reference_id")) not in {"", number}):
        raise legacy.ShippingLabelError(
            "salla_order_reference_mismatch", "أعادت سلة طلبًا مختلفًا؛ أوقفت الطباعة."
        )
    return data


async def _resolve(db: Any, owner: str, number: str) -> tuple[str, dict[str, Any]]:
    """Use Mezan's already-known Salla ID before a keyword order search."""
    if db is not None:
        collection = getattr(db, "unified_orders", None)
        if collection is not None:
            record = await collection.find_one(
                {"user_id": str(owner), "order_number": number},
                {"_id": 0, "order_id": 1},
            )
            internal_id = legacy._text((record or {}).get("order_id"))
            if internal_id:
                fresh = await _order(db, owner, internal_id, number)
                if fresh is not None:
                    return internal_id, fresh
    return await legacy._resolve_order(db, owner, number)


def _merge_rows(current: dict, incoming: list[dict[str, Any]], internal_id: str) -> dict:
    # A newer provider shipment wins. Do not erase its official label by
    # overwriting a populated field with a null from another Salla endpoint.
    candidate = _current(incoming, internal_id)
    if not candidate:
        return current
    if not current:
        return candidate
    old_id, new_id = legacy._text(current.get("id")), legacy._text(candidate.get("id"))
    if old_id == new_id:
        merged = dict(current)
        for key, value in candidate.items():
            if key == "status" or value not in (None, "", [], {}):
                merged[key] = value
        return merged
    try:
        return candidate if int(new_id) >= int(old_id) else current
    except ValueError:
        return candidate


async def _shipment(db: Any, owner: str, internal_id: str, order: dict[str, Any]) -> dict:
    embedded = _rows(order.get("shipments", []))
    selected = _current(embedded, internal_id)
    if selected and _printable(selected)["ready"]:
        return selected

    # Order-scope shipments first, as in the earlier Amasi-compatible path.
    # The standalone API is fallback only: a 403/504 must not veto a PDF
    # obtained from the current order's shipment.
    for path, params in (
        (f"/orders/{internal_id}/shipments", {"per_page": 50}),
        ("/shipments", {"order_id": internal_id, "per_page": 50}),
    ):
        response = await _get(db, owner, path, params=params)
        if response is not None:
            selected = _merge_rows(selected, _rows(response), internal_id)
        if selected and _printable(selected)["ready"]:
            return selected

    shipment_id = legacy._text(selected.get("id")) if selected else ""
    if shipment_id:
        for path in (f"/shipments/{shipment_id}", f"/shipments/{shipment_id}/tracking"):
            response = await _get(db, owner, path)
            if response is None:
                continue
            data = response.get("data") if isinstance(response, dict) else None
            if isinstance(data, dict) and isinstance(data.get("shipment"), dict):
                data = {**data, **data["shipment"]}
            if isinstance(data, dict):
                if data.get("id") not in (None, "") and legacy._text(data["id"]) != shipment_id:
                    continue
                selected = _merge_rows(selected, [{**data, "id": shipment_id}], internal_id)
            if selected and _printable(selected)["ready"]:
                break
    return selected or {}


def _completed(order: dict[str, Any]) -> bool:
    if legacy._order_is_completed(order):
        return True
    status = order.get("status")
    slug = (status.get("original") or status) if isinstance(status, dict) else status
    return legacy._status(slug) in _DONE


async def carrier_label(db: Any, owner: str, order_number: str, *, issue: bool) -> dict[str, Any]:
    """One Salla order-status POST when needed; read the original carrier AWB.

    A rejected or ambiguous status acknowledgement is reconciled by GET,
    never by blindly posting again. No shipment-create API is used.
    """
    number = legacy._text(order_number)
    if not number:
        raise legacy.ShippingLabelError("order_number_required", "رقم الطلب مطلوب.", status_code=400)
    internal_id, order = await _resolve(db, owner, number)
    # A cancelled/refunded order must never be moved back to completed.
    original = (order.get("status") or {}).get("original") if isinstance(order.get("status"), dict) else None
    if (legacy._status(order.get("status")) in _CANCELLED
            or legacy._status(original) in _CANCELLED):
        raise legacy.ShippingLabelError(
            "order_cancelled", "الطلب ملغى في سلة؛ لم تتغير حالته ولم تُطبع شحنته.",
        )
    changed = False
    status_error = None
    if issue and not _completed(order):
        try:
            await call_salla(db, owner, "POST", f"/orders/{internal_id}/status",
                             json={"slug": "completed"}, single_post_attempt=True)
            changed = True
        except (SallaError, TimeoutError, ConnectionError) as exc:
            status_error = exc
        latest = await _order(db, owner, internal_id, number)
        if latest is not None:
            order = latest

    attempts = _READ_ATTEMPTS_AFTER_STATUS if issue else 1
    result = {}
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(0.55)
            latest = await _order(db, owner, internal_id, number)
            if latest is not None:
                order = latest
        if not _completed(order):
            continue
        result = await _shipment(db, owner, internal_id, order)
        if result and (legacy._is_store_courier(result) or _printable(result)["ready"]):
            break
        if not result:
            carrier = legacy.extract_shipping({
                "shipping": order.get("shipping"),
                "shipping_company": order.get("shipping_company"),
                "shipping_company_code": order.get("shipping_company_code"),
            }) or {}
            if legacy._is_store_courier({
                "courier_name": carrier.get("company_name"),
                "meta": {"app_id": carrier.get("company_code")},
            }):
                break

    if not _completed(order):
        raise legacy.ShippingLabelError(
            "order_status_not_completed",
            "لم تؤكد سلة حالة «تم التنفيذ»؛ لم تتم الطباعة أو إنشاء بوليصة بديلة.",
            status_code=502 if status_error else 409,
        )

    if not result:
        carrier = legacy.extract_shipping({
            "shipping": order.get("shipping"),
            "shipping_company": order.get("shipping_company"),
            "shipping_company_code": order.get("shipping_company_code"),
        }) or {}
        merchant_courier = legacy._is_store_courier({
            "courier_name": carrier.get("company_name"),
            "meta": {"app_id": carrier.get("company_code")},
        })
        if merchant_courier:
            store = await legacy._store_identity(db, owner)
            print_data = legacy._store_courier_print_data(number, order, {}, store)
            print_data["courier_name"] = carrier.get("company_name") or "مندوب المتجر"
            return {"ok": True, "source": "mezan", "ready": True,
                    "label_type": "store_courier", "order_status_completed": True,
                    "order_status_changed": changed, "shipment_id": None,
                    "courier_name": print_data["courier_name"], "label_url": None,
                    "tracking_number": None, "shipping_number": None,
                    "print_data": print_data, "status": "store_courier",
                    "message": "تم تجهيز بوليصة مندوب المتجر."}

    if result and legacy._is_store_courier(result):
        store = await legacy._store_identity(db, owner)
        print_data = legacy._store_courier_print_data(number, order, result, store)
        return {"ok": True, "source": "mezan", "ready": True,
                "label_type": "store_courier", "order_status_completed": True,
                "order_status_changed": changed, "shipment_id": legacy._text(result.get("id")),
                "courier_name": print_data["courier_name"], "label_url": None,
                "tracking_number": None, "shipping_number": None,
                "print_data": print_data, "status": "store_courier",
                "message": "تم تجهيز بوليصة مندوب المتجر."}

    snap = _printable(result)
    return {"ok": True, "source": "salla", "order_status_completed": True,
            "order_status_changed": changed, **snap,
            "message": (
                "بوليصة شركة الشحن الرسمية جاهزة للطباعة." if snap["ready"]
                else "الشحنة موجودة في سلة، لكن ملف البوليصة لم يصل بعد." if snap["shipment_id"]
                else "لم تُرجع سلة شحنة صادرة بعد؛ لم تتم طباعة بوليصة غير موجودة."
            )}
