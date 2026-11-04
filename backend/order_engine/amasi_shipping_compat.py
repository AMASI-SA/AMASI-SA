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


async def _with_verified_destination(
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
                and not any(part in {"customer", "buyer", "sender", "ship_from"} for part in source)):
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


async def carrier_label(db: Any, owner: str, order_number: str, *, issue: bool, completed_only: bool = False) -> dict[str, Any]:
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
                db, owner, number, internal_id, order, result if row_internal else None,
            )
            return await legacy._internal_delivery_document(db, owner, number, document_order, baseline, provider_carrier)
        if (row_internal or order_internal) and (local_carrier["name"] or local_carrier["code"] not in {"", "0"}):
            raise legacy._stale_label()
        if result and any(provider_carrier.values()):
            snap = _printable(result)
            row_carrier = {"name": legacy._text(snap.get("courier_name")), "code": legacy._text(snap.get("courier_code"))}
            if order_internal != row_internal or legacy._label_carriers_match(provider_carrier, row_carrier) is False:
                raise legacy._stale_label()
        if result and db is not None:
            await legacy._persist_verified_snapshot(
                db, owner, number, _printable(result), baseline=baseline, persist=False,
            )
    observed = {"observed_order_status": _order_status(order)} if _order_status(order) in _DONE else {}
    if not result:
        merchant_courier = order_internal if completed_only else legacy._is_store_courier({
            "courier_name": carrier.get("company_name"),
            "meta": {"app_id": carrier.get("company_code")},
        })
        if merchant_courier:
            store = await legacy._store_identity(db, owner)
            print_order = await _with_verified_destination(db, owner, number, internal_id, order)
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
        print_order = await _with_verified_destination(db, owner, number, internal_id, order, result)
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
