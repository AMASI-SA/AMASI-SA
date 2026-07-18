"""Explicit Salla shipping-label issuance for one Mezan order.

The order details page stays local/read-only.  This module is called only by
an explicit owner click on "Issue label".  Salla remains authoritative for
the shipment id, printable label URL, and tracking number.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from salla_integration.service import SallaError, call_salla
from salla_integration.sync import resync_single_order


_CANCELLED = {"cancelled", "canceled", "void", "deleted"}
_PENDING = {"pending", "creating", "processing"}


class ShippingLabelError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("slug") or value.get("name") or value.get("status")
    return _text(value).lower().replace("-", "_").replace(" ", "_")


def _url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        return candidate if candidate.startswith(("https://", "http://")) else ""
    if isinstance(value, dict):
        for key in ("url", "download_url", "label_url", "original", "pdf"):
            candidate = _url(value.get(key))
            if candidate:
                return candidate
        for child in value.values():
            candidate = _url(child)
            if candidate:
                return candidate
    if isinstance(value, list):
        for child in value:
            candidate = _url(child)
            if candidate:
                return candidate
    return ""


def _tracking(row: dict[str, Any]) -> str:
    for key in ("tracking_number", "shipping_number", "waybill_number", "awb"):
        value = _text(row.get(key))
        if value and value != "0":
            return value
    return ""


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    label_url = _url(
        row.get("label_url")
        or row.get("label")
        or row.get("awb")
        or row.get("documents")
    )
    tracking_number = _tracking(row)
    shipment_status = _status(row.get("status"))
    ready = bool(
        label_url
        and tracking_number
        and shipment_status not in _CANCELLED
        and shipment_status not in _PENDING
    )
    return {
        "ready": ready,
        "shipment_id": _text(row.get("id")) or None,
        "status": shipment_status or None,
        "label_url": label_url or None,
        "tracking_number": tracking_number or None,
        "shipping_number": _text(row.get("shipping_number")) or None,
        "tracking_url": _url(
            row.get("tracking_link") or row.get("tracking_url")
        ) or None,
        "courier_name": _text(
            row.get("courier_name")
            or row.get("company")
            or row.get("shipping_company")
        ) or None,
    }


def _active_outbound(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        shipment_type = _status(row.get("type") or row.get("shipment_type"))
        if shipment_type in {"return", "return_shipment", "reverse"}:
            continue
        if _status(row.get("status")) in _CANCELLED:
            continue
        active.append(row)

    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        raw_id = _text(row.get("id"))
        try:
            numeric = int(raw_id)
        except (TypeError, ValueError):
            numeric = 0
        return numeric, _text(row.get("created_at"))

    return sorted(active, key=sort_key, reverse=True)


async def _resolve_order(
    db: Any,
    user_id: str,
    order_number: str,
) -> tuple[str, dict[str, Any]]:
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
    rows = response.get("data") if isinstance(response, dict) else None
    rows = rows if isinstance(rows, list) else []

    match = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _text(row.get("reference_id")) == order_number:
            match = row
            break
    if match is None and len(rows) == 1 and isinstance(rows[0], dict):
        match = rows[0]
    if match is None:
        raise ShippingLabelError(
            "order_not_found_in_salla",
            "لم يتم العثور على الطلب داخل سلة.",
            status_code=404,
        )

    internal_id = _text(match.get("id"))
    if not internal_id:
        raise ShippingLabelError(
            "salla_order_id_missing",
            "لم تُرجع سلة المعرّف الداخلي للطلب.",
        )

    details_response = await call_salla(
        db,
        user_id,
        "GET",
        f"/orders/{internal_id}",
    )
    details = (
        details_response.get("data")
        if isinstance(details_response, dict)
        else None
    )
    if not isinstance(details, dict):
        raise ShippingLabelError(
            "salla_order_details_invalid",
            "تعذّر قراءة تفاصيل الطلب من سلة.",
        )
    if _text(details.get("reference_id")) not in {"", order_number}:
        raise ShippingLabelError(
            "salla_order_reference_mismatch",
            "أعادت سلة طلبًا مختلفًا؛ أوقفت العملية للحماية.",
        )
    return internal_id, details


async def _shipment_rows(
    db: Any,
    user_id: str,
    internal_order_id: str,
    embedded: Any,
) -> list[dict[str, Any]]:
    response = await call_salla(
        db,
        user_id,
        "GET",
        "/shipments",
        params={"order_id": internal_order_id, "per_page": 50},
    )
    listed = response.get("data") if isinstance(response, dict) else None
    rows = (
        [dict(row) for row in listed if isinstance(row, dict)]
        if isinstance(listed, list)
        else []
    )
    async def enrich(row: dict[str, Any]) -> dict[str, Any]:
        shipment_id = _text(row.get("id"))
        if not shipment_id:
            return row
        try:
            response = await call_salla(
                db,
                user_id,
                "GET",
                f"/shipments/{shipment_id}",
            )
        except SallaError:
            return row
        details = response.get("data") if isinstance(response, dict) else None
        if not isinstance(details, dict):
            return row
        merged = dict(row)
        # These fields are authoritative even when null after cancellation.
        clearable = {
            "label", "label_url", "shipping_number", "tracking_number",
            "tracking_link", "tracking_url", "status",
        }
        for key, value in details.items():
            if key in clearable or value not in (None, "", [], {}):
                merged[key] = value
        return merged

    if not rows:
        return []
    return list(await asyncio.gather(*(enrich(row) for row in rows)))


def _location(value: Any) -> Any:
    if isinstance(value, dict):
        return (
            value.get("id")
            or value.get("code")
            or value.get("name")
            or value.get("value")
        )
    return value


def _address_payload(value: Any, *, include_type: bool) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    payload: dict[str, Any] = {}
    keys = (
        "name", "email", "phone", "address_line", "street_number", "block",
        "short_address", "building_number", "additional_number",
        "postal_code", "geo_coordinates",
    )
    if include_type and source.get("type") not in (None, ""):
        payload["type"] = source.get("type")
    for key in keys:
        if source.get(key) not in (None, "", [], {}):
            payload[key] = source.get(key)
    if "geo_coordinates" not in payload:
        latitude = source.get("latitude")
        longitude = source.get("longitude")
        if latitude is not None and longitude is not None:
            payload["geo_coordinates"] = {
                "lat": latitude,
                "lng": longitude,
            }
    for key in ("country", "city"):
        normalized = _location(source.get(key))
        if normalized in (None, ""):
            normalized = source.get(f"{key}_id")
        if normalized not in (None, ""):
            payload[key] = normalized
    if not payload.get("address_line"):
        parts = [
            source.get("street"),
            source.get("block") or source.get("district"),
            _location(source.get("city")),
            _location(source.get("country")),
        ]
        payload["address_line"] = "، ".join(
            _text(part) for part in parts if _text(part)
        )
    return payload


def _package_payload(value: Any) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    allowed = {
        "external_id", "item_id", "name", "sku", "quantity",
        "price", "weight", "options",
    }
    packages = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        package = {
            key: row.get(key)
            for key in allowed
            if row.get(key) not in (None, "", [], {})
        }
        weight = package.get("weight")
        if isinstance(weight, dict):
            weight = dict(weight)
            if "units" not in weight and weight.get("unit"):
                weight["units"] = weight.pop("unit")
            package["weight"] = weight
        if package.get("name") and package.get("quantity"):
            packages.append(package)
    return packages


def _payment_method(
    shipment: dict[str, Any],
    order: dict[str, Any],
) -> str:
    raw = shipment.get("payment_method") or order.get("payment_method")
    if isinstance(raw, dict):
        raw = raw.get("slug") or raw.get("name") or raw.get("code")
    normalized = _status(raw)
    cod = shipment.get("cash_on_delivery")
    cod_amount = cod.get("amount") if isinstance(cod, dict) else None
    if normalized in {"cod", "cash_on_delivery", "الدفع_عند_الاستلام"}:
        return "cod"
    try:
        if float(cod_amount or 0) > 0:
            return "cod"
    except (TypeError, ValueError):
        pass
    return "pre_paid"


def _create_payload(
    internal_order_id: str,
    order: dict[str, Any],
    shipment: dict[str, Any],
) -> dict[str, Any]:
    courier_id = shipment.get("courier_id")
    packages = _package_payload(shipment.get("packages"))
    ship_to = _address_payload(shipment.get("ship_to"), include_type=False)
    ship_from = _address_payload(shipment.get("ship_from"), include_type=True)

    if not courier_id:
        raise ShippingLabelError(
            "courier_missing",
            "شركة الشحن غير مرتبطة بالشحنة الحالية في سلة.",
        )
    if not packages:
        raise ShippingLabelError(
            "shipment_packages_missing",
            "لم تُرجع سلة عناصر الشحنة؛ لم يتم إصدار بوليصة ناقصة.",
        )
    required_to = (
        "name", "email", "phone", "country", "city", "address_line",
        "street_number", "block", "short_address", "building_number",
        "additional_number", "postal_code", "geo_coordinates",
    )
    missing_to = [key for key in required_to if ship_to.get(key) in (None, "")]
    if missing_to:
        raise ShippingLabelError(
            "national_address_incomplete",
            "العنوان الوطني في سلة غير مكتمل: " + "، ".join(missing_to),
        )

    payment_method = _payment_method(shipment, order)
    payload: dict[str, Any] = {
        "overwrite_exists_pending": True,
        "courier_id": courier_id,
        "order_id": int(internal_order_id)
        if internal_order_id.isdigit()
        else internal_order_id,
        "shipment_type": "shipment",
        "payment_method": payment_method,
        "ship_to": ship_to,
        "ship_from": ship_from,
        "packages": packages,
    }
    for key in (
        "description", "remarks", "external_id", "external_additional_id",
        "external_company_name", "service_types", "policy_options",
    ):
        if shipment.get(key) not in (None, "", [], {}):
            payload[key] = shipment.get(key)
    if payment_method == "cod" and isinstance(
        shipment.get("cash_on_delivery"), dict
    ):
        payload["cash_on_delivery"] = shipment["cash_on_delivery"]
    return payload


async def _poll_shipment(
    db: Any,
    user_id: str,
    shipment_id: str,
    seed: dict[str, Any],
    *,
    attempts: int = 12,
) -> dict[str, Any]:
    latest = dict(seed)
    for attempt in range(attempts):
        if _snapshot(latest)["ready"]:
            return latest
        if attempt:
            await asyncio.sleep(0.8)
        if not shipment_id:
            continue
        try:
            response = await call_salla(
                db,
                user_id,
                "GET",
                f"/shipments/{shipment_id}",
            )
        except SallaError:
            continue
        details = response.get("data") if isinstance(response, dict) else None
        if isinstance(details, dict):
            latest = details
    return latest


async def _persist_verified_snapshot(
    db: Any,
    user_id: str,
    order_number: str,
    snapshot: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    set_fields: dict[str, Any] = {
        "shipping_status": snapshot.get("status") or "pending",
        "shipment_status": snapshot.get("status") or "pending",
        "shipping_verified_at": now,
        "shipping_verified_source": "salla",
    }
    for field in (
        "shipment_id", "shipping_number", "tracking_number",
        "tracking_url", "label_url",
    ):
        value = snapshot.get(field)
        if value not in (None, ""):
            target = {
                "shipment_id": "salla_shipment_id",
                "label_url": "shipping_label_url",
            }.get(field, field)
            set_fields[target] = value

    update: dict[str, Any] = {"$set": set_fields}
    if not snapshot.get("ready"):
        update["$unset"] = {
            "shipping_label_url": "",
            "tracking_number": "",
            "tracking_url": "",
            "shipping_number": "",
        }
    await db.unified_orders.update_one(
        {"user_id": str(user_id), "order_number": str(order_number)},
        update,
    )


async def refresh_shipping_label(
    db: Any,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Verify the current Salla shipment without creating a new shipment."""
    normalized = _text(order_number)
    if not normalized:
        raise ShippingLabelError(
            "order_number_required",
            "رقم الطلب مطلوب.",
            status_code=400,
        )

    try:
        internal_id, order = await _resolve_order(
            db, user_id, normalized
        )
        rows = await _shipment_rows(
            db,
            user_id,
            internal_id,
            order.get("shipments"),
        )
    except SallaError as exc:
        if exc.status_code == 403:
            raise ShippingLabelError(
                "shipping_scope_required",
                "صلاحية قراءة الشحن غير مفعلة في ربط سلة.",
                status_code=403,
            ) from exc
        raise ShippingLabelError(
            "salla_shipping_unavailable",
            "تعذّر التحقق من البوليصة الحالية في سلة.",
            status_code=502,
        ) from exc

    active = _active_outbound(rows)
    current = active[0] if active else {}
    for row in active:
        if _snapshot(row)["ready"]:
            current = row
            break

    snapshot = _snapshot(current)
    try:
        await resync_single_order(db, user_id, normalized)
    finally:
        # Root shipping fields are a legacy compatibility layer. Explicitly
        # clear them when Salla says the current shipment has no active AWB.
        await _persist_verified_snapshot(
            db,
            user_id,
            normalized,
            snapshot,
        )

    return {
        "ok": True,
        "source": "salla",
        **snapshot,
        "message": (
            "تم التحقق من سلة والبوليصة الحالية جاهزة."
            if snapshot["ready"]
            else "لا توجد بوليصة فعّالة حاليًا في سلة؛ أوقفت الطباعة ومسحت الرقم القديم."
        ),
    }


