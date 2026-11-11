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
import httpx
from typing import Any, Awaitable, Callable

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
    except (SallaError, TimeoutError, ConnectionError, httpx.HTTPError):
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


async def _shipment(db: Any, owner: str, internal_id: str, order: dict[str, Any], *,
                    strict=False, baseline=None) -> dict:
    metadata = legacy._label_metadata(baseline)
    current_id = legacy._text(metadata.get("shipment_id") or (baseline or {}).get("salla_shipment_id"))
    superseded = {str(value) for value in metadata.get("superseded_shipment_ids") or []}

    def candidates(response, *, standalone=False):
        rows = _rows(response)
        if not strict:
            return rows
        result = []
        for row in rows:
            shipment_id = legacy._text(row.get("id"))
            if shipment_id in superseded or (current_id and current_id != shipment_id):
                continue
            # Embedded/order-scoped lists prove their parent by construction.
            # A global list can only return a positively identified child.
            nested = row.get("order") if isinstance(row.get("order"), dict) else {}
            if standalone and legacy._text(row.get("order_id") or nested.get("id")) != internal_id:
                continue
            result.append(row)
        return result

    needs_current_child = strict and not current_id and not _order_label_carrier(order, strict=True)
    standalone = None

    async def verified(selected):
        if not needs_current_child or not (legacy._internal_carrier(baseline) or _mobile_courier_row(selected)):
            return selected
        # A sparse order does not identify its current shipment. A unique active
        # child from an order-filtered complete provider list can identify its courier.
        response = standalone
        if response is None:
            response = await _get(db, owner, "/shipments", params={"order_id": internal_id, "per_page": 50})
        page = response.get("pagination") if isinstance(response, dict) else None
        page = page if isinstance(page, dict) else {}
        rows = candidates(response, standalone=True)
        eligible = [row for row in rows if _current([row], internal_id)
                    and legacy._status(row.get("status")) not in {"", "delivered", "returned", "refunded"}]
        if (len(eligible) != 1 or len(_rows(response)) >= 50
                or (page.get("links") or {}).get("next")
                or any(str(page.get(key, 1)) != "1" for key in ("totalPages", "total_pages", "last_page"))):
            raise legacy._stale_label()
        return eligible[0]

    if needs_current_child and legacy._internal_carrier(baseline):
        return await verified({})

    embedded = candidates(order.get("shipments", []))
    selected = _current(embedded, internal_id)
    if selected and _printable(selected)["ready"]:
        return await verified(selected)

    # Order-scope shipments first, as in the earlier Amasi-compatible path.
    # The standalone API is fallback only: a 403/504 must not veto a PDF
    # obtained from the current order's shipment.
    for path, params in (
        (f"/orders/{internal_id}/shipments", {"per_page": 50}),
        ("/shipments", {"order_id": internal_id, "per_page": 50}),
    ):
        response = await _get(db, owner, path, params=params)
        if path == "/shipments":
            standalone = response
        if response is not None:
            selected = _merge_rows(selected, candidates(response, standalone=(path == "/shipments")), internal_id)
        if selected and _printable(selected)["ready"]:
            return await verified(selected)

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
    return await verified(selected or {})


def _mobile_courier_row(row: dict) -> bool:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    return legacy._mobile_store_courier(row.get("courier_name"), meta.get("app_id"))


def _order_label_carrier(order: dict, *, strict: bool) -> dict:
    if strict:
        # Resolve the same order aliases as ingestion but never use shipment history
        # (including shipping.shipment) as a substitute for an explicit current carrier.
        shipping = order.get("shipping")
        carrier = legacy.extract_shipping({
            **order, "shipments": [], "shipment": None,
            "shipping": {**shipping, "shipment": None} if isinstance(shipping, dict) else None,
        }) or {}
        if carrier.get("source_path") == "delivery_method" and legacy._text(carrier.get("company_name")).casefold() == "shipping":
            return {}
        return carrier
    return legacy.extract_shipping({key: order.get(key) for key in (
        "shipping", "shipping_company", "shipping_company_code",
    )}) or {}


def _order_status(order: dict[str, Any]) -> str | None:
    from assembly_status_policy import status_values
    from preparation_receipt_status_message import ALIASES

    values = status_values(order.get("status")) | status_values(order.get("status_slug"))
    values = {ALIASES.get(value.replace("_", " "), value) for value in values}
    return next(iter(values)) if len(values) == 1 else None


