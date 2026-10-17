"""Fresh completion evidence for an inactive, blocked v2 delivery only.

This does not resume delivery. Healthy canonical reads write nothing. The sole
authorized repair is a versioned in_progress/completed alias conflict, under
the operational owner fence; it changes source status aliases only. Outbox,
inventory, workflow, component and provider state are never written here.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import HTTPException
import httpx

from accounting_atomic import SessionDatabase
from assembly_completion_delivery import source_fingerprint, workflow_fingerprint
from assembly_completion_legacy_readback import (
    _assert_provider_identity, _assert_provider_material, _assert_provider_shipping, _courier, _root_database,
)
from assembly_status_policy import canonical_status, status_values
from fulfillment_v2_routes import (
    COMPONENT_LIFECYCLES, LEGACY_COMPONENT_COHORT, assert_component_execution,
    component_provider_version, component_source_time, legacy_component_cohort,
)
from operational_atomic import operational_owner
from order_engine import shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING, extract_shipping, projected_shipping
from stock_component_consumption_service import PLANS

READBACK_TIMEOUT_SECONDS = 9.0
_CODES = {"assembly_canonical_status_blocked", "completion_source_refresh_pending"}


def _inactive(outbox):
    if (not isinstance(outbox, dict) or outbox.get("version") != 2
            or outbox.get("state") != "requires_attention"
            or outbox.get("error_code") not in _CODES
            or outbox.get("legacy_readback_only") or outbox.get("claim")):
        return False
    value = outbox.get("lease_until")
    if value in (None, ""):
        return True
    try:
        expires = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return expires.tzinfo is not None and expires <= datetime.now(timezone.utc)


def _alias_conflict(source):
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    provider = status_values(raw.get("status")) | status_values(raw.get("status_slug"))
    values = provider | status_values(source.get("order_status")) | status_values(source.get("order_status_slug"))
    return bool(provider) and values == {"in_progress", "completed"}


async def _capture(db, owner, number):
    workflow = await db.order_review_workflows.find_one({"user_id": owner, "order_number": number})
    if not workflow or workflow.get("assembly_status") != "completed":
        return None
    outbox = workflow.get("assembly_delivery")
    if not _inactive(outbox) or workflow_fingerprint(workflow) != outbox.get("workflow_fingerprint"):
        return None
    source = await db.unified_orders.find_one({"user_id": owner, "order_number": number})
    if not source or source_fingerprint(source) != outbox.get("source_fingerprint"):
        return None
    watermark = source.get("g47_salla_snapshot") or {}
    if watermark.get("component_pending") or watermark.get("requires_authoritative_refresh") or watermark.get("cancelled"):
        return None
    repair = canonical_status(source) not in {"in_progress", "completed"}
    if repair and not _alias_conflict(source):
        return None
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    internal_id = shipping._text(raw.get("id"))
    if not internal_id or shipping._text(raw.get("reference_id")) != number:
        return None
    plan = await db[PLANS].find_one({"user_id": owner, "order_id": number})
    lifecycle = await db[COMPONENT_LIFECYCLES].find_one({"user_id": owner, "order_number": number})
    settings = await db.settings.find_one({"user_id": owner}) or {}
    try:
        if plan:
            if plan.get("state") != "accepted" or plan.get("reconciliation_required"):
                return None
            await assert_component_execution(db, user_id=owner, order_number=number, plan=plan)
        elif (not lifecycle or lifecycle.get("state") != LEGACY_COMPONENT_COHORT
                or lifecycle.get("cancelled") or lifecycle.get("retry_required")
                or not await legacy_component_cohort(db, user_id=owner, order_number=number)):
            # Read existing legacy evidence; never call its audit-marker writer.
            return None
    except HTTPException:
        return None
    current = projected_shipping(source) or extract_shipping(raw) or {}
    cancelled = await db.salla_shipping_label_inbox.find_one({"user_id": owner,
        "$or": [{"order_number": number}, {"order_id": internal_id}],
        "observation.shipment_id": shipping._text(current.get("shipment_id")),
        "observation.status": {"$in": list(shipping._CANCELLED)}}, {"_id": 1})
    return {"workflow": workflow, "source": source, "plan": plan, "lifecycle": lifecycle,
            "settings": settings, "internal_id": internal_id, "shipping": current,
            "cancelled": bool(cancelled), "repair_needed": repair}


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
        if current["repair_needed"]:
            raise shipping._stale_label()
        if _courier(current["shipping"]):
            return {"ready": False, "label_url": None, "label_type": "store_courier", "print_data": None}
        return await shipping.read_saved_external_shipping_label(scoped, owner, number)


def _repair_version(order, before):
    version = component_provider_version(order)
    source = before["source"]
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    values = [(source.get("g47_salla_snapshot") or {}).get("source_updated_at"),
              raw.get("updated_at"), (raw.get("date") or {}).get("updated") if isinstance(raw.get("date"), dict) else None,
              (before["lifecycle"] or {}).get("source_updated_at")]
    supplied = [value for value in values if value not in (None, "")]
    versions = [component_source_time(value) for value in supplied]
    if not version or not versions or any(value is None for value in versions) or version < max(versions):
        return None
    return version


async def _repair_aliases(db, owner, number, order, before, *, authorize_repair=None):
    version = _repair_version(order, before)
    if version is None:
        return None
    async def repair(scoped):
        current = await _capture(scoped, owner, number)
        if current != before:
            raise shipping._stale_label()
        if authorize_repair is not None:
            # Routes provide a fresh permission/tenant check inside this same
            # transaction; denial also rolls back the owner revision increment.
            await authorize_repair(scoped)
        patch = {"order_status": "completed", "order_status_slug": "completed",
                 "raw_by_source.salla_direct.status": deepcopy(order["status"]) if "status" in order else "completed",
                 "raw_by_source.salla_direct.status_slug": "completed",
                 "raw_by_source.salla_direct.updated_at": version}
        changed = await scoped.unified_orders.update_one(
            {"_id": current["source"]["_id"], "user_id": owner, "order_number": number}, {"$set": patch})
        if changed.matched_count != 1:
            raise shipping._stale_label()
        after = await _capture(scoped, owner, number)
        expected = deepcopy(before)
        expected["source"].update(order_status="completed", order_status_slug="completed")
        expected["source"]["raw_by_source"]["salla_direct"].update(
            status=patch["raw_by_source.salla_direct.status"], status_slug="completed", updated_at=version)
        expected["repair_needed"] = False
        if after != expected:
            raise shipping._stale_label()
        return after
    return await operational_owner(_root_database(db), owner, repair)


async def _read(db, owner, number, *, authorize_repair=None):
    before = await _snapshot(db, owner, number)
    if before is None:
        return None
    try:
        response = await shipping.call_salla(db, owner, "GET", "/orders/" + quote(before["internal_id"], safe=""))
    except (shipping.SallaError, httpx.HTTPError) as exc:
        raise shipping.ShippingLabelError("completion_readback_unavailable", "تعذّر التحقق من حالة الطلب في سلة؛ أعد تحديث الحالة.", status_code=503) from exc
    order = response.get("data") if isinstance(response, dict) else None
    _assert_provider_identity(order, before["internal_id"], number)
    _assert_provider_shipping(order, before["shipping"])
    _assert_provider_material(order, (before["source"].get("raw_by_source") or {}).get("salla_direct") or {})
    if before["repair_needed"]:
        before = await _repair_aliases(db, owner, number, order, before, authorize_repair=authorize_repair)
        if before is None:
            return None
    # This independent fresh local snapshot is the response linearization point.
    label = await _snapshot(db, owner, number, expected=before)
    ready = label.get("ready") is True
    return {**label, "order_number": number, "assembly_completion_confirmed": True,
            "order_completion_status": "confirmed", "order_status_completed": True,
            "label_status": "available" if ready else "pending", "requires_attention": False,
            "error_code": None, "error_message": None, "ready": ready}


async def read_blocked_completion(db, user_id, order_number, *, authorize_repair=None):
    """Return ephemeral proof or None if this narrow recovery is inapplicable.

Never resumes an outbox or performs status/issuance POST, AWB/PDF work or resync.
Only the owner-approved versioned alias repair can write source status fields.
"""
    owner, number = shipping._text(user_id), shipping._text(order_number)
    if not owner or not number:
        raise shipping.ShippingLabelError("order_number_required", "رقم الطلب والمالك مطلوبان.", status_code=400)
    try:
        return await asyncio.wait_for(_read(db, owner, number, authorize_repair=authorize_repair), READBACK_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise shipping.ShippingLabelError("completion_readback_timeout", "تعذّر التحقق من سلة خلال المهلة؛ أعد تحديث الحالة.", status_code=504) from exc