async def issue_shipping_label(
    db: Any,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    normalized = _text(order_number)
    if not normalized:
        raise ShippingLabelError(
            "order_number_required",
            "رقم الطلب مطلوب.",
            status_code=400,
        )

    try:
        internal_id, order = await _resolve_order(
            db, user_id, normalized
        )
        rows = await _shipment_rows(
            db,
            user_id,
            internal_id,
            order.get("shipments"),
        )
    except SallaError as exc:
        if exc.status_code == 403:
            raise ShippingLabelError(
                "shipping_scope_required",
                "صلاحية shipping.read_write غير مفعلة؛ أعد ربط سلة ثم جرّب.",
                status_code=403,
            ) from exc
        raise ShippingLabelError(
            "salla_shipping_unavailable",
            "تعذّر الاتصال بسلة لإصدار البوليصة.",
            status_code=502,
        ) from exc

    active = _active_outbound(rows)
    for row in active:
        snapshot = _snapshot(row)
        if snapshot["ready"]:
            await resync_single_order(db, user_id, normalized)
            return {"ok": True, "source": "salla", **snapshot}

    if not active:
        raise ShippingLabelError(
            "pending_shipment_missing",
            "لا توجد شحنة صادرة حالية في سلة لإصدار بوليصتها.",
        )

    source = active[0]
    if _status(source.get("status")) == "creating":
        polled = await _poll_shipment(
            db,
            user_id,
            _text(source.get("id")),
            source,
            attempts=8,
        )
        snapshot = _snapshot(polled)
        if snapshot["ready"]:
            await resync_single_order(db, user_id, normalized)
            return {"ok": True, "source": "salla", **snapshot}
        return {
            "ok": True,
            "source": "salla",
            **snapshot,
            "message": "سلة ما زالت تُصدر البوليصة؛ لم تُفتح الطباعة بعد.",
        }

    payload = _create_payload(internal_id, order, source)
    try:
        response = await call_salla(
            db,
            user_id,
            "POST",
            "/shipments",
            json=payload,
        )
    except SallaError as exc:
        if exc.status_code == 403:
            raise ShippingLabelError(
                "shipping_scope_required",
                "صلاحية shipping.read_write غير مفعلة؛ أعد ربط سلة ثم جرّب.",
                status_code=403,
            ) from exc
        raise ShippingLabelError(
            "salla_label_creation_failed",
            "رفضت سلة إصدار البوليصة؛ لم يتم تسجيلها كمطبوعة في ميزان.",
            status_code=502,
        ) from exc

    created = response.get("data") if isinstance(response, dict) else None
    if not isinstance(created, dict):
        raise ShippingLabelError(
            "salla_label_response_invalid",
            "أعادت سلة استجابة إصدار غير مكتملة.",
            status_code=502,
        )

    shipment_id = _text(created.get("id") or source.get("id"))
    latest = await _poll_shipment(
        db,
        user_id,
        shipment_id,
        created,
    )
    snapshot = _snapshot(latest)
    await resync_single_order(db, user_id, normalized)

    return {
        "ok": True,
        "source": "salla",
        **snapshot,
        "message": (
            "تم إصدار البوليصة من سلة وأصبحت جاهزة للطباعة."
            if snapshot["ready"]
            else "قبلت سلة الإصدار وما زالت تُنشئ البوليصة؛ لم تُفتح الطباعة بعد."
        ),
    }
