"""Read one legacy courier's order QR without restoring an archived shipment.

The native activity/comment distinction and observed carrier-change template
are deliberately narrower than the display history parser. No state is repaired
here: two complete provider lists and the final source fence only authorize this
request's ID-free document.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import unicodedata

from assembly_status_policy import canonical_status
from operational_delivery_evidence import _instant
from salla_shipping import CURRENT_SHIPPING
from store_delivery_domain import normalize_city, normalize_shipping_carrier


_PREFIX = "تم تعديل بيانات الشحن من iMile للتوصيل إلى: "
_ACTIVE = {"created", "ready", "shipping_ready", "pending", "processing"}
_INACTIVE = {"cancelled", "canceled", "void", "deleted", "returned", "refunded", "delivered"}
_SOURCE_FIELDS = (
    "user_id", "order_number", "order_id", "salla_order_id", "order_status", "order_status_slug",
    "raw_by_source.salla_direct", "g47_salla_snapshot", CURRENT_SHIPPING,
    "shipping_company", "shipping_company_code", "salla_shipment_id", "shipping_status", "shipment_status",
    "tracking_number", "tracking_url", "shipping_number", "shipping_label_url",
    "shipping_address_raw", "shipping_address_source_path", "salla_address_verified_at",
    "salla_address_provider_updated_at", "is_gift", "gift", "gift_order", "order_type",
)
_WORKFLOW_FIELDS = ("stage", "assembly_status", "shipping_status_epoch", "store_courier_assignee_id",
                    "store_courier_assignee_name", "store_delivery_assignment_id")


def _fail(code="courier_history_unproven"):
    from .shipping_label_service import ShippingLabelError
    raise ShippingLabelError(code, "تعذّر إثبات المندوب والعنوان الحاليين من سجل سلة؛ لم تُفتح الشحنة المستبعدة.", status_code=409)


def _text(value):
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 300:
        _fail()
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        _fail()
    return value


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        _fail()
    value = str(value)
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        _fail()
    return value


def _reference(value, expected):
    return value is None or value == "" or _id(value) == _id(expected)


def _time(value):
    try:
        return _instant(value)
    except (TypeError, ValueError, KeyError):
        _fail()


def eligible_baseline(baseline):
    row = baseline or {}
    current = row.get(CURRENT_SHIPPING) or {}
    retired = current.get("superseded_shipment_ids")
    return bool(isinstance(current, dict)
        and current.get("source_kind") == "order" and current.get("event_name") == "order.snapshot"
        and current.get("source_path") == "delivery_method" and current.get("company_name") == "shipping"
        and current.get("company_code") in (None, "") and row.get("shipping_company") in (None, "", "shipping")
        and row.get("shipping_company_code") in (None, "")
        and all(current.get(key) in (None, "") for key in ("shipment_id", "tracking_number", "tracking_url", "label_url"))
        and all(row.get(key) in (None, "") for key in ("salla_shipment_id", "tracking_number", "shipping_number", "tracking_url", "shipping_label_url"))
        and not any(str(value or "").casefold() in _INACTIVE for value in (
            current.get("status"), row.get("shipping_status"), row.get("shipment_status")))
        and isinstance(retired, list) and len(retired) == 1)


async def _source(db, owner, number):
    return await db.unified_orders.find_one({"user_id": owner, "order_number": number},
        {"_id": 1, **dict.fromkeys(_SOURCE_FIELDS, 1)})


def _source_valid(source, owner, number):
    from . import shipping_label_service as shipping
    raw = ((source or {}).get("raw_by_source") or {}).get("salla_direct") or {}
    watermark = (source or {}).get("g47_salla_snapshot") or {}
    if (not source or source.get("user_id") != owner or source.get("order_number") != number
            or not eligible_baseline(source) or canonical_status(source) != "completed"
            or any(watermark.get(key) for key in ("cancelled", "component_pending", "requires_authoritative_refresh"))
            or shipping._text(raw.get("reference_id")) != number):
        _fail()
    identity = _id(source.get("order_id"))
    if _id(raw.get("id")) != identity or source.get("salla_order_id") not in (None, "", identity, int(identity)):
        _fail()
    _id(source[CURRENT_SHIPPING]["superseded_shipment_ids"][0])
    return identity


async def _pending_or_cancelled(db, owner, number, identity, child_id):
    references = [{"order_number": number}, {"order_id": {"$in": [identity, int(identity)]}}]
    scope = {"user_id": owner, "$or": references}
    collection = db.salla_shipping_label_inbox
    cancelled = await collection.find_one({**scope,
        "observation.shipment_id": {"$in": [child_id, int(child_id)]},
        "observation.status": {"$in": sorted(_INACTIVE - {"delivered"})}}, {"_id": 1})
    # An unapplied verified observation is not permission to print through the
    # previous generation. Unknown/missing clocks cannot establish it is older.
    pending = await collection.find_one({**scope, "state": "pending"}, {"_id": 1})
    if cancelled or pending:
        _fail("shipping_snapshot_changed")


@dataclass(frozen=True)
class HistoryContext:
    owner: str
    number: str
    identity: str
    child_id: str
    source: dict[str, Any]
    baseline: dict[str, Any]
    workflow: dict[str, Any]


@dataclass(frozen=True)
class HistoryCourierProof:
    context: HistoryContext
    carrier: dict[str, str]
    document_order: dict[str, Any]
    store: dict[str, Any]


async def capture_context(db, owner, number, baseline):
    if db is None or not eligible_baseline(baseline):
        return None
    from . import shipping_label_service as shipping
    from fulfillment_carrier_label import _require_print_completed_workflow
    source = await _source(db, owner, number)
    # The explicit completion action retains its existing authority for a
    # valid in-progress order. This read-only recovery only owns completed ones.
    if canonical_status(source) == "in_progress" and not ((source or {}).get("g47_salla_snapshot") or {}).get("cancelled"):
        return None
    identity = _source_valid(source, owner, number)
    if await shipping._label_baseline(db, owner, number) != baseline:
        _fail("shipping_snapshot_changed")
    workflow = await _require_print_completed_workflow(db, user_id=owner, order_number=number)
    context = HistoryContext(owner, number, identity, _id(source[CURRENT_SHIPPING]["superseded_shipment_ids"][0]),
        deepcopy(source), deepcopy(baseline), {key: deepcopy(workflow.get(key)) for key in _WORKFLOW_FIELDS})
    await _pending_or_cancelled(db, owner, number, identity, context.child_id)
    return context


def _complete_page(response):
    if not isinstance(response, dict) or response.get("success") is not True:
        _fail()
    rows, pagination = response.get("data"), response.get("pagination")
    if not isinstance(rows, list) or not isinstance(pagination, dict):
        _fail()
    total, pages, current = (pagination.get(key) for key in ("total", "totalPages", "currentPage"))
    if (any(type(value) is not int for value in (total, pages, current))
            or pages != 1 or current != 1 or total != len(rows) or not 0 < total < 100
            or pagination.get("count", total) != total):
        _fail()
    links = pagination.get("links")
    if links and (not isinstance(links, dict) or links.get("next")):
        _fail()
    identities = [_id(row.get("id")) if isinstance(row, dict) else _fail() for row in rows]
    if len(set(identities)) != len(identities):
        _fail()
    return deepcopy(rows)


async def _shipment_list(db, context, *, selected_path=None):
    from .amasi_shipping_compat import _get
    paths = [selected_path] if selected_path else [f"/orders/{context.identity}/shipments", "/shipments"]
    for path in paths:
        params = {"per_page": 100, "page": 1}
        if path == "/shipments":
            params["order_id"] = context.identity
        response = await _get(db, context.owner, path, params=params)
        if response is None:
            continue
        rows = _complete_page(response)
        if len(rows) != 1:
            _fail()
        return path, rows[0]
    _fail("courier_history_unavailable")


def _child(row, context):
    if (_id(row.get("id")) != context.child_id or _id(row.get("order_id")) != context.identity
            or row.get("type") != "shipment" or row.get("status") not in _ACTIVE):
        _fail()
    for value in (row.get("order_reference_id"), row.get("reference_id")):
        if not _reference(value, context.number):
            _fail()
    nested = row.get("order")
    if nested is not None:
        if not isinstance(nested, dict):
            _fail()
        if nested.get("id") is not None and _id(nested["id"]) != context.identity:
            _fail()
        if any(not _reference(nested.get(key), context.number) for key in ("reference_id", "order_number")):
            _fail()
    name = _text(row.get("courier_name"))
    if not name.startswith("مندوب ") or name in {"مندوب المتجر", "مندوب المحل"}:
        _fail()
    _id(row.get("courier_id"))
    meta = row.get("meta")
    if not isinstance(meta, dict) or type(meta.get("app_id")) not in (int, str) or meta["app_id"] not in (0, "0"):
        _fail()
    for key in ("company_name", "shipping_company_name", "external_company_name"):
        if row.get(key) not in (None, "") and normalize_shipping_carrier(_text(row[key])) != normalize_shipping_carrier(name):
            _fail()
    return name


def _history(rows, order, context, courier):
    from .amasi_shipping_compat import _order_status
    version = _time(order.get("updated_at"))
    now = datetime.now(timezone.utc)
    current = context.source[CURRENT_SHIPPING]
    if not _time(current.get("carrier_updated_at")) <= version <= now:
        _fail()
    for value in (current.get("provider_updated_at"), (context.source.get("g47_salla_snapshot") or {}).get("source_updated_at")):
        if value is not None and _time(value) > version:
            _fail()
    ordered = []
    for row in rows:
        if row.get("type") != "activity":
            _fail()
        if row.get("order_id") is not None and _id(row["order_id"]) != context.identity:
            _fail()
        if any(not _reference(row.get(key), context.number) for key in ("reference_id", "order_number", "order_reference_id")):
            _fail()
        actor = row.get("created_by")
        if not isinstance(actor, dict) or not _text(actor.get("name")):
            _fail()
        _id(actor.get("id"))
        at = _time(row.get("created_at"))
        note = "" if row.get("note") == "" else _text(row.get("note"))
        status = _text(row.get("status"))
        customized = row.get("customized")
        if customized is not None and (not isinstance(customized, dict) or customized.get("name") != status):
            _fail()
        if at > version or row.get("date") is not None and _time(row["date"]) != at:
            _fail()
        ordered.append((at, note, row))
    ordered.sort(key=lambda value: value[0])
    if len({row[0] for row in ordered}) != len(ordered):
        _fail()
    matches = [index for index, (_, note, _) in enumerate(ordered) if note == _PREFIX + courier]
    if len(matches) != 1:
        _fail()
    changed = matches[0]
    if ordered[changed][0] > _time(current.get("carrier_updated_at")):
        _fail()
    for index, (_, note, row) in enumerate(ordered):
        if index == changed:
            continue
        if any(word in note.casefold() for word in ("الشحن", "عنوان", "شحنة", "shipment", "shipping", "carrier", "courier")):
            _fail()
        if index > changed:
            # Only native status activities may follow this sole reassignment.
            # Unknown actions/notes never become evidence of "nothing changed".
            status = row.get("status")
            state = _order_status({"status": status})
            if state not in {"in_progress", "completed"} or note not in ("", status):
                _fail()
            customized = row.get("customized") or {}
            if customized.get("slug") and _order_status({"status": customized["slug"]}) != state:
                _fail()
    if changed == len(ordered) - 1 or _order_status({"status": ordered[-1][2].get("status")}) != "completed":
        _fail()
    if _order_status(order) != "completed":
        _fail()


async def _destination(db, order, child, context, courier):
    from . import amasi_shipping_compat as compat
    from . import shipping_label_service as shipping
    from .customer_history import normalize_saudi_mobile
    clean = deepcopy(order)
    clean.update(shipments=[], shipment=None)
    clean.pop("_mezan_current_shipping_address", None)
    if isinstance(clean.get("shipping"), dict):
        clean["shipping"] = {key: value for key, value in clean["shipping"].items() if key not in {"shipment", "shipments"}}
    recipient = shipping.resolve_salla_recipient(clean) or {}
    saved = deepcopy((context.source.get("raw_by_source") or {}).get("salla_direct") or {})
    saved.update(shipments=[], shipment=None)
    if isinstance(saved.get("shipping"), dict):
        saved["shipping"] = {key: value for key, value in saved["shipping"].items() if key not in {"shipment", "shipments"}}
    saved_recipient = shipping.resolve_salla_recipient(saved) or {}
    if (compat._separate(clean, recipient)
            or compat._separate(saved, saved_recipient)
            or any(context.source.get(key) or clean.get(key) or saved.get(key) for key in ("is_gift", "gift", "gift_order"))
            or clean.get("source") == "buy_as_gift"):
        _fail("recipient_address_unavailable")
    destination = child.get("ship_to")
    customer = clean.get("customer") or {}
    if not isinstance(destination, dict) or not isinstance(customer, dict):
        _fail("recipient_address_unavailable")
    customer_name = _text(customer.get("full_name") or customer.get("name"))
    child_name = _text(destination.get("name") or destination.get("full_name"))
    phone = normalize_saudi_mobile(customer.get("mobile") or customer.get("phone"))
    child_phone = normalize_saudi_mobile(destination.get("phone") or destination.get("mobile"))
    address = shipping._delivery_address(destination)
    if (" ".join(customer_name.casefold().split()) != " ".join(child_name.casefold().split())
            or not phone or phone != child_phone or not shipping._street_destination(address)
            or normalize_city(address.get("city")) != normalize_city(courier.removeprefix("مندوب "))):
        _fail("recipient_address_unavailable")
    # The light order may omit its destination. Only this freshly corroborated
    # child can then supply it, as one unit. Even a previously verified saved
    # address might belong to the archived carrier generation.
    current = shipping._order_delivery_address(clean)
    if shipping._street_destination(current) and normalize_city(current.get("city")) != normalize_city(courier.removeprefix("مندوب ")):
        _fail("recipient_address_unavailable")
    if not shipping._street_destination(current):
        clean["_mezan_current_shipping_address"] = address
    return clean


async def verify_document(scoped, owner, number, order, baseline, carrier, proof):
    from . import shipping_label_service as shipping
    from fulfillment_carrier_label import _require_print_completed_workflow
    if (not isinstance(proof, HistoryCourierProof) or (owner, number) != (proof.context.owner, proof.context.number)
            or proof.document_order != order or proof.carrier != carrier or proof.context.baseline != baseline):
        _fail("shipping_snapshot_changed")
    context = proof.context
    source = await _source(scoped, owner, number)
    _source_valid(source, owner, number)
    if source != context.source or await shipping._label_baseline(scoped, owner, number) != baseline:
        _fail("shipping_snapshot_changed")
    workflow = await _require_print_completed_workflow(scoped, user_id=owner, order_number=number)
    if {key: workflow.get(key) for key in _WORKFLOW_FIELDS} != context.workflow:
        _fail("shipping_snapshot_changed")
    await _pending_or_cancelled(scoped, owner, number, context.identity, context.child_id)


async def recover_document(db, order, baseline, context):
    from . import amasi_shipping_compat as compat
    from . import shipping_label_service as shipping
    if context is None:
        return None
    if (_id(order.get("id")) != context.identity or str(order.get("reference_id")) != context.number
            or compat._order_status(order) != "completed" or compat._order_label_carrier(order, strict=True)):
        _fail()
    first_order = deepcopy(order)
    path, child = await _shipment_list(db, context)
    courier = _child(child, context)
    response = await compat._get(db, context.owner, f"/orders/{context.identity}/histories", params={"page": 1, "per_page": 100})
    _history(_complete_page(response), order, context, courier)
    document_order = await _destination(db, order, child, context, courier)
    store = await shipping._store_identity(db, context.owner)
    _, latest_child = await _shipment_list(db, context, selected_path=path)
    _child(latest_child, context)
    if latest_child != child:
        _fail("shipping_snapshot_changed")
    latest_order = await compat._order(db, context.owner, context.identity, context.number)
    if latest_order != first_order:
        _fail("shipping_snapshot_changed")
    carrier = {"name": courier, "code": "0"}
    proof = HistoryCourierProof(context, carrier, deepcopy(document_order), deepcopy(store))
    return await shipping._internal_delivery_document(db, context.owner, context.number,
        document_order, baseline, carrier, history_proof=proof)
