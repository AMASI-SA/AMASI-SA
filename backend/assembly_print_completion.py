"""Read-only print eligibility, independent of delivery-outbox progress.

Owner policy: current local products ready + fresh Salla completed. This never
replays Ready, resets an outbox, changes provider status, or creates a shipment.
"""
import asyncio
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx

from accounting_atomic import SessionDatabase
from assembly_completion_legacy_readback import (
    _root_database, _select, _WORKFLOW_FIELDS, _SOURCE_FIELDS, _RAW_FIELDS,
    _assert_provider_identity, _assert_provider_shipping, _courier,
)
from order_engine import shipping_label_service as shipping
from salla_shipping import extract_shipping, projected_shipping


def _covers_current_products(raw, pieces):
    expected = set()
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        return False
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            return False
        identity = shipping._text(item.get("id"))
        try:
            quantity = Decimal(str(item.get("quantity")))
            if not quantity.is_finite() or quantity <= 0 or quantity > 10000 or quantity != quantity.to_integral_value():
                return False
        except (InvalidOperation, ValueError):
            return False
        if not identity or identity in seen:
            return False
        seen.add(identity)
        expected.update((identity, unit) for unit in range(1, int(quantity) + 1))
        if len(expected) > 10000:
            return False
    actual = []
    for piece in pieces:
        if piece.get("virtual_kind") == "operational":
            continue
        try:
            unit = Decimal(str(piece.get("unit_index")))
            if not unit.is_finite() or unit != unit.to_integral_value():
                return False
        except (InvalidOperation, ValueError):
            return False
        actual.append((shipping._text(piece.get("order_item_id")), int(unit)))
    return len(actual) == len(set(actual)) and set(actual) == expected


async def _capture(db, owner, number):
    from preparation_piece_operations import PIECES, PIECE_STATUS_CANCELLED, _workflow_assembly_pieces
    workflow = await db.order_review_workflows.find_one({"user_id": owner, "order_number": number})
    if not workflow or workflow.get("assembly_status") != "completed":
        return None
    pieces = await db[PIECES].find({"user_id": owner, "order_number": number,
        "status": {"$ne": PIECE_STATUS_CANCELLED}, "$or": [
            {"experiment_archived_at": {"$exists": False}}, {"experiment_archived_at": None}]
    }, {"image_b64": 0}).sort("piece_id", 1).to_list(10001)
    pieces.extend(_workflow_assembly_pieces(workflow, order_number=number))
    if not pieces or len(pieces) > 10000 or any(p.get("assembly_status") != "ready" or p.get("active_hold_id") for p in pieces):
        return None
    source = await db.unified_orders.find_one({"user_id": owner, "order_number": number})
    if not source:
        return None
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    if not _covers_current_products(raw, pieces):
        return None
    internal_id = shipping._text(raw.get("id"))
    if not internal_id or shipping._text(raw.get("reference_id")) != number:
        return None
    current = projected_shipping(source) or extract_shipping(raw) or {}
    cancelled = await db.salla_shipping_label_inbox.find_one({"user_id": owner,
        "$or": [{"order_number": number}, {"order_id": internal_id}],
        "observation.shipment_id": shipping._text(current.get("shipment_id")),
        "observation.status": {"$in": list(shipping._CANCELLED)}}, {"_id": 1})
    return {"workflow": _select(workflow, (*_WORKFLOW_FIELDS, "carrier_label_print_confirmed",
                "carrier_handoff_employee_id")),
            "source": _select(source, _SOURCE_FIELDS), "raw": _select(raw, _RAW_FIELDS),
            "pieces": pieces, "shipping": current, "cancelled": bool(cancelled), "internal_id": internal_id}


async def _snapshot(db, owner, number, *, expected=None):
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


async def _read(db, owner, number):
    before = await _snapshot(db, owner, number)
    if before is None:
        return None
    try:
        response = await shipping.call_salla(db, owner, "GET", "/orders/" + quote(before["internal_id"], safe=""))
    except (shipping.SallaError, httpx.HTTPError) as exc:
        raise shipping.ShippingLabelError("completion_readback_unavailable", "تعذّر التحقق من حالة الطلب في سلة؛ أعد التحديث.", status_code=503) from exc
    order = response.get("data") if isinstance(response, dict) else None
    _assert_provider_identity(order, before["internal_id"], number)
    _assert_provider_shipping(order, before["shipping"])
    label = await _snapshot(db, owner, number, expected=before)
    ready = label.get("ready") is True
    return {**label, "order_number": number, "assembly_completion_confirmed": True,
        "order_completion_status": "confirmed", "order_status_completed": True,
        "label_status": "available" if ready else "pending", "requires_attention": False,
        "error_code": None, "error_message": None, "ready": ready}


async def read_print_completion(db, user_id, order_number):
    try:
        return await asyncio.wait_for(_read(db, user_id, order_number), 9.0)
    except TimeoutError as exc:
        raise shipping.ShippingLabelError("completion_readback_timeout", "تعذّر التحقق من سلة خلال المهلة؛ أعد التحديث.", status_code=504) from exc
