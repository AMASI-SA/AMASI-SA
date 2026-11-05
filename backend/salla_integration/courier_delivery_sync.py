"""Acquire a bounded, version-bound native shipment and carrier delivery timeline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from operational_courier_transitions import (
    CARRIER_HISTORY, ORDER_HISTORY, WEBHOOK_PROOF, _state, carrier_activity_transition, native_status_transition,
    order_delivery_time,
)
from operational_delivery_evidence import DELIVERED, _inactive, _instant, _order_waybill, _shipment_time, _status, _text
from salla_shipping import CURRENT_SHIPPING, SHIPPING_FIELDS, accept_shipping, extract_shipping, same_carrier, shipping_root_fields
from .service import call_salla


ISSUE_CODES = {"operational_delivery_shipment_not_delivered", "operational_delivery_timestamp_missing",
               "operational_delivery_shipment_source_missing", "operational_prepaid_shipping_completion_timestamp_missing"}
_RETRY = "salla_courier_delivery_retry_after"
_RAW = "raw_by_source.salla_direct"
_FENCED = ("order_id", "salla_order_id", "merchant", "merchant_id", "store_id", "g47_salla_snapshot", "order_status", "order_status_slug",
           "last_salla_direct_status_source_updated_at", CURRENT_SHIPPING, *sorted(SHIPPING_FIELDS))
_PROJECTION = {"_id": 0, "user_id": 1, "order_number": 1, _RAW: 1, **dict.fromkeys(_FENCED, 1)}
_SHIPMENT_FIELDS = ("id", "order_id", "order_reference_id", "type", "status", "created_at", "updated_at",
                    "courier_id", "courier_name", "company_code", "company_name", "courier", "company",
                    "tracking_number", "tracking_id", "shipping_number", "awb", "tracking",
                    "actual_delivered_at", "delivered_at")


async def _complete_pages(db, owner, path, params, *, max_pages=20):
    """A truncated or repeated page cannot prove the absence of a later return."""
    result, identities, expected = [], set(), None
    for page in range(1, max_pages + 1):
        response = await call_salla(db, owner, "GET", path, params={**params, "page": page, "per_page": 100})
        if not isinstance(response, dict) or response.get("success") is False:
            raise ValueError("courier_native_response_invalid")
        rows, pagination = response.get("data"), response.get("pagination")
        if not isinstance(rows, list) or not isinstance(pagination, dict):
            raise ValueError("courier_native_pagination_missing")
        total, pages, current = (pagination.get(key) for key in ("total", "totalPages", "currentPage"))
        if (any(type(value) is not int for value in (total, pages, current))
                or not 0 <= total <= 300 or not 1 <= pages <= max_pages or current != page
                or expected is not None and expected != (total, pages)):
            raise ValueError("courier_native_pagination_incomplete")
        expected = total, pages
        for row in rows:
            identity = _text(row.get("id")) if isinstance(row, dict) else None
            if not identity or identity in identities:
                raise ValueError("courier_native_page_repeated")
            identities.add(identity)
            result.append(row)
        if page == pages:
            if len(result) != total:
                raise ValueError("courier_native_pagination_incomplete")
            return result
        if not rows:
            raise ValueError("courier_native_pagination_incomplete")
    raise ValueError("courier_native_pagination_incomplete")


def verified_delivery(source, shipments, histories, owner, now):
    """Build source evidence only for one original, identified native waybill."""
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    current = source.get(CURRENT_SHIPPING) or {}
    identity, number = _text(raw.get("id")), _text(source.get("order_number"))
    if len(shipments) != 1 or current.get("superseded_shipment_ids"):
        raise ValueError("courier_native_shipment_ambiguous")
    shipment = shipments[0]
    if (not isinstance(shipment, dict) or _inactive(shipment)
            or _text(shipment.get("order_id")) != identity
            or shipment.get("order_reference_id") is not None and _text(shipment["order_reference_id"]) != number
            or _status(shipment.get("status")) not in DELIVERED
            or _text(shipment.get("id")) != current.get("shipment_id")):
        raise ValueError("courier_native_shipment_mismatch")
    if any(row.get("order_id") is not None and _text(row["order_id"]) != identity for row in histories):
        raise ValueError("courier_native_history_order_mismatch")
    events = [proof for row in histories if (proof := native_status_transition(row, identity)) is not None]
    activity = [row for row in events if row.get("source") == "salla_carrier_activity"]
    transition = carrier_activity_transition(activity, current, identity)
    at = _instant(transition["at"])
    if any((_instant(event.get("at")) >= at if event.get("delivered") is not True else _instant(event.get("at")) != at)
           for event in events if event.get("source") != "salla_carrier_activity"):
        raise ValueError("courier_native_timeline_conflict")
    version = _instant(source["g47_salla_snapshot"]["source_updated_at"])
    created = _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
    shipment_created = _instant(shipment.get("created_at"))
    if shipment.get("updated_at") is not None and _instant(shipment["updated_at"]) > now:
        raise ValueError("courier_native_clock_in_future")
    if not created <= shipment_created <= _instant(transition["dispatch"]["at"]) < at <= version <= now:
        raise ValueError("courier_native_timeline_conflict")
    incoming = extract_shipping(shipment, event_name="shipment.snapshot", event_time=at)
    if (not incoming or not same_carrier(current, incoming)
            or incoming.get("tracking_number") != current.get("tracking_number")):
        raise ValueError("courier_native_shipment_mismatch")
    # This is an operational observation of the already-bound carrier. Keep its
    # approved identity key; the native numeric courier ID remains in the proof.
    incoming.update(company_code=current.get("company_code"), company_name=current.get("company_name"))
    snapshot = {key: deepcopy(shipment[key]) for key in _SHIPMENT_FIELDS if key in shipment}
    native_raw = {**raw, "shipping": {}, "shipment": None, "shipments": [snapshot]}
    dated, _ = _shipment_time(native_raw, incoming, incoming["shipment_id"], incoming["tracking_number"],
                             order_transition=lambda: at)
    if dated != at:
        raise ValueError("courier_native_timeline_conflict")
    incoming[WEBHOOK_PROOF] = {
        "source": CARRIER_HISTORY, "owner": owner, "order_id": identity, "order_number": number,
        "shipment_id": incoming["shipment_id"], "waybill": incoming["tracking_number"], "at": at.isoformat(),
        "source_updated_at": version.isoformat(), "history_complete": True,
        "events": sorted(activity, key=lambda row: row["history_id"]), "shipment": snapshot,
    }
    accepted = accept_shipping(source, incoming)
    if not accepted or accepted.get(WEBHOOK_PROOF, {}).get("ambiguous"):
        raise ValueError("courier_native_source_conflict")
    return accepted


def verified_order_delivery(source, histories, owner, now):
    """Attach an order timeline without turning an in-transit label into delivered."""
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    current = source.get(CURRENT_SHIPPING) or {}
    identity, number = _text(raw.get("id")), _text(source.get("order_number"))
    if source.get("user_id") != owner or not identity or not number:
        raise ValueError("courier_native_order_mismatch")
    events = []
    for row in histories:
        if row.get("order_id") is not None and _text(row["order_id"]) != identity:
            raise ValueError("courier_native_history_order_mismatch")
        proof = native_status_transition(row, identity)
        if proof is not None:
            events.append({"provider_order_id": identity, "order_number": number,
                           "source_event_id": _text(row.get("id") or row.get("history_id")),
                           "native_status_transition": [proof]})
    # Carrier activity has its separate authenticated shipment+actor contract.
    # This path accepts a typed order transition, independently of shipment state.
    if not any(e["native_status_transition"][0].get("delivered") is True
               and not e["native_status_transition"][0].get("source") for e in events):
        raise ValueError("courier_native_order_transition_missing")
    at = order_delivery_time(raw, source, events, now)
    _order_waybill(raw, current, current.get("shipment_id"), current.get("tracking_number"), at)
    created = _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
    if created > at:
        raise ValueError("courier_native_timeline_conflict")
    result = deepcopy(current)
    result[WEBHOOK_PROOF] = {"source": ORDER_HISTORY, "owner": owner, "order_id": identity, "order_number": number,
        "shipment_id": current["shipment_id"], "waybill": current["tracking_number"], "at": at.isoformat(),
        "source_updated_at": source["g47_salla_snapshot"]["source_updated_at"], "history_complete": True,
        "events": sorted(events, key=lambda row: row["source_event_id"])}
    return result


async def acquire_missing(db, owner, issues, *, started_at, limit=4):
    """Refresh identified post-opening delivery gaps, without financial/workflow hooks."""
    targets = {str(row["source_id"]) for row in issues if row.get("code") in ISSUE_CODES and row.get("source_id")}
    completed_targets = {str(row['source_id']) for row in issues
                         if row.get('code') == 'operational_prepaid_shipping_completion_timestamp_missing' and row.get('source_id')}
    if not targets:
        return
    identities = list(targets) + [int(value) for value in targets if value.isascii() and value.isdigit()]
    start, now = _instant(started_at), datetime.now(timezone.utc)
    for _ in range(min(limit, 4)):
        source = await db.unified_orders.find_one_and_update(
            {"user_id": owner, _RAW + ".id": {"$in": identities},
             **({} if completed_targets else {"order_status_slug": {"$in": ["delivered", "تم التوصيل"]}}),
             "g47_salla_snapshot.source_updated_at": {"$gte": start.isoformat()},
             "$or": [{_RETRY: {"$exists": False}}, {_RETRY: {"$lte": now}}]},
            {"$set": {_RETRY: now + timedelta(minutes=15)}}, projection=_PROJECTION)
        if not source:
            break
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        current, watermark = source.get(CURRENT_SHIPPING) or {}, source.get("g47_salla_snapshot") or {}
        identity, number = _text(raw.get("id")), _text(source.get("order_number"))
        completed = identity in completed_targets
        capture_binding = None
        try:
            status = raw.get("status")
            effective = status.get("customized") or status if isinstance(status, dict) else status
            date = raw.get("date") if isinstance(raw.get("date"), dict) else {}
            version = _instant(raw.get("updated_at") or date.get("updated") or raw.get("date_updated"))
            if (source.get("user_id") != owner or not identity or not number
                    or _text(raw.get("reference_id") or raw.get("order_number")) != number
                    or any(source.get(key) is not None and _text(source[key]) != identity for key in ("order_id", "salla_order_id"))
                    or not completed and _state(effective) is not True
                    or not completed and any(_state(source[key]) is not True for key in ("order_status", "order_status_slug") if source.get(key) is not None)
                    or any(watermark.get(key) for key in ("cancelled", "component_pending", "requires_authoritative_refresh"))
                    or not start <= version <= now or _instant(watermark.get("source_updated_at")) != version
                    or current.get("source_kind") not in {"order", "shipment"} or _inactive(current)
                    or not _text(current.get("shipment_id")) or not _text(current.get("tracking_number"))
                    or any(source[key] != value for key, value in shipping_root_fields(current).items() if key in source)
                    or current.get("superseded_shipment_ids")):
                continue
            if completed:
                from operational_completed_capture import recover_completed_capture
                async def load_shipments():
                    return await _complete_pages(db, owner, '/shipments', {'order_id': identity}, max_pages=1)
                recovered = await recover_completed_capture(db, owner, source, now, load_shipments=load_shipments)
                if recovered is not None:
                    accepted, capture_binding, capture_scope = recovered
                else:
                    from operational_order_completed_evidence import verified_completed_history
                    histories = await _complete_pages(db, owner, "/orders/" + identity + "/histories", {})
                    accepted = verified_completed_history(source, histories, owner, now)
                    try:
                        delivery = verified_order_delivery(source, histories, owner, now)
                        accepted[WEBHOOK_PROOF] = delivery[WEBHOOK_PROOF]
                    except ValueError:
                        pass
            else:
                histories = await _complete_pages(db, owner, "/orders/" + identity + "/histories", {})
                try:
                    accepted = verified_order_delivery(source, histories, owner, now)
                except ValueError:
                    shipments = await _complete_pages(db, owner, "/shipments", {"order_id": identity}, max_pages=1)
                    accepted = verified_delivery(source, shipments, histories, owner, now)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        # The inspected order, source revision, current waybill, and its clocks
        # must all survive the network reads. No source or delivery hook runs.
        selector = {"user_id": owner, "order_number": number, _RAW: raw,
                    **{key: source.get(key) for key in _FENCED}}
        if capture_binding is not None:
            from operational_completed_capture import owner_binding, recheck_capture_set
            try:
                await recheck_capture_set(db, owner, source, now, capture_binding, capture_scope)
                if await owner_binding(db, owner) != capture_binding:
                    continue
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        await db.unified_orders.update_one(selector, {"$set": {
            CURRENT_SHIPPING: accepted, **shipping_root_fields(accepted),
        }})