async def _with_order_destination(
    db: Any, owner: str, number: str, internal_id: str, order: dict[str, Any],
    shipment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the last verified *order* destination, not an archived AWB.

    Only the merchant's own validated order identity can supply this fallback.
    Never change a shipment or make an additional request to Salla.
    """
    from store_delivery_domain import normalize_city
    fresh = legacy._order_delivery_address(order)
    if legacy._street_destination(fresh):
        return order

    recipient = legacy.resolve_salla_recipient({**order, "shipments": []}) or {}

    def compatible(address, contact):
        previous_city = normalize_city(address.get("city"))
        if fresh.get("city") and previous_city and previous_city != normalize_city(fresh["city"]):
            return False
        for current, previous in ((recipient.get("name"), contact.get("full_name") or contact.get("name")),
                                  (recipient.get("mobile"), contact.get("mobile") or contact.get("phone"))):
            if current and previous and legacy._text(current).casefold() != legacy._text(previous).casefold():
                return False
        return True

    saved_destination = {}
    collection = getattr(db, "unified_orders", None) if db is not None else None
    if collection is not None:
        saved = await collection.find_one(
            {"user_id": str(owner), "order_number": str(number)},
            {"_id": 0, "order_id": 1, "raw_by_source.salla_direct.id": 1,
             "salla_address_verified_at": 1, "shipping_address_raw": 1, "shipping_address_source_path": 1},
        )
        saved = saved if isinstance(saved, dict) else {}
        raw = saved.get("raw_by_source")
        raw = raw.get("salla_direct") if isinstance(raw, dict) else None
        saved_id = legacy._text((raw.get("id") if isinstance(raw, dict) else None) or saved.get("order_id"))
        address = saved.get("shipping_address_raw")
        source = legacy._text(saved.get("shipping_address_source_path")).split(".")
        if (saved.get("salla_address_verified_at") and saved_id == internal_id and isinstance(address, dict)
                and not any(part in {"customer", "buyer", "sender", "ship_from"} for part in source)
                and (not _separate(order, recipient) or _recipient_address_path(source))):
            destination = legacy._delivery_address(address)
            if destination and compatible(destination, address):
                saved_destination = destination
                if legacy._street_destination(destination):
                    return {**order, "_mezan_current_shipping_address": destination}

    if shipment:
        destination = legacy._order_delivery_address(shipment)
        contact = legacy.resolve_salla_recipient(shipment) or {}
        if destination and compatible(destination, contact) and (not fresh or legacy._street_destination(destination)):
            return {**order, "_mezan_current_shipping_address": destination}
    if not fresh and saved_destination:
        return {**order, "_mezan_current_shipping_address": saved_destination}
    return order


async def mezan_courier_name(db, owner: str, number: str) -> str | None:
    """Use the current Order Engine carrier also displayed by assembly search."""
    from .repository import MongoOrderRepository
    from .service import OrderNotFoundError, get_order

    try:
        order = await get_order(MongoOrderRepository(db), user_id=owner, order_number=number)
    except OrderNotFoundError:
        return None
    carrier = order.shipping
    if order.order_number == number and legacy._mobile_store_courier(carrier.company, carrier.company_code):
        return carrier.company
    return None


class _RecipientShipmentNeeded(Exception):
    """Request a read outside the Mongo transaction, before any document is saved."""


async def with_courier_destination(db, owner, callback):
    from operational_atomic import operational_owner

    try:
        return await operational_owner(db, owner, lambda scoped: callback(scoped, None))
    except _RecipientShipmentNeeded as missing:
        internal_id, number, courier, recipient = missing.args
    try:
        rows = await _outgoing_address_rows(db, owner, internal_id, number, courier, recipient)
    except legacy.ShippingLabelError as unavailable:
        # A missing optional national code must not suppress a known street.
        try:
            return await operational_owner(db, owner, lambda scoped: callback(scoped, []))
        except legacy.ShippingLabelError as local_error:
            if local_error.code == "recipient_address_unavailable":
                raise unavailable from None
            raise
    return await operational_owner(db, owner, lambda scoped: callback(scoped, rows))


def _address_list(response):
    """Only a complete, order-filtered page can identify the current child."""
    if not isinstance(response, dict):
        return []
    rows = _rows(response)
    page = response.get("pagination") or {}
    if not isinstance(page, dict):
        return []
    links = page.get("links") or {}
    if (len(rows) >= 50 or not isinstance(links, dict) or links.get("next")
            or any(str(page.get(k, 1)) != "1" for k in ("totalPages", "total_pages", "last_page"))):
        return []
    return rows


async def _outgoing_address_rows(db, owner, internal_id, number, courier, recipient, selected=None):
    """List rows can omit the actual destination. Read only the proven child."""
    if not internal_id.isdecimal():
        return []
    if selected and not _needs_shipment_destination(
        _shipment_destination([selected], internal_id, number, courier, recipient), True
    ):
        return [selected]
    unreadable = False
    tried_details = set()
    partial = []
    for path, params, scoped in (
        (f"/orders/{internal_id}/shipments", {"per_page": 50}, True),
        ("/shipments", {"order_id": internal_id, "per_page": 50}, False),
    ):
        if selected is not None and not scoped:
            rows = [selected]
        else:
            response = await _get(db, owner, path, params=params)
            unreadable |= response is None
            rows = _address_list(response)
        # Scope comes from the URL we requested, never a field provided by Salla.
        rows = [{**row, "_mezan_order_scoped": scoped} for row in rows]
        if scoped:
            for row in rows:
                nested = row.get("order") if isinstance(row.get("order"), dict) else {}
                row["order_id"] = row.get("order_id") or nested.get("id") or internal_id
        current = _current_destination_rows(rows, internal_id, number, courier)
        if len(current) != 1:
            continue
        address = _shipment_destination(current, internal_id, number, courier, recipient)
        if not _needs_shipment_destination(address, True):
            return current
        shipment_id = legacy._text(current[0].get("id"))
        if any(address.get(k) for k in (*_STREET_FIELDS, *_NATIONAL_FIELDS)):
            partial = current
        if not shipment_id.isdecimal() or shipment_id in tried_details:
            continue
        tried_details.add(shipment_id)
        response = await _get(db, owner, f"/shipments/{shipment_id}")
        if response is None:
            unreadable = True
            continue
        detail = response.get("data") if isinstance(response, dict) else None
        if isinstance(detail, dict) and isinstance(detail.get("shipment"), dict):
            detail = {**detail, **detail["shipment"]}
        if not isinstance(detail, dict) or not detail:
            continue
        if legacy._text(detail.get("id")) != shipment_id:
            return []
        merged = {**current[0], **{k: v for k, v in detail.items() if v not in (None, "", {}, [])},
                  "_mezan_order_scoped": scoped}
        if (detail.get("order") and isinstance(detail["order"], dict)
                and legacy._text(detail["order"].get("id")) not in {"", internal_id}):
            return []
        # Never splice an old address or buyer's name into the detail ship_to.
        enriched = _shipment_destination([merged], internal_id, number, courier, recipient)
        if not enriched:
            return []
        if not _needs_shipment_destination(enriched, True):
            return [merged]
        if any(enriched.get(k) for k in (*_STREET_FIELDS, *_NATIONAL_FIELDS)):
            partial = [merged]
    if unreadable and not partial:
        raise legacy.ShippingLabelError(
            "recipient_address_source_unavailable", "تعذّر الاتصال بسلة لقراءة عنوان شحنة المستلم.", status_code=503,
        )
    return partial


def _current_destination_rows(rows, internal_id, number, courier):
    from store_delivery_domain import normalize_shipping_carrier
    from salla_shipping import _carrier

    current = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        nested = row.get("order") if isinstance(row.get("order"), dict) else {}
        order_id = row.get("order_id") or nested.get("id")
        reference = row.get("order_reference_id") or nested.get("reference_id")
        if legacy._text(order_id) != internal_id or legacy._text(reference) not in {"", number}:
            continue
        if normalize_shipping_carrier(_carrier(row).get("company_name")) != normalize_shipping_carrier(courier):
            continue
        direction = legacy._status(row.get("type") or row.get("shipment_type"))
        status = legacy._status(row.get("status"))
        if (not direction or not status) and not row.get("_mezan_order_scoped"):
            continue
        if direction and direction not in {"shipment", "outbound", "outgoing"}:
            continue
        if _current([row], internal_id) and status not in {"delivered", "returned", "refunded"}:
            current.append(row)
    return current


def _shipment_destination(rows, internal_id, number, courier, recipient):
    from .customer_history import normalize_saudi_mobile

    current = _current_destination_rows(rows, internal_id, number, courier)
    if len(current) != 1:
        return {}
    ship_to = current[0].get("ship_to")
    if not isinstance(ship_to, dict):
        return {}
    if (not current[0].get("type") and not current[0].get("shipment_type")
            and not (recipient.get("mobile") or recipient.get("name"))):
        return {}
    known_phone = legacy._text(recipient.get("mobile") or recipient.get("phone"))
    phone = legacy._text(ship_to.get("phone") or ship_to.get("mobile"))
    if known_phone and phone:
        if (normalize_saudi_mobile(known_phone) or known_phone) != (normalize_saudi_mobile(phone) or phone):
            return {}
    known_name = legacy._text(recipient.get("name"))
    shipment_name = legacy._text(ship_to.get("name") or ship_to.get("full_name"))
    if known_name and shipment_name and known_name.casefold() != shipment_name.casefold():
        return {}
    if (known_name or known_phone) and not ((known_phone and phone) or (known_name and shipment_name)):
        return {}
    return {**legacy._delivery_address(ship_to), "name": shipment_name,
            "phone": ship_to.get("phone") or ship_to.get("mobile")}


_STREET_FIELDS = ("address_line", "formatted", "street")
_NATIONAL_FIELDS = ("short_address", "national_address")


def _needs_shipment_destination(address, independent):
    if not any(address.get(key) for key in _STREET_FIELDS):
        return True
    country = legacy._text(address.get("country_code") or address.get("country")).casefold()
    saudi = country in {"", "sa", "sau", "saudi arabia", "السعودية", "المملكة العربية السعودية"}
    return independent and saudi and not any(address.get(key) for key in _NATIONAL_FIELDS)


def _separate(order, recipient):
    return legacy.recipient_is_independent(order, recipient) or order.get("source") == "buy_as_gift"


def _prefer_destination(local, ship_to):
    # Choose one destination as a unit. Never attach an old short code to
    # another street, or use a city comparison to veto the current ship_to.
    def completeness(destination):
        return (any(destination.get(k) for k in _STREET_FIELDS),
                any(destination.get(k) for k in _NATIONAL_FIELDS))
    return ship_to if completeness(ship_to) > completeness(local) else local


def _apply_recipient_destination(order, recipient, address):
    if not any(address.get(k) for k in (*_STREET_FIELDS, *_NATIONAL_FIELDS)):
        raise legacy.ShippingLabelError("recipient_address_unavailable", "تعذّر جلب عنوان مستلم الطلب من سلة.")
    updated = {**order, "_mezan_current_shipping_address": address}
    name = recipient.get("name") or address.get("name")
    if not name:
        raise legacy.ShippingLabelError("recipient_address_unavailable", "تعذّر جلب بيانات مستلم الطلب من سلة.")
    if not recipient.get("name") or not recipient.get("mobile"):
        updated["recipient"] = {**recipient, "name": name, "mobile": recipient.get("mobile") or address.get("phone")}
    return updated


def _independent_recipient_order(order, saved_address, saved_path):
    # The generic root address has no recipient role. Refresh can also copy
    # a buyer-derived address into shipping.address or shipping_address.
    shipping = dict(order["shipping"]) if isinstance(order.get("shipping"), dict) else {}
    order = {**order, "shipping": shipping}
    order.pop("address", None)
    buyer = order.get("customer")
    excluded = [buyer.get(k) for k in ("address", "shipping_address")] if isinstance(buyer, dict) else []
    if not _recipient_address_path(saved_path):
        excluded.append(saved_address)
    for container, key in ((order, "shipping_address"), (shipping, "address")):
        if container.get(key) in excluded:
            container.pop(key, None)
    return order


def _recipient_address_path(path):
    # Only named order-delivery or recipient containers have positive provenance.
    parts = {part.split("[")[0] for part in path}
    return bool(parts & {"recipient", "receiver", "consignee", "ship_to", "shipping", "shipping_address"}
                and not parts & {"customer", "buyer", "sender", "ship_from", "shipment", "shipments"})


async def _with_verified_destination(db, owner, number, internal_id, order, shipment=None, *, courier=None):
    # A sparse Salla order can take the provider path when Mezan does not yet
    # project its courier. Resolve exactly the same delivery role in this case.
    current = {**order, "shipments": [], "shipment": None}
    recipient = legacy.resolve_salla_recipient(current) or {}
    if not courier or not _separate(current, recipient):
        return await _with_order_destination(db, owner, number, internal_id, order, shipment)
    current = _independent_recipient_order(current, None, ())
    current = await _with_order_destination(db, owner, number, internal_id, current)
    address = current.get("_mezan_current_shipping_address") or legacy._order_delivery_address(current)
    if _needs_shipment_destination(address, True) or not recipient.get("name"):
        try:
            rows = await _outgoing_address_rows(db, owner, internal_id, number, courier, recipient, shipment)
        except legacy.ShippingLabelError:
            if not any(address.get(k) for k in (*_STREET_FIELDS, *_NATIONAL_FIELDS)):
                raise
            rows = []
        incoming = _shipment_destination(rows, internal_id, number, courier, recipient)
        address = _prefer_destination(address, incoming)
    return _apply_recipient_destination(current, recipient, address)


async def local_mezan_courier_label(db, owner: str, number: str, workflow: dict, shipment_rows=None) -> dict | None:
    """Generate a merchant QR from the same transaction's current order and recipient."""
    from assembly_status_policy import canonical_status

    name = await mezan_courier_name(db, owner, number)
    if not name:
        return None
    source = await db.unified_orders.find_one({"user_id": owner, "order_number": number}, {
        "_id": 0, "order_status": 1, "order_status_slug": 1, "g47_salla_snapshot": 1,
        "raw_by_source.salla_direct": 1, "shipping_address_raw": 1,
        "salla_shipment_id": 1, "salla_shipment_snapshot": 1, legacy.CURRENT_SHIPPING: 1,
        "shipping_address_source_path": 1, "salla_address_verified_at": 1, "salla_address_provider_updated_at": 1,
    }) or {}
    by_source = source.get("raw_by_source")
    raw = by_source.get("salla_direct") if isinstance(by_source, dict) else None
    if not isinstance(raw, dict):
        raise legacy.ShippingLabelError("salla_order_reference_mismatch", "تعذّر تأكيد هوية الطلب في ميزان.")
    status = canonical_status(source)
    if status == "in_progress":
        from assembly_completion_delivery import public_status
        confirmed = public_status(workflow, canonical=status)["order_completion_status"] == "confirmed"
        if not (confirmed and workflow.get("salla_order_status") == "completed"
                and workflow.get("salla_order_status_verified_at")):
            raise legacy.ShippingLabelError("carrier_order_in_progress", "بانتظار تأكيد تنفيذ الطلب في سلة.")
    elif status != "completed":
        raise legacy.ShippingLabelError("order_status_not_completed", "حالة الطلب الحالية لا تسمح بالطباعة.")
    if not legacy._text(raw.get("id")) or legacy._text(raw.get("reference_id")) != number:
        raise legacy.ShippingLabelError("salla_order_reference_mismatch", "تعذّر تأكيد هوية الطلب في ميزان.")

    # Ignore embedded shipment history and any previously rendered document.
    order = {**raw, "shipments": [], "shipment": None}
    order.pop("_mezan_current_shipping_address", None)
    recipient = legacy.resolve_salla_recipient(order) or {}
    separate = _separate(order, recipient)
    path = legacy._text(source.get("shipping_address_source_path")).split(".")
    if separate:
        order = _independent_recipient_order(order, source.get("shipping_address_raw"), path)
        recipient = legacy.resolve_salla_recipient(order) or {}
    order_at = legacy.provider_time(raw.get("updated_at"))
    saved_at = legacy.provider_time(source.get("salla_address_provider_updated_at") or source.get("salla_address_verified_at"))
    newer_recipient_street = (order_at and saved_at and order_at > saved_at
                              and legacy._street_destination(legacy._order_delivery_address(order)))
    if (source.get("salla_address_verified_at") and not newer_recipient_street
            and (not separate or _recipient_address_path(path))
            and not any(part in {"customer", "buyer", "sender", "ship_from"} for part in path)):
        address = source.get("shipping_address_raw")
        address = address if isinstance(address, dict) else {}
        same_recipient = all(not current or not saved or legacy._text(current).casefold() == legacy._text(saved).casefold()
                             for current, saved in ((recipient.get("name"), address.get("name") or address.get("full_name")),
                                                    (recipient.get("mobile"), address.get("mobile") or address.get("phone"))))
        destination = legacy._delivery_address(address)
        if destination and same_recipient:
            order["_mezan_current_shipping_address"] = destination
    # In gift deliveries Salla may expose only the recipient contact in the
    # order and keep the destination exclusively in its outgoing shipment.
    address = (order.get("_mezan_current_shipping_address") or legacy._order_delivery_address(order)
               or legacy._order_delivery_address(recipient))
    if (recipient or separate) and (_needs_shipment_destination(address, separate) or (separate and not recipient.get("name"))):
        internal_id = legacy._text(raw["id"])
        snapshot = source.get("salla_shipment_snapshot")
        stored = {}
        current = legacy._label_metadata(source)
        stored_id = legacy._text(source.get("salla_shipment_id"))
        if (not legacy._street_destination(address) and isinstance(snapshot, dict) and stored_id
                and legacy._text(snapshot.get("id")) == stored_id
                and legacy._text(current.get("shipment_id")) in {"", stored_id}
                and stored_id not in {str(value) for value in current.get("superseded_shipment_ids") or []}):
            stored = _shipment_destination([snapshot], internal_id, number, name, recipient)
        local = _prefer_destination(address, stored)
        if _needs_shipment_destination(local, separate) or (separate and not (recipient.get("name") or local.get("name"))):
            if shipment_rows is None:
                raise _RecipientShipmentNeeded(internal_id, number, name, recipient)
            incoming = _shipment_destination(shipment_rows, internal_id, number, name, recipient)
            local = _prefer_destination(address, incoming)
        if separate:
            order = _apply_recipient_destination(order, recipient, local)
        elif local:
            order["_mezan_current_shipping_address"] = local
    store = raw.get("store") if isinstance(raw.get("store"), dict) else {}
    if not store.get("name"):
        integration = await db.salla_integrations.find_one({"user_id": owner}, {"_id": 0, "store_name": 1}) or {}
        store = {**store, "name": integration.get("store_name") or "أماسي"}
    data = legacy._store_courier_print_data(number, order, {}, store)
    data.update(courier_name=name, store_phone=legacy._text(store.get("phone") or store.get("mobile")) or None)
    return {"ok": True, "source": "mezan", "ready": True, "label_type": "store_courier",
            "order_number": number, "assembly_completion_confirmed": True,
            "order_status_completed": True, "order_status_changed": False, "shipment_id": None,
            "tracking_number": None, "shipping_number": None, "label_url": None, "courier_name": name,
            "status": "store_courier", "print_data": data, "message": "تم تجهيز بوليصة مندوب المتجر."}


async def carrier_label(
    db: Any, owner: str, order_number: str, *, issue: bool,
    completed_only: bool = False, explicit_complete: bool = False,
    before_explicit_complete: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """One Salla order-status POST when needed; read the original carrier AWB.

    A rejected or ambiguous status acknowledgement is reconciled by GET,
    never by blindly posting again. No shipment-create API is used.
    """
    number = legacy._text(order_number)
    if not number:
        raise legacy.ShippingLabelError("order_number_required", "رقم الطلب مطلوب.", status_code=400)
    baseline = await legacy._label_baseline(db, owner, number) if completed_only else None
    internal_id, order = await _resolve(db, owner, number)
    status = _order_status(order)
    if completed_only and (legacy._text(order.get("id")) != internal_id
                           or legacy._text(order.get("reference_id")) != number):
        raise legacy.ShippingLabelError("salla_order_reference_mismatch", "تعذّر تأكيد هوية الطلب في سلة.")
    if status in _CANCELLED | {"refunded"}:
        raise legacy.ShippingLabelError("order_cancelled", "الطلب ملغى أو مسترجع في سلة.")
    if explicit_complete:
        # Only the explicit, locally fenced refresh endpoint enables this. An
        # acknowledged status write flows straight to shipment lookup: no
        # verification GET, polling or automatic status-POST retry in between.
        if status not in {"in_progress", "completed"}:
            raise legacy.ShippingLabelError("order_status_not_completed", "حالة الطلب لا تسمح بإعادة تأكيد التنفيذ.")
        if before_explicit_complete is not None:
            await before_explicit_complete()
        acknowledgement = await call_salla(db, owner, "POST", f"/orders/{internal_id}/status",
                         json={"slug": "completed"}, single_post_attempt=True)
        if not isinstance(acknowledgement, dict) or acknowledgement.get("success") is not True:
            raise legacy.ShippingLabelError("completion_status_unconfirmed",
                "لم تؤكد سلة نجاح تحديث الحالة؛ أعد التحقق من الطلب.", status_code=502)
        order = {**order, "status": {"slug": "completed"}, "status_slug": "completed"}
        status = "completed"
    if completed_only and status == "in_progress":
        raise legacy.ShippingLabelError("carrier_order_in_progress", "لم تؤكد سلة تنفيذ الطلب بعد.")
    printable = {"completed"} if completed_only else {"completed", *_DONE}
    if status not in printable and not (issue and not completed_only and status == "in_progress"):
        raise legacy.ShippingLabelError("order_status_not_completed", "حالة الطلب الحالية لا تسمح بالطباعة.")
    changed = False
    status_error = None
    if issue and not completed_only and status == "in_progress":
        try:
            await call_salla(db, owner, "POST", f"/orders/{internal_id}/status",
                             json={"slug": "completed"}, single_post_attempt=True)
            changed = True
        except (SallaError, TimeoutError, ConnectionError, httpx.HTTPError) as exc:
            status_error = exc
        latest = await _order(db, owner, internal_id, number)
        if latest is not None:
            order = latest

    attempts = _READ_ATTEMPTS_AFTER_STATUS if issue else 1
    result = {}
    carrier = {}
    order_internal = False
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(0.55)
            latest = await _order(db, owner, internal_id, number)
            if latest is not None:
                order = latest
        if _order_status(order) not in printable:
            continue
        carrier = _order_label_carrier(order, strict=completed_only)
        if completed_only:
            order_internal = legacy._mobile_store_courier(carrier.get("company_name"), carrier.get("company_code"))
            if order_internal:
                # A named current merchant courier needs no shipment/AWB lookup.
                result = {}
                break
        result = await _shipment(db, owner, internal_id, order, strict=completed_only, baseline=baseline)
        if result and (legacy._is_store_courier(result) or _printable(result)["ready"]):
            break
        if not result and not completed_only and legacy._is_store_courier({
            "courier_name": carrier.get("company_name"), "meta": {"app_id": carrier.get("company_code")},
        }):
            break

    if _order_status(order) not in printable:
        raise legacy.ShippingLabelError(
            "order_status_not_completed",
            "لم تؤكد سلة حالة «تم التنفيذ»؛ لم تتم الطباعة أو إنشاء بوليصة بديلة.",
            status_code=502 if status_error else 409,
        )

    if completed_only:
        provider_carrier = {"name": legacy._text(carrier.get("company_name")),
                            "code": legacy._text(carrier.get("company_code"))}
        row_internal = _mobile_courier_row(result)
        local_carrier = legacy._mobile_label_carrier(baseline or {})
        if (order_internal and not any(legacy._mobile_generation_carrier(baseline or {}).values())
                and not legacy._store_label_carriers_match(local_carrier, provider_carrier)):
            # A legacy display cannot overrule the freshly fetched order's city
            # or associate a former driver's assignment with its new document.
            local_carrier = {"name": "", "code": ""}
        local_internal = legacy._mobile_store_courier(local_carrier["name"], local_carrier["code"])
        if not local_internal and any(local_carrier.values()) and any(provider_carrier.values()):
            if legacy._label_carriers_match(local_carrier, provider_carrier) is False:
                raise legacy._stale_label()
        if local_internal:
            # Canonical store courier beats old embedded external shipments
            # only when the provider still independently confirms this carrier.
            if not any(provider_carrier.values()) and row_internal:
                snap = _printable(result)
                row_code = legacy._text(snap["courier_code"])
                if local_carrier["code"] == "0" and (result.get("meta") or {}).get("app_id") in (0, "0"):
                    row_code = "0"
                provider_carrier = {"name": legacy._text(snap["courier_name"]), "code": row_code}
            elif not order_internal:
                raise legacy._stale_label()
            document_order = await _with_verified_destination(
                db, owner, number, internal_id, order, result if row_internal else None, courier=provider_carrier["name"],
            )
            # Only the explicit action tolerates its own completed-status
            # webhook clock; its outer material snapshot fences the document.
            return await legacy._internal_delivery_document(
                db, owner, number, document_order, baseline, provider_carrier,
                allow_completion_observation=explicit_complete)
        if (row_internal or order_internal) and (local_carrier["name"] or local_carrier["code"] not in {"", "0"}):
            raise legacy._stale_label()
        if result and any(provider_carrier.values()):
            snap = _printable(result)
            row_carrier = {"name": legacy._text(snap.get("courier_name")), "code": legacy._text(snap.get("courier_code"))}
            if order_internal != row_internal or legacy._label_carriers_match(provider_carrier, row_carrier) is False:
                raise legacy._stale_label()
        if result and db is not None:
            current_id = legacy._text(legacy._label_metadata(baseline).get("shipment_id")
                                      or (baseline or {}).get("salla_shipment_id"))
            # With no local/order carrier or current ID, _shipment admits a
            # merchant child only through its unique complete current-list proof.
            # Keep the shared cancellation, supersession and freshness guards.
            recovered_internal_child = bool(row_internal and not current_id
                and not any(local_carrier.values()) and not any(provider_carrier.values()))
            await legacy._persist_verified_snapshot(
                db, owner, number, _printable(result), baseline=baseline, persist=False,
                allow_current_internal_child=recovered_internal_child,
            )
    observed = {"observed_order_status": _order_status(order)} if _order_status(order) in _DONE else {}
    if not result:
        merchant_courier = order_internal if completed_only else legacy._is_store_courier({
            "courier_name": carrier.get("company_name"),
            "meta": {"app_id": carrier.get("company_code")},
        })
        if merchant_courier:
            store = await legacy._store_identity(db, owner)
            print_order = await _with_verified_destination(db, owner, number, internal_id, order, courier=carrier.get("company_name"))
            print_data = legacy._store_courier_print_data(number, print_order, {}, store)
            print_data["courier_name"] = carrier.get("company_name") or "مندوب المتجر"
            return {**observed, "ok": True, "source": "mezan", "ready": True,
                    "label_type": "store_courier", "order_status_completed": True,
                    "order_status_changed": changed, "shipment_id": None,
                    "courier_name": print_data["courier_name"], "label_url": None,
                    "tracking_number": None, "shipping_number": None,
                    "print_data": print_data, "status": "store_courier",
                    "message": "تم تجهيز بوليصة مندوب المتجر."}

    if result and (row_internal if completed_only else legacy._is_store_courier(result)):
        store = await legacy._store_identity(db, owner)
        print_order = await _with_verified_destination(db, owner, number, internal_id, order, result, courier=result.get("courier_name"))
        print_data = legacy._store_courier_print_data(number, print_order, result, store)
        return {**observed, "ok": True, "source": "mezan", "ready": True,
                "label_type": "store_courier", "order_status_completed": True,
                "order_status_changed": changed, "shipment_id": legacy._text(result.get("id")),
                "courier_name": print_data["courier_name"], "label_url": None,
                "tracking_number": None, "shipping_number": None,
                "print_data": print_data, "status": "store_courier",
                "message": "تم تجهيز بوليصة مندوب المتجر."}

    snap = _printable(result)
    return {**observed, "ok": True, "source": "salla", "order_status_completed": True,
            "order_status_changed": changed, **snap,
            "message": (
                "بوليصة شركة الشحن الرسمية جاهزة للطباعة." if snap["ready"]
                else "الشحنة موجودة في سلة، لكن ملف البوليصة لم يصل بعد." if snap["shipment_id"]
                else "لم تُرجع سلة شحنة صادرة بعد؛ لم تتم طباعة بوليصة غير موجودة."
            )}
