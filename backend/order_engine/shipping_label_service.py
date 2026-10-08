"""Explicit Salla shipping-label issuance for one Mezan order.

The order details page stays local/read-only.  This module is called only by
an explicit owner click on "Issue label".  Salla remains authoritative for
the shipment id, printable label URL, and tracking number.
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing

from salla_integration.service import SallaError, call_salla
from salla_integration.sync import resync_single_order
from operational_atomic import operational_owner
from salla_shipping import CURRENT_SHIPPING, extract_shipping, provider_time, same_carrier

from .recipient_enrichment import recipient_is_independent, resolve_salla_recipient


_CANCELLED = {"cancelled", "canceled", "void", "deleted"}
_PENDING = {"pending", "creating", "processing"}
_COMPLETED_STATUS_NAME = "تم التنفيذ"


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


def _order_is_completed(order: dict[str, Any]) -> bool:
    """Accept Salla's original ``completed`` status or its Arabic custom child."""
    status = order.get("status")
    if _status(status) == "completed":
        return True
    if not isinstance(status, dict):
        return _text(status) == _COMPLETED_STATUS_NAME

    candidates = [status]
    for key in ("original", "customized", "parent"):
        child = status.get(key)
        if isinstance(child, dict):
            candidates.append(child)
    for candidate in candidates:
        if _status(candidate) == "completed":
            return True
        if _text(candidate.get("name")) == _COMPLETED_STATUS_NAME:
            return True
    return False


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _completed_custom_status_id(response: Any) -> Any:
    """Resolve the store's custom ``تم التنفيذ`` id without hard-coding it."""
    for row in _walk_dicts(response):
        if _text(row.get("name")) != _COMPLETED_STATUS_NAME:
            continue
        status_id = row.get("id") or row.get("status_id")
        if status_id not in (None, ""):
            return int(status_id) if _text(status_id).isdigit() else status_id
    return None


