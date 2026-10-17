"""Ephemeral, read-only completion proof for pre-outbox assembly workflows.

Saved label availability is a local projection policy, not Salla completion
evidence. Each completed read needs a fresh exact Salla order observation. Only
overlapping readers share work; completed results are never retained as authority.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from urllib.parse import quote

import httpx

from accounting_atomic import SessionDatabase
from accounting_write_control import AccountingDatabase
from assembly_status_policy import canonical_status
from order_engine import shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING, extract_shipping, projected_shipping

READBACK_TIMEOUT_SECONDS = 9.0
_flights = {}
_WORKFLOW_FIELDS = (
    "user_id", "order_number", "stage", "completion_mode", "review_completion_operation_id",
    "items", "operational_items", "fulfillment_decision", "assembly_status",
    "assembly_ready_piece_count", "assembly_total_piece_count", "preparation_receipt_status",
    "shipping_print_batch_id", "experiment_mode", "experiment_delivery_flow",
    "shipping_status_epoch", "store_courier_assignee_id", "store_courier_assignee_name",
    "store_delivery_assignment_id",
)
_SOURCE_FIELDS = (
    "user_id", "order_number", "order_status", "order_status_slug", "g47_salla_snapshot",
    CURRENT_SHIPPING, *shipping._LABEL_ROOT_FIELDS,
    "shipping_address", "shipping_address_raw", "shipping_city", "shipping_district",
    "shipping_street", "shipping_national_address", "shipping_short_address",
    "shipping_postal_code", "shipping_building_number", "shipping_additional_number",
    "shipping_country", "shipping_latitude", "shipping_longitude",
)
_RAW_FIELDS = (
    "id", "reference_id", "status", "status_slug", "items", "shipping_address", "payment_method",
    "payment_status", "shipping", "shipments", "shipping_company", "shipping_company_code",
)


def _select(value, keys):
    # Preserve absent vs explicitly null fields in the fence.
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _courier(carrier):
    return shipping._is_store_courier({"company": carrier.get("company_name"),
                                      "meta": {"app_id": carrier.get("company_code")}})


async def _capture(db, owner, number):
    workflow = await db.order_review_workflows.find_one({"user_id": owner, "order_number": number})
    if not workflow or workflow.get("assembly_status") != "completed":
        raise shipping.ShippingLabelError("assembly_completion_required", "أكمل جميع منتجات الطلب في التجميع والعنونة أولًا.")
    if "assembly_delivery" in workflow:
        return None
    source = await db.unified_orders.find_one({"user_id": owner, "order_number": number})
    if not source:
        raise shipping.ShippingLabelError("order_not_found", "الطلب غير موجود في ميزان.", status_code=404)
    if canonical_status(source) not in {"in_progress", "completed"}:
        raise shipping.ShippingLabelError("assembly_canonical_status_blocked", "حالة الطلب الحالية غير مؤكدة أو لا تسمح بالطباعة.")
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    internal_id = shipping._text(raw.get("id"))
    if not internal_id:
        raise shipping.ShippingLabelError("salla_order_id_missing", "حدّث بيانات الطلب من سلة للتحقق من معرّفه.")
    current = projected_shipping(source) or extract_shipping(raw) or {}
    fence = {"workflow": _select(workflow, _WORKFLOW_FIELDS),
             "source": _select(source, _SOURCE_FIELDS), "raw": _select(raw, _RAW_FIELDS)}
    # Receipt invalidation precedes the asynchronous current-shipment projection.
    cancelled = await db.salla_shipping_label_inbox.find_one({"user_id": owner,
        "$or": [{"order_number": number}, {"order_id": internal_id}],
        "observation.shipment_id": shipping._text(current.get("shipment_id")),
        "observation.status": {"$in": list(shipping._CANCELLED)}}, {"_id": 1})
    fence["cancelled"] = bool(cancelled)
    return {"fence": fence, "internal_id": internal_id, "shipping": current}


def _root_database(db):
    root = db.current() if isinstance(db, AccountingDatabase) else db
    if isinstance(root, SessionDatabase):
        raise shipping.ShippingLabelError("completion_readback_scope_conflict", "أعد التحقق من الطلب خارج عملية الحفظ الحالية.")
    return root


async def _snapshot(db, owner, number, *, expected=None):
    # A fresh read-only Mongo snapshot keeps workflow/source/receipt observations
    # consistent without incrementing operational owner revisions. Never hold a
    # Mongo snapshot across provider IO or reuse an existing transaction snapshot.
    root = _root_database(db)
    async with await root.client.start_session(snapshot=True) as session:
        scoped = SessionDatabase(root, session)
        scoped._owner = owner
        current = await _capture(scoped, owner, number)
        if expected is None:
            return current
        if current != expected:
            raise shipping._stale_label()
        if _courier(current["shipping"]):
            return {"ready": False, "label_url": None, "label_type": "store_courier", "print_data": None}
        return await shipping.read_saved_external_shipping_label(scoped, owner, number)


def _assert_provider_identity(order, internal_id, number):
    if (not isinstance(order, dict) or shipping._text(order.get("id")) != internal_id
            or shipping._text(order.get("reference_id")) != number):
        raise shipping.ShippingLabelError("salla_order_reference_mismatch", "أعادت سلة طلبًا مختلفًا؛ أوقفت الطباعة.")
    if not shipping._order_is_completed(order):
        raise shipping.ShippingLabelError("order_status_not_completed", "لم تؤكد سلة تم التنفيذ؛ الطباعة متوقفة.")


def _assert_provider_shipping(order, current):
    # Missing shipping in a light response does not initiate a shipment lookup.
    # Positive contradictions, however, cannot authorize the cached old label.
    if _courier(current):
        # Old external shipments do not define the current store-courier route.
        # Keep the existing explicit courier document path and its assignment.
        observed = extract_shipping({key: order.get(key) for key in (
            "shipping", "shipping_company", "shipping_company_code")}) or {}
    else:
        rows = order.get("shipments")
        rows = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else [order.get("shipment")]
        outbound = [row for row in rows if isinstance(row, dict)
                    and shipping._status(row.get("type") or row.get("shipment_type")) not in
                    {"return", "return_shipment", "reverse"}]
        current_id = shipping._text(current.get("shipment_id"))
        if any(current_id and shipping._text(row.get("id")) == current_id
               and shipping._status(row.get("status")) in shipping._CANCELLED for row in outbound):
            raise shipping._stale_label()
        active_ids = {shipping._text(row.get("id")) for row in outbound
                      if shipping._text(row.get("id")) and shipping._status(row.get("status")) not in shipping._CANCELLED}
        if len(active_ids) > 1:
            raise shipping._stale_label()
        observed = extract_shipping(order) or {}
    for key in ("shipment_id", "tracking_number"):
        if observed.get(key) and current.get(key) and str(observed[key]) != str(current[key]):
            raise shipping._stale_label()
    left = {"code": shipping._text(current.get("company_code")), "name": shipping._text(current.get("company_name"))}
    right = {"code": shipping._text(observed.get("company_code")), "name": shipping._text(observed.get("company_name"))}
    if shipping._label_carriers_match(left, right) is False:
        raise shipping._stale_label()
    if observed.get("status") in shipping._CANCELLED | shipping._PENDING:
        raise shipping._stale_label()


async def _read(db, owner, number, before):
    try:
        response = await shipping.call_salla(db, owner, "GET", "/orders/" + quote(before["internal_id"], safe=""))
    except (shipping.SallaError, httpx.HTTPError) as exc:
        raise shipping.ShippingLabelError("completion_readback_unavailable", "تعذّر التحقق من حالة الطلب في سلة؛ أعد تحديث الحالة.", status_code=503) from exc
    order = response.get("data") if isinstance(response, dict) else None
    _assert_provider_identity(order, before["internal_id"], number)
    _assert_provider_shipping(order, before["shipping"])
    # The final snapshot is the local read linearization point. Its label and
    # cancellation receipt belong to the same observation as the material fence.
    label = await _snapshot(db, owner, number, expected=before)
    ready = label.get("ready") is True
    return {**label, "order_number": number, "assembly_completion_confirmed": True,
            "order_completion_status": "confirmed", "order_status_completed": True,
            "label_status": "available" if ready else "pending", "requires_attention": False,
            "error_code": None, "error_message": None, "ready": ready}


async def _join(db, owner, number):
    before = await _snapshot(db, owner, number)
    if before is None:
        return None
    digest = hashlib.sha256(json.dumps(before, sort_keys=True, default=str).encode()).hexdigest()
    # Different request wrappers around the same root database share only the
    # same merchant/order/fence; their caller-owned final reads remain separate.
    key = (id(_root_database(db)), owner, number, digest)
    entry = _flights.get(key)
    if entry is None or entry["task"].done():
        task = asyncio.create_task(asyncio.wait_for(_read(db, owner, number, before), READBACK_TIMEOUT_SECONDS))
        entry = {"task": task, "waiters": 0}
        _flights[key] = entry
    entry["waiters"] += 1
    try:
        result = await asyncio.shield(entry["task"])
        # A joined result is not reusable authority: this caller checks the
        # current local fence and cancellation receipt just before returning.
        label = await _snapshot(db, owner, number, expected=before)
        if any(result.get(key) != value for key, value in label.items()):
            raise shipping._stale_label()
        return deepcopy(result)
    finally:
        entry["waiters"] -= 1
        if entry["waiters"] == 0:
            if _flights.get(key) is entry:
                del _flights[key]
            if not entry["task"].done():
                entry["task"].cancel()
            try:
                await entry["task"]
            except (asyncio.CancelledError, Exception):
                # Every waiter already receives its own original exception.
                pass


async def read_legacy_completion(db, user_id, order_number):
    """Return fresh ephemeral legacy proof, or None when an outbox exists.

    No workflow, source, owner revision or outbox writes are performed. The
    authenticated Salla transport retains its existing token-refresh policy.
    """
    owner, number = shipping._text(user_id), shipping._text(order_number)
    if not owner or not number:
        raise shipping.ShippingLabelError("order_number_required", "رقم الطلب والمالك مطلوبان.", status_code=400)
    try:
        return await asyncio.wait_for(_join(db, owner, number), READBACK_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise shipping.ShippingLabelError("completion_readback_timeout", "تعذّر التحقق من سلة خلال المهلة؛ أعد تحديث الحالة.", status_code=504) from exc