async def _ensure_order_completed(
    db: Any,
    user_id: str,
    internal_order_id: str,
    order: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Move the Salla order to completed and verify it before shipment work."""
    if _order_is_completed(order):
        return order, False

    custom_status_id = None
    try:
        statuses = await call_salla(db, user_id, "GET", "/orders/statuses")
        custom_status_id = _completed_custom_status_id(statuses)
    except SallaError:
        # Status discovery is optional; Salla also accepts the original slug.
        pass

    payload = (
        {"status_id": custom_status_id}
        if custom_status_id is not None
        else {"slug": "completed"}
    )
    try:
        await call_salla(
            db,
            user_id,
            "POST",
            f"/orders/{internal_order_id}/status",
            json=payload,
        )
    except SallaError as exc:
        # Some stores expose the original status with the same Arabic name.
        # If its id is not accepted as a custom sub-status, use the documented
        # original slug instead.
        if custom_status_id is not None and exc.status_code in {400, 404, 422}:
            try:
                await call_salla(
                    db,
                    user_id,
                    "POST",
                    f"/orders/{internal_order_id}/status",
                    json={"slug": "completed"},
                )
            except SallaError as fallback_exc:
                exc = fallback_exc
            else:
                exc = None
        if exc is not None:
            if exc.status_code == 403:
                raise ShippingLabelError(
                    "orders_scope_required",
                    "صلاحية orders.read_write غير مفعلة؛ لم نحاول إصدار البوليصة.",
                    status_code=403,
                ) from exc
            if exc.status_code in {400, 409, 422}:
                raise ShippingLabelError(
                    "order_status_transition_rejected",
                    "رفضت سلة تغيير حالة الطلب إلى تم التنفيذ؛ لم نحاول إصدار البوليصة.",
                    status_code=409,
                ) from exc
            raise ShippingLabelError(
                "order_status_update_failed",
                "تعذّر تغيير حالة الطلب في سلة إلى تم التنفيذ؛ لم نحاول إصدار البوليصة.",
                status_code=502,
            ) from exc

    latest = order
    try:
        for attempt in range(6):
            if attempt:
                await asyncio.sleep(0.5)
            response = await call_salla(
                db,
                user_id,
                "GET",
                f"/orders/{internal_order_id}",
            )
            candidate = response.get("data") if isinstance(response, dict) else None
            if isinstance(candidate, dict):
                latest = candidate
            if _order_is_completed(latest):
                return latest, True
    except SallaError as exc:
        raise ShippingLabelError(
            "order_status_verification_failed",
            "تم إرسال تغيير الحالة، لكن تعذّر تأكيد «تم التنفيذ» من سلة؛ لم نحاول إصدار البوليصة.",
            status_code=502,
        ) from exc

    raise ShippingLabelError(
        "order_status_not_completed",
        "لم تؤكد سلة أن حالة الطلب أصبحت «تم التنفيذ»؛ لم نحاول إصدار البوليصة.",
        status_code=409,
    )


def _url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        return candidate if candidate.startswith(("https://", "http://")) else ""
    if isinstance(value, dict):
        for key in (
            "url", "download_url", "label_url", "original", "pdf",
            "pdf_label", "pdf_url",
        ):
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
        or row.get("pdf_label")
        or row.get("pdf_url")
        or row.get("awb")
        or row.get("documents")
    )
    tracking_number = _tracking(row)
    # Share the structured carrier parser without deriving identity from an
    # order envelope or from the printable label's readiness.
    carrier = extract_shipping({"shipping": row}) or {}
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
        "courier_name": carrier.get("company_name"),
        "courier_code": carrier.get("company_code"),
        "shipment_updated_at": provider_time(row.get("updated_at")) or provider_time(row.get("created_at")),
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


def _is_store_courier(row: dict[str, Any]) -> bool:
    """Salla's merchant courier has app_id=0 and no external AWB service."""
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    app_id = meta.get("app_id")
    courier_name = _text(row.get("courier_name") or row.get("company"))
    return app_id in {0, "0"} or "مندوب" in courier_name


def _money(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        amount = value.get("amount")
        currency = _text(value.get("currency")) or "SAR"
    else:
        amount = value
        currency = "SAR"
    return {"amount": amount, "currency": currency}


def _qr_data_uri(value: str) -> str:
    qr = QrCodeWidget(value)
    x1, y1, x2, y2 = qr.getBounds()
    source_width = x2 - x1
    source_height = y2 - y1
    size = 180
    scale = min(size / source_width, size / source_height)
    drawing = Drawing(
        size,
        size,
        transform=[scale, 0, 0, scale, -x1 * scale, -y1 * scale],
    )
    drawing.add(qr)
    svg = renderSVG.drawToString(drawing).encode("utf-8")
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")


def _order_date(order: dict[str, Any]) -> str:
    value = order.get("date") or order.get("created_at")
    if isinstance(value, dict):
        value = value.get("date") or value.get("value")
    return _text(value).split(" ", 1)[0]


def _cod_balance_when_salla_omits_remaining(
    order: dict[str, Any],
    remaining_action: dict[str, Any],
) -> dict[str, Any]:
    """Derive only a provable COD balance; Salla can return null for unpaid COD."""
    payment = order.get("payment")
    payment = payment if isinstance(payment, dict) else {}
    amounts = order.get("amounts")
    amounts = amounts if isinstance(amounts, dict) else {}
    total = amounts.get("total") or order.get("total")
    currency = _text(_money(total).get("currency")).upper()

    def fail() -> None:
        raise ShippingLabelError(
            "cod_balance_unavailable",
            "تعذّر التحقق من المبلغ المتبقي للدفع عند الاستلام؛ لم نطبع بوليصة بمبلغ غير مؤكد.",
        )

    def amount(value: Any) -> Decimal:
        raw = value.get("amount") if isinstance(value, dict) else value
        if isinstance(value, dict):
            source_currency = _text(value.get("currency")).upper()
            if source_currency and source_currency != currency:
                fail()
        if isinstance(raw, bool) or raw in (None, ""):
            fail()
        try:
            result = Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            fail()
        if not result.is_finite() or result < 0:
            fail()
        return result

    if total is None:
        fail()
    order_total = amount(total)
    if not currency:
        fail()
    paid = next(
        (value for value in (
            order.get("paid_amount"),
            payment.get("paid_amount"),
            remaining_action.get("paid_amount"),
        ) if value is not None),
        None,
    )
    collection_status = _status(
        order.get("payment_collection_status") or payment.get("collection_status")
    )
    payment_status = _status(order.get("payment_status") or payment.get("status"))

    if payment_status in {"refunded", "refund", "cancelled", "canceled"}:
        fail()

    if paid is None:
        if collection_status == "unpaid" or payment_status == "unpaid":
            paid_amount = Decimal("0.00")
        elif collection_status == "paid" or payment_status in {"paid", "completed"}:
            paid_amount = order_total
        else:
            fail()
    else:
        paid_amount = amount(paid)
    if paid_amount > order_total:
        fail()
    if (
        collection_status == "paid" or payment_status in {"paid", "completed"}
    ) and paid_amount != order_total:
        fail()
    if (collection_status == "partial" or payment_status == "partial") and (
        paid_amount <= 0 or paid_amount >= order_total
    ):
        fail()
    if (collection_status == "unpaid" or payment_status == "unpaid") and paid_amount > 0:
        fail()
    return {"amount": float(order_total - paid_amount), "currency": currency}


def _store_courier_print_data(
    order_number: str,
    order: dict[str, Any],
    shipment: dict[str, Any],
    store: dict[str, Any],
) -> dict[str, Any]:
    ship_to = shipment.get("ship_to")
    ship_to = ship_to if isinstance(ship_to, dict) else {}
    customer = order.get("customer")
    customer = customer if isinstance(customer, dict) else {}
    shipping = order.get("shipping")
    shipping = shipping if isinstance(shipping, dict) else {}
    packages = shipment.get("packages")
    packages = packages if isinstance(packages, list) else []

    recipient = resolve_salla_recipient(order, shipment=shipment)
    independent_recipient = recipient_is_independent(order, recipient)
    recipient_address = (
        recipient.get("address")
        if isinstance(recipient, dict) and isinstance(recipient.get("address"), dict)
        else {}
    )

    if recipient:
        delivery_name = _text(recipient.get("name"))
        delivery_phone = _text(recipient.get("mobile") or recipient.get("phone"))
        address_source = recipient_address
    else:
        delivery_name = (
            _text(ship_to.get("name"))
            or _text(customer.get("full_name"))
            or _text(customer.get("name"))
        )
        delivery_phone = (
            _text(ship_to.get("phone"))
            or _text(customer.get("mobile"))
            or _text(customer.get("phone"))
        )
        shipping_address = shipping.get("address")
        shipping_address = (
            shipping_address if isinstance(shipping_address, dict) else {}
        )
        customer_shipping = customer.get("shipping_address")
        customer_shipping = (
            customer_shipping if isinstance(customer_shipping, dict) else {}
        )
        address_source = ship_to or shipping_address or customer_shipping

    address_keys = (
        "country",
        "country_code",
        "city",
        "district",
        "neighborhood",
        "block",
        "street",
        "street_name",
        "street_number",
        "short_address",
        "national_address",
        "building_number",
        "additional_number",
        "postal_code",
        "address_line",
        "address_line1",
        "address_line_two",
        "formatted",
        "latitude",
        "longitude",
        "map_url",
    )
    address = {
        key: address_source.get(key)
        for key in address_keys
        if address_source.get(key) not in (None, "", [], {})
    }

    amounts = order.get("amounts")
    amounts = amounts if isinstance(amounts, dict) else {}
    total = shipment.get("total") or amounts.get("total") or order.get("total")
    payment_actions = order.get("payment_actions")
    payment_actions = payment_actions if isinstance(payment_actions, dict) else {}
    remaining_action = payment_actions.get("remaining_action")
    remaining_action = (
        remaining_action if isinstance(remaining_action, dict) else {}
    )
    remaining = remaining_action.get("remaining_amount")
    if remaining is None:
        remaining = (
            _cod_balance_when_salla_omits_remaining(order, remaining_action)
            if _payment_method(shipment, order) == "cod"
            else {"amount": 0, "currency": _money(total).get("currency") or "SAR"}
        )
    ship_from = shipment.get("ship_from")
    ship_from = ship_from if isinstance(ship_from, dict) else {}
    buyer_name = _text(customer.get("full_name")) or _text(customer.get("name"))
    buyer_phone = _text(customer.get("mobile")) or _text(customer.get("phone"))

    return {
        "order_number": order_number,
        "barcode_value": order_number,
        "qr_code": _qr_data_uri(order_number),
        "order_date": _order_date(order),
        "courier_name": _text(shipment.get("courier_name")) or "مندوب المتجر",
        "store_name": _text(store.get("name"))
        or _text(ship_from.get("name"))
        or "المتجر",
        "store_logo": _url(store.get("avatar") or store.get("logo")) or None,
        "store_phone": _text(ship_from.get("phone")) or None,
        # Backward-compatible keys now carry the operational delivery recipient.
        "customer_name": delivery_name or None,
        "customer_phone": delivery_phone or None,
        "recipient_name": delivery_name or None,
        "recipient_phone": delivery_phone or None,
        "recipient_independent": bool(independent_recipient),
        "buyer_name": buyer_name or None,
        "buyer_phone": buyer_phone or None,
        "address": address,
        "total": _money(total),
        "remaining_amount": _money(remaining),
        # Kept in the response for compatibility; mobile print HTML no longer
        # renders product names on the store-courier label.
        "items": [
            {
                "name": _text(row.get("name")),
                "quantity": row.get("quantity") or 1,
                "sku": _text(row.get("sku")) or None,
            }
            for row in packages
            if isinstance(row, dict) and _text(row.get("name"))
        ],
    }


async def _store_identity(db: Any, user_id: str) -> dict[str, Any]:
    try:
        response = await call_salla(db, user_id, "GET", "/store/info")
    except SallaError:
        return {}
    data = response.get("data") if isinstance(response, dict) else None
    return data if isinstance(data, dict) else {}


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
    embedded_rows = (
        [dict(row) for row in embedded if isinstance(row, dict)]
        if isinstance(embedded, list)
        else [dict(embedded)] if isinstance(embedded, dict) else []
    )
    response: dict[str, Any] | None = None
    modern_error: SallaError | None = None
    if not any(_snapshot(row)["ready"] for row in embedded_rows):
        try:
            response = await call_salla(
                db,
                user_id,
                "GET",
                "/shipments",
                params={"order_id": internal_order_id, "per_page": 50},
            )
        except SallaError as exc:
            if exc.status_code not in {401, 403}:
                raise
            modern_error = exc
            try:
                # Salla's legacy order-shipment route is available to apps
                # that can read and update orders even when the standalone
                # shipping scope was not granted.  Existing Amasi shipping
                # apps use this route after moving the order to completed.
                response = await call_salla(
                    db,
                    user_id,
                    "GET",
                    f"/orders/{internal_order_id}/shipments",
                    params={"per_page": 50},
                )
            except SallaError as legacy_exc:
                if legacy_exc.status_code not in {401, 403}:
                    raise

    listed = response.get("data") if isinstance(response, dict) else None
    if isinstance(listed, dict):
        nested = listed.get("shipments")
        if isinstance(nested, list):
            listed = nested
        elif isinstance(nested, dict):
            listed = [nested]
        elif listed.get("id"):
            listed = [listed]
        else:
            listed = []
    rows = (
        [dict(row) for row in listed if isinstance(row, dict)]
        if isinstance(listed, list)
        else embedded_rows
    )
    # The order-details endpoint is the original permission-compatible
    # source used by Amasi. If the standalone Shipments API is not granted,
    # keep using the shipment embedded in order.created/order details instead
    # of converting a read fallback into a shipping.read_write error.
    if response is None and modern_error is not None and not rows:
        return embedded_rows

    async def enrich(row: dict[str, Any]) -> dict[str, Any]:
        shipment_id = _text(row.get("id"))
        if not shipment_id:
            return row
        merged = dict(row)
        clearable = {
            "label", "label_url", "pdf_label", "pdf_url",
            "shipping_number", "tracking_number",
            "tracking_link", "tracking_url", "status",
        }
        try:
            response = await call_salla(
                db,
                user_id,
                "GET",
                f"/shipments/{shipment_id}",
            )
        except SallaError:
            response = None
        details = response.get("data") if isinstance(response, dict) else None
        if isinstance(details, dict):
            # These fields are authoritative even when null after cancellation.
            for key, value in details.items():
                if key in clearable or value not in (None, "", [], {}):
                    merged[key] = value

        # Some couriers publish the PDF only through the documented tracking
        # endpoint while the shipment details response still has ``label=null``.
        if not _snapshot(merged)["ready"]:
            try:
                tracking_response = await call_salla(
                    db,
                    user_id,
                    "GET",
                    f"/shipments/{shipment_id}/tracking",
                )
            except SallaError:
                tracking_response = None
            tracking_details = (
                tracking_response.get("data")
                if isinstance(tracking_response, dict)
                else None
            )
            if isinstance(tracking_details, dict):
                nested = tracking_details.get("shipment")
                if isinstance(nested, dict):
                    tracking_details = {**tracking_details, **nested}
                for key, value in tracking_details.items():
                    if key in clearable or value not in (None, "", [], {}):
                        merged[key] = value
        return merged

    if not rows:
        return []
    return list(await asyncio.gather(*(enrich(row) for row in rows)))


async def _wait_for_active_outbound_shipments(
    db: Any,
    user_id: str,
    internal_order_id: str,
    embedded: Any,
    *,
    attempts: int = 7,
) -> list[dict[str, Any]]:
    """Wait briefly for a courier AWB triggered by ``completed`` status.

    Salla shipping apps run asynchronously.  iMile and similar couriers may
    attach the shipment a couple of seconds after the order status update, so
    a single immediate list request can incorrectly report that no shipment
    exists.  This bounded poll never creates a second shipment.
    """
    active: list[dict[str, Any]] = []
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(0.5)
        latest_embedded = embedded
        try:
            order_response = await call_salla(
                db,
                user_id,
                "GET",
                f"/orders/{internal_order_id}",
            )
        except SallaError:
            order_response = None
        order_details = (
            order_response.get("data")
            if isinstance(order_response, dict)
            else None
        )
        if isinstance(order_details, dict):
            latest_embedded = order_details.get("shipments")
        rows = await _shipment_rows(
            db,
            user_id,
            internal_order_id,
            latest_embedded,
        )
        active = _active_outbound(rows)
        if active:
            return active
    return active


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
    payment = order.get("payment")
    payment = payment if isinstance(payment, dict) else {}
    raw = shipment.get("payment_method") or order.get("payment_method") or payment.get("method")
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
    internal_order_id: str = "",
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
            response = None
        details = response.get("data") if isinstance(response, dict) else None
        if isinstance(details, dict):
            latest = details
        if not _snapshot(latest)["ready"] and shipment_id:
            try:
                tracking_response = await call_salla(
                    db,
                    user_id,
                    "GET",
                    f"/shipments/{shipment_id}/tracking",
                )
            except SallaError:
                tracking_response = None
            tracking_details = (
                tracking_response.get("data")
                if isinstance(tracking_response, dict)
                else None
            )
            if isinstance(tracking_details, dict):
                nested = tracking_details.get("shipment")
                if isinstance(nested, dict):
                    tracking_details = {**tracking_details, **nested}
                latest = {**latest, **tracking_details}
        if not _snapshot(latest)["ready"] and internal_order_id:
            try:
                order_response = await call_salla(
                    db,
                    user_id,
                    "GET",
                    f"/orders/{internal_order_id}",
                )
            except SallaError:
                order_response = None
            order_details = (
                order_response.get("data")
                if isinstance(order_response, dict)
                else None
            )
            embedded = (
                order_details.get("shipments")
                if isinstance(order_details, dict)
                else None
            )
            try:
                embedded_rows = _active_outbound(
                    await _shipment_rows(
                        db,
                        user_id,
                        internal_order_id,
                        embedded,
                    )
                )
            except SallaError:
                embedded_rows = _active_outbound(
                    [dict(row) for row in embedded if isinstance(row, dict)]
                    if isinstance(embedded, list)
                    else [dict(embedded)] if isinstance(embedded, dict) else []
                )
            for row in embedded_rows:
                if _text(row.get("id")) == shipment_id or _snapshot(row)["ready"]:
                    latest = {**latest, **row}
                    if _snapshot(latest)["ready"]:
                        break
    return latest


async def _best_effort_resync(
    db: Any,
    user_id: str,
    order_number: str,
) -> None:
    """Refresh the order cache without invalidating a verified AWB result."""
    try:
        await resync_single_order(db, user_id, order_number)
    except Exception:
        # The shipping response is authoritative for this action. A transient
        # order-resync/Cloudflare failure must not turn a created AWB into a
        # false printing failure in the UI.
        return


async def _recover_created_shipment(
    db: Any,
    user_id: str,
    internal_order_id: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Recover when Salla times out after accepting shipment creation."""
    shipment_id = _text(source.get("id"))
    latest = await _poll_shipment(
        db,
        user_id,
        shipment_id,
        source,
        attempts=6,
        internal_order_id=internal_order_id,
    )
    if _tracking(latest) or _snapshot(latest)["ready"]:
        return latest

    # ``overwrite_exists_pending`` can replace the pending shipment with a new
    # id, so finish with a fresh list lookup before reporting a real failure.
    for attempt in range(4):
        if attempt:
            await asyncio.sleep(1)
        try:
            active = _active_outbound(
                await _shipment_rows(
                    db,
                    user_id,
                    internal_order_id,
                    None,
                )
            )
        except SallaError:
            continue
        for row in active:
            if _snapshot(row)["ready"]:
                return row
        if active and _tracking(active[0]):
            latest = active[0]
    return latest


_LABEL_ROOT_FIELDS = (
    "shipping_company", "shipping_company_code", "salla_shipment_id",
    "shipping_status", "shipment_status", "tracking_number", "tracking_url",
    "shipping_number", "shipping_label_url",
)


async def _label_baseline(db: Any, user_id: str, order_number: str) -> dict[str, Any] | None:
    if db is None:
        return None
    return await db.unified_orders.find_one(
        {"user_id": str(user_id), "order_number": str(order_number)},
        {"_id": 1, CURRENT_SHIPPING: 1, **{field: 1 for field in _LABEL_ROOT_FIELDS}},
    )


def _label_metadata(row: dict[str, Any] | None) -> dict[str, Any]:
    value = (row or {}).get(CURRENT_SHIPPING)
    return value if isinstance(value, dict) else {}


def _label_carrier(row: dict[str, Any]) -> dict[str, str]:
    current = _label_metadata(row)
    return {
        "code": _text(current.get("company_code") if current else row.get("shipping_company_code")),
        "name": _text(current.get("company_name") if current else row.get("shipping_company")),
    }


def _label_carriers_match(left: dict[str, str], right: dict[str, str]) -> bool | None:
    if left["code"] and right["code"]:
        return left["code"] == right["code"]
    if left["name"] and right["name"]:
        return same_carrier({"company_name": left["name"]}, {"company_name": right["name"]})
    return None


def _stale_label() -> ShippingLabelError:
    return ShippingLabelError(
        "shipping_snapshot_changed",
        "تغيّرت جهة الشحن أو الشحنة الحالية؛ لم تُفتح البوليصة القديمة. أعد التحقق من البوليصة الحالية.",
        status_code=409,
    )


def _internal_carrier(row: dict[str, Any] | None) -> bool:
    carrier = _label_carrier(row or {})
    return bool(carrier["name"] or carrier["code"]) and _is_store_courier({
        "courier_name": carrier["name"], "meta": {"app_id": carrier["code"]},
    })


async def _internal_delivery_document(db, user_id, order_number, order, baseline):
    """Internal delivery is not an external shipment or an external label.

    Ignore old embedded shipments, including their addresses. Recheck the
    carrier generation after provider reads; a real concurrent switch blocks.
    Assignment is read at the same final boundary, never from an old label.
    """
    # Explicit order-level carrier facts take precedence over old embedded
    # shipments. A provider read contradicting our frozen carrier is a race.
    order_carrier = extract_shipping({"shipping": order.get("shipping"),
        "shipping_company": order.get("shipping_company"),
        "shipping_company_code": order.get("shipping_company_code")})
    if order_carrier and _label_carriers_match(_label_carrier(baseline), {
        "code": _text(order_carrier.get("company_code")),
        "name": _text(order_carrier.get("company_name")),
    }) is not True:
        raise _stale_label()
    store = await _store_identity(db, user_id)
    async def document(scoped):
        current = await _label_baseline(scoped, user_id, order_number)
        if not _internal_carrier(current) or (
            _label_carrier(current) != _label_carrier(baseline)
            or _label_metadata(current).get("carrier_updated_at") !=
                _label_metadata(baseline).get("carrier_updated_at")
        ):
            raise _stale_label()
        workflow = await scoped.order_review_workflows.find_one(
            {"user_id": str(user_id), "order_number": order_number},
            {"_id": 0, "store_courier_assignee_id": 1, "store_courier_assignee_name": 1,
             "store_delivery_assignment_id": 1},
        ) or {}
        current_order = {**order, "shipments": []}
        data = _store_courier_print_data(order_number, current_order, {}, store)
        data.update(courier_name=_label_carrier(current)["name"] or "مندوب المتجر",
                    assigned_courier_id=workflow.get("store_courier_assignee_id"),
                    assigned_courier_name=workflow.get("store_courier_assignee_name"),
                    assignment_id=workflow.get("store_delivery_assignment_id"))
        return {"ok": True, "source": "mezan", "ready": True, "label_type": "store_courier",
                "shipment_id": None, "status": "store_courier", "courier_name": data["courier_name"],
                "experiment_override": False, "label_url": None, "tracking_number": None,
                "shipping_number": None, "order_status_completed": _order_is_completed(order),
                "order_status_changed": False, "print_data": data,
                "message": "تم تجهيز مستند المندوب من بيانات الطلب الحالية."}
    return await operational_owner(db, str(user_id), document)


def _current_outbound(rows, baseline):
    """Prefer the canonical identity, not an unrelated ready/greater-ID label."""
    active = _active_outbound(rows)
    current_id = _text(_label_metadata(baseline).get("shipment_id"))
    matching = [row for row in active if current_id and _text(row.get("id")) == current_id]
    return matching or active


async def _persist_verified_snapshot(
    db: Any,
    user_id: str,
    order_number: str,
    snapshot: dict[str, Any],
    *,
    clear_missing: bool = False,
    baseline: dict[str, Any] | None = None,
    created_replacement: bool = False,
    persist: bool = True,
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
    if snapshot.get("tracking_number") not in (None, ""):
        set_fields["shipping_number"] = snapshot["tracking_number"]

    update: dict[str, Any] = {"$set": set_fields}
    if clear_missing:
        update["$unset"] = {
            "shipping_label_url": "",
            "tracking_number": "",
            "tracking_url": "",
            "shipping_number": "",
        }
    row = await _label_baseline(db, user_id, order_number)
    selector = {"user_id": str(user_id), "order_number": str(order_number)}
    if not _label_metadata(row):
        if not persist:
            return
        # Legacy rows retain their original behavior. The metadata condition
        # prevents a webhook creating the new contract between this read/write
        # from being overwritten through the legacy path.
        legacy_selector = {**selector, CURRENT_SHIPPING: (row or {}).get(CURRENT_SHIPPING, {"$exists": False})}
        legacy_result = await db.unified_orders.update_one(legacy_selector, update)
        if legacy_result.matched_count or not _label_metadata(await _label_baseline(db, user_id, order_number)):
            return

    async def commit(scoped):
        current_row = await _label_baseline(scoped, user_id, order_number)
        current = _label_metadata(current_row)
        if not current:
            raise _stale_label()
        current_id = _text(current.get("shipment_id") or current_row.get("salla_shipment_id"))
        snapshot_id = _text(snapshot.get("shipment_id"))
        superseded = list(current.get("superseded_shipment_ids") or [])
        if snapshot_id and snapshot_id in superseded:
            raise _stale_label()
        current_carrier = _label_carrier(current_row)
        snapshot_carrier = {"code": _text(snapshot.get("courier_code")), "name": _text(snapshot.get("courier_name"))}
        carrier_match = _label_carriers_match(current_carrier, snapshot_carrier)
        if carrier_match is False:
            raise _stale_label()
        baseline_current = _label_metadata(baseline)
        if baseline_current:
            if _label_carriers_match(_label_carrier(baseline), current_carrier) is not True:
                raise _stale_label()
            baseline_id = _text(baseline_current.get("shipment_id") or baseline.get("salla_shipment_id"))
            if current_id and current_id not in {baseline_id, snapshot_id}:
                raise _stale_label()
            old_tracking = _text(baseline_current.get("tracking_number") or baseline.get("tracking_number"))
            current_tracking = _text(current.get("tracking_number") or current_row.get("tracking_number"))
            if current_tracking and current_tracking != old_tracking and _tracking(snapshot) != current_tracking:
                raise _stale_label()
        different_id = bool(snapshot_id and snapshot_id != current_id)
        proven_replacement = bool(different_id and (
            created_replacement and baseline_current and carrier_match is True and
            current_id == _text(baseline_current.get("shipment_id") or baseline.get("salla_shipment_id"))
        ))
        if different_id and not proven_replacement:
            # GET-only results cannot prove whether an unknown ID is a fresh
            # replacement or an archived active row. Wait for its webhook.
            raise _stale_label()
        if snapshot.get("ready") and not snapshot_id:
            raise _stale_label()
        current_statuses = [_status(current.get("status")), _status(current_row.get("shipping_status")),
                            _status(current_row.get("shipment_status"))]
        current_status = next((value for value in current_statuses if value in _CANCELLED), current_statuses[0])
        snapshot_status = _status(snapshot.get("status"))
        snapshot_time = provider_time(snapshot.get("shipment_updated_at"))
        current_time = provider_time(current.get("shipment_updated_at"))
        if snapshot_time and current_time and snapshot_time < current_time:
            raise _stale_label()
        if current_status in _CANCELLED and snapshot_id and snapshot_status not in _CANCELLED and not proven_replacement:
            raise _stale_label()
        if not snapshot_id and not clear_missing:
            raise _stale_label()
        if not persist:
            return
        guarded_update = deepcopy(update)
        metadata = deepcopy(current)
        # This local evidence changes the metadata CAS token even when the
        # verified operational values were already equal. Provider clocks
        # advance only from actual provider-supplied timestamps.
        metadata["verified_at"] = now
        if snapshot_time:
            metadata["shipment_updated_at"] = snapshot_time
        if different_id or snapshot_status in _PENDING:
            # A newly created ID may have no AWB/PDF yet. Missing facts in
            # that replacement cannot inherit the previous shipment's label.
            for field in ("shipping_label_url", "tracking_number", "tracking_url", "shipping_number"):
                if field not in guarded_update["$set"]:
                    guarded_update.setdefault("$unset", {})[field] = ""
        if snapshot_status in _CANCELLED:
            for field in ("shipping_label_url", "tracking_number", "tracking_url", "shipping_number"):
                guarded_update["$set"].pop(field, None)
            guarded_update.setdefault("$unset", {}).update({
                "shipping_label_url": "", "tracking_number": "", "tracking_url": "", "shipping_number": "",
            })
        if not snapshot_id and current_status in _CANCELLED:
            guarded_update["$set"].update(shipping_status=current_status, shipment_status=current_status)
        for root, key in (
            ("salla_shipment_id", "shipment_id"), ("shipping_status", "status"),
            ("tracking_number", "tracking_number"), ("tracking_url", "tracking_url"),
            ("shipping_label_url", "label_url"),
        ):
            if root in guarded_update["$set"]:
                metadata[key] = deepcopy(guarded_update["$set"][root])
            elif root in guarded_update.get("$unset", {}):
                metadata[key] = None
        if clear_missing or snapshot_status in _CANCELLED:
            metadata.update(tracking_number=None, tracking_url=None, label_url=None)
        if different_id and current_id:
            metadata["superseded_shipment_ids"] = [*superseded, *([] if current_id in superseded else [current_id])]
        guarded_update["$set"][CURRENT_SHIPPING] = metadata
        guarded_selector = {**selector, CURRENT_SHIPPING: current}
        for field in _LABEL_ROOT_FIELDS:
            guarded_selector[field] = current_row[field] if field in current_row else {"$exists": False}
        result = await scoped.unified_orders.update_one(guarded_selector, guarded_update)
        if result.matched_count != 1:
            raise _stale_label()

    await operational_owner(db, str(user_id), commit)


async def _print_shipment_rows(
    db: Any, user_id: str, internal_order_id: str,
) -> list[dict[str, Any]]:
    """Read provider authority only; ambiguity must not select an older PDF."""
    response = await call_salla(
        db, user_id, "GET", "/shipments",
        params={"order_id": internal_order_id, "per_page": 50},
    )
    listed = response.get("data") if isinstance(response, dict) else None
    if isinstance(listed, dict):
        listed = listed.get("shipments", [listed] if listed.get("id") else [])
    if not isinstance(listed, list) or any(not isinstance(row, dict) for row in listed):
        raise ShippingLabelError("salla_shipping_unavailable", "البوليصة الحالية غير متاحة", status_code=502)
    pagination = response.get("pagination") or {}
    if pagination.get("links", {}).get("next") or len(listed) >= 50:
        return []
    for row in listed:
        shipment_order = row.get("order_id") or (row.get("order") or {}).get("id")
        if shipment_order is not None and _text(shipment_order) != internal_order_id:
            raise ShippingLabelError("salla_order_reference_mismatch", "أعادت سلة شحنة لطلب مختلف؛ أوقفت الطباعة.")
    active = _active_outbound(listed)
    # Do not infer current from numeric IDs, timestamps, or label readiness.
    if len(active) != 1:
        return []
    current = active[0]
    shipment_id = _text(current.get("id"))
    if not shipment_id:
        return []
    response = await call_salla(db, user_id, "GET", f"/shipments/{shipment_id}")
    details = response.get("data") if isinstance(response, dict) else None
    if not isinstance(details, dict) or _text(details.get("id")) != shipment_id:
        raise ShippingLabelError("salla_shipping_unavailable", "البوليصة الحالية غير متاحة", status_code=502)
    shipment_order = details.get("order_id") or (details.get("order") or {}).get("id")
    if shipment_order is not None and _text(shipment_order) != internal_order_id:
        raise ShippingLabelError("salla_order_reference_mismatch", "أعادت سلة شحنة لطلب مختلف؛ أوقفت الطباعة.")
    # Detail is authoritative, including missing/null label. Never merge an old URL.
    return _active_outbound([details])


async def refresh_shipping_label(
    db: Any,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Read/select/print current provider data without local writes or comparisons."""
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
        if _text(order.get("reference_id")) != normalized or _text(order.get("id")) != internal_id:
            raise ShippingLabelError("salla_order_reference_mismatch", "أعادت سلة طلبًا مختلفًا؛ أوقفت الطباعة.")
        carrier = extract_shipping({
            "shipping": order.get("shipping"),
            "shipping_company": order.get("shipping_company"),
            "shipping_company_code": order.get("shipping_company_code"),
        }) or {}
        store_courier = _is_store_courier({
            "courier_name": carrier.get("company_name"),
            "meta": {"app_id": carrier.get("company_code")},
        })
        rows = [] if store_courier else await _print_shipment_rows(
            db,
            user_id,
            internal_id,
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
    if store_courier or (active and _is_store_courier(active[0])):
        # Use the pre-guard courier formatter, without the issue path's status
        # transition or resync. Old external shipments do not supply its data.
        source = {} if store_courier else dict(active[0])
        store = await _store_identity(db, user_id)
        print_order = {**order, "shipments": []} if store_courier else order
        print_data = _store_courier_print_data(normalized, print_order, source, store)
        return {
            "ok": True, "source": "mezan", "ready": True,
            "label_type": "store_courier", "shipment_id": _text(source.get("id")) or None,
            "status": "store_courier", "courier_name": "مندوب المتجر",
            "label_url": None, "tracking_number": None, "shipping_number": None,
            "print_data": print_data,
            "message": "تم تجهيز بوليصة مندوب المتجر من بيانات الطلب.",
        }
    current = active[0] if active else {}

    snapshot = _snapshot(current)

    return {
        "ok": True,
        "source": "salla",
        **snapshot,
        "message": (
            "تم التحقق من سلة والبوليصة الحالية جاهزة."
            if snapshot["ready"]
            else "البوليصة الحالية غير متاحة"
        ),
    }


async def issue_shipping_label(
    db: Any,
    user_id: str,
    order_number: str,
    *,
    force_store_courier: bool = False,
) -> dict[str, Any]:
    normalized = _text(order_number)
    if not normalized:
        raise ShippingLabelError(
            "order_number_required",
            "رقم الطلب مطلوب.",
            status_code=400,
        )

    # Reconcile before freezing the baseline. Never advance it after provider IO:
    # subsequent changes are concurrent changes and must still fail closed.
    await _best_effort_resync(db, user_id, normalized)
    label_baseline = await _label_baseline(db, user_id, normalized)
    try:
        internal_id, order = await _resolve_order(
            db, user_id, normalized
        )
        if _internal_carrier(label_baseline):
            return await _internal_delivery_document(db, user_id, normalized, order, label_baseline)
        # Keep the shipment created with the order before changing status.
        # Some Salla couriers temporarily remove it from order details during
        # the completed transition. This snapshot is read-only and must never
        # be submitted to POST /shipments.
        order_created_shipments = _active_outbound(
            [dict(row) for row in order.get("shipments", []) if isinstance(row, dict)]
            if isinstance(order.get("shipments"), list)
            else [dict(order["shipments"])]
            if isinstance(order.get("shipments"), dict)
            else []
        )

        order, order_status_changed = await _ensure_order_completed(
            db,
            user_id,
            internal_id,
            order,
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

    active = _current_outbound(rows, label_baseline)
    if order_status_changed and not active:
        try:
            active = await _wait_for_active_outbound_shipments(
                db,
                user_id,
                internal_id,
                order.get("shipments"),
            )
        except SallaError:
            # The normal error below remains more useful than leaking a
            # transient provider response after Salla accepted the status.
            active = []
        if not active:
            # Fall back to the immutable shipment attached at order.created.
            # Subsequent polling refreshes it through GET /orders/{id}; it
            # never creates or overwrites a shipment.
            active = order_created_shipments
    if active and (force_store_courier or _is_store_courier(active[0])):
        source = dict(active[0])
        if force_store_courier:
            # Experiment-only override: reuse the authoritative Salla order
            # address and packages, but never mutate or cancel its real AWB.
            source["courier_name"] = "مندوب المتجر"
            source["company"] = "مندوب المتجر"
        store = await _store_identity(db, user_id)
        print_data = _store_courier_print_data(
            normalized,
            order,
            source,
            store,
        )
        if not force_store_courier:
            await _persist_verified_snapshot(
                db, user_id, normalized, _snapshot(source), baseline=label_baseline, persist=False,
            )
        return {
            "ok": True,
            "source": "mezan",
            "ready": True,
            "label_type": "store_courier",
            "shipment_id": _text(source.get("id")) or None,
            "status": "store_courier",
            "courier_name": "مندوب المتجر",
            "experiment_override": force_store_courier,
            "label_url": None,
            "tracking_number": None,
            "shipping_number": None,
            "order_status_completed": True,
            "order_status_changed": order_status_changed,
            "print_data": print_data,
            "message": (
                "تم تحويل الطلب إلى تم التنفيذ وتجهيز بوليصة مندوب المتجر للمسار التجريبي."
                if force_store_courier and order_status_changed
                else "تم تجهيز بوليصة مندوب المتجر للمسار التجريبي دون تغيير شحنة سلة الحقيقية."
                if force_store_courier
                else "تم تحويل الطلب إلى تم التنفيذ وتجهيز بوليصة مندوب المتجر."
                if order_status_changed
                else "تم تجهيز بوليصة مندوب المتجر من بيانات الطلب."
            ),
        }

    for row in active:
        snapshot = _snapshot(row)
        if snapshot["ready"]:
            await _persist_verified_snapshot(
                db,
                user_id,
                normalized,
                snapshot,
                baseline=label_baseline,
            )
            return {
                "ok": True,
                "source": "salla",
                "order_status_completed": True,
                "order_status_changed": order_status_changed,
                **snapshot,
            }

    if not active:
        raise ShippingLabelError(
            "pending_shipment_missing",
            "لا توجد شحنة صادرة حالية في سلة لإصدار بوليصتها.",
        )

    source = active[0]
    if (
        order_status_changed
        or _status(source.get("status")) in _PENDING
        or _tracking(source)
    ):
        polled = await _poll_shipment(
            db,
            user_id,
            _text(source.get("id")),
            source,
            attempts=8,
            internal_order_id=internal_id,
        )
        snapshot = _snapshot(polled)
        await _persist_verified_snapshot(
            db,
            user_id,
            normalized,
            snapshot,
            baseline=label_baseline,
        )
        return {
            "ok": True,
            "source": "salla",
            "order_status_completed": True,
            "order_status_changed": order_status_changed,
            **snapshot,
            "message": (
                "تم التحقق من سلة والبوليصة جاهزة للطباعة."
                if snapshot["ready"]
                else "تم إصدار رقم الشحنة، ورابط البوليصة ما زال قيد التجهيز في سلة؛ لم تُفتح الطباعة بعد."
                if snapshot.get("tracking_number") or snapshot.get("shipping_number")
                else "سلة ما زالت تُصدر البوليصة؛ لم تُفتح الطباعة بعد."
            ),
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
        status_code = int(exc.status_code or 0)
        if status_code in {408, 429} or status_code >= 500:
            recovered = await _recover_created_shipment(
                db,
                user_id,
                internal_id,
                source,
            )
            recovered_snapshot = _snapshot(recovered)
            if (
                recovered_snapshot["ready"]
                or recovered_snapshot.get("tracking_number")
                or recovered_snapshot.get("shipping_number")
            ):
                await _persist_verified_snapshot(
                    db,
                    user_id,
                    normalized,
                    recovered_snapshot,
                    baseline=label_baseline,
                )
                return {
                    "ok": True,
                    "source": "salla",
                    "recovered_after_timeout": True,
                    "order_status_completed": True,
                    "order_status_changed": order_status_changed,
                    **recovered_snapshot,
                    "message": (
                        "تم إصدار الشحنة رغم تأخر استجابة سلة، وتم استرداد البوليصة وهي جاهزة للطباعة."
                        if recovered_snapshot["ready"]
                        else "تم إصدار رقم الشحنة رغم تأخر استجابة سلة، ورابط البوليصة ما زال قيد التجهيز؛ لم تُفتح الطباعة بعد."
                    ),
                }
        raise ShippingLabelError(
            "salla_label_creation_failed",
            "لم تؤكد سلة إصدار البوليصة، ولم يظهر رقم شحنة جديد بعد إعادة التحقق.",
            status_code=502,
        ) from exc

    created = response.get("data") if isinstance(response, dict) else None
    confirmed_created_id = _text(created.get("id")) if isinstance(created, dict) else ""
    if not isinstance(created, dict):
        created = await _recover_created_shipment(
            db,
            user_id,
            internal_id,
            source,
        )
        if not (_tracking(created) or _snapshot(created)["ready"]):
            raise ShippingLabelError(
                "salla_label_response_invalid",
                "أعادت سلة استجابة إصدار غير مكتملة، ولم يظهر رقم شحنة بعد إعادة التحقق.",
                status_code=502,
            )

    shipment_id = _text(created.get("id") or source.get("id"))
    latest = await _poll_shipment(
        db,
        user_id,
        shipment_id,
        created,
        internal_order_id=internal_id,
    )
    snapshot = _snapshot(latest)
    await _persist_verified_snapshot(
        db,
        user_id,
        normalized,
        snapshot,
        baseline=label_baseline,
        created_replacement=bool(confirmed_created_id and confirmed_created_id == snapshot.get("shipment_id")),
    )

    return {
        "ok": True,
        "source": "salla",
        "order_status_completed": True,
        "order_status_changed": order_status_changed,
        **snapshot,
        "message": (
            "تم تحويل الطلب إلى تم التنفيذ، ثم إصدار البوليصة من سلة وأصبحت جاهزة للطباعة."
            if snapshot["ready"] and order_status_changed
            else "تم إصدار البوليصة من سلة وأصبحت جاهزة للطباعة."
            if snapshot["ready"]
            else "تم تحويل الطلب إلى تم التنفيذ، ثم قبلت سلة الإصدار وما زالت تُنشئ البوليصة."
            if order_status_changed and not (
                snapshot.get("tracking_number") or snapshot.get("shipping_number")
            )
            else "تم إصدار رقم الشحنة، ورابط البوليصة ما زال قيد التجهيز في سلة؛ لم تُفتح الطباعة بعد."
            if snapshot.get("tracking_number") or snapshot.get("shipping_number")
            else "قبلت سلة الإصدار وما زالت تُنشئ البوليصة؛ لم تُفتح الطباعة بعد."
        ),
    }
