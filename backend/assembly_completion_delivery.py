"""Durable, read-first delivery of a committed assembly completion.

The outbox is embedded in the workflow and inserted by the assembly transaction.
Only an explicit new local completion or an authorized manual resume enrolls an
order. A persisted status-attempt marker is never reset after uncertain IO.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import hashlib
import json
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from shipping_read_budget import read_budget

from operational_atomic import operational_owner
from assembly_status_policy import canonical_status, source_status
from order_engine import shipping_label_service as shipping

WORKFLOWS = "order_review_workflows"
FIELD = "assembly_delivery"
LEASE_SECONDS = 120
TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 8
MAX_READ_ATTEMPTS = 16
READ_TIMEOUT_SECONDS = 30
GLOBAL_READ_INTERVAL = 15
OWNER_READ_INTERVAL = 60
LIMITS = "assembly_read_reconciliation_limits"
OWNER_SCAN_LIMIT = 16
QUEUE_QUERY_TIMEOUT_MS = 1000
logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc)


def pending_operation(actor_id, actor_name):
    return {"version": 1, "operation_id": uuid.uuid4().hex, "state": "pending", "attempts": 0,
            "due_at": now().isoformat(), "lease_until": "", "claim": None,
            "status_attempted": False, "awb_attempted": False, "order_confirmed": False,
            "actor_id": actor_id, "actor_name": actor_name}


def source_fingerprint(source):
    # Fence source/material changes while permitting the expected status update.
    raw = ((source or {}).get("raw_by_source") or {}).get("salla_direct") or {}
    evidence = {key: raw.get(key) for key in ("id", "reference_id", "items", "shipping_address", "payment_method", "payment_status")}
    carrier = shipping.extract_shipping({"shipping": raw.get("shipping"),
        "shipping_company": raw.get("shipping_company"), "shipping_company_code": raw.get("shipping_company_code")}) or {}
    evidence["carrier"] = {key: carrier.get(key) for key in ("company_name", "company_code")}
    shipping_source = raw.get("shipping") if isinstance(raw.get("shipping"), dict) else {}
    evidence["shipping_recipient"] = {key: shipping_source.get(key) for key in ("address", "recipient", "receiver")}
    evidence["component_pending"] = ((source or {}).get("g47_salla_snapshot") or {}).get("component_pending")
    evidence["requires_authoritative_refresh"] = ((source or {}).get("g47_salla_snapshot") or {}).get("requires_authoritative_refresh")
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()


def workflow_fingerprint(workflow):
    """Completion evidence, independent of label observations and audit revision.

    Revisions also advance for harmless metadata. Freeze the material approval,
    piece/route coverage and shipping identity instead; component/source evidence
    is additionally read under the owner fence before effects and publication.
    """
    keys = ("user_id", "order_number", "stage", "completion_mode", "review_completion_operation_id",
            "items", "operational_items", "fulfillment_decision", "assembly_status",
            "assembly_ready_piece_count", "assembly_total_piece_count", "preparation_receipt_status",
            "shipping_print_batch_id", "experiment_mode", "experiment_delivery_flow")
    return hashlib.sha256(json.dumps({key: workflow.get(key) for key in keys},
                                    sort_keys=True, default=str).encode()).hexdigest()


async def guard_effect(db, operation, *, effect=None, patch=None):
    """Current source/component fence under the same operational owner lock."""
    owner, number = operation["user_id"], operation["order_number"]
    async def check(scoped):
        current = await shipping_workflow(scoped, owner, number)
        outbox = current.get(FIELD) or {}
        source = await scoped["unified_orders"].find_one({"user_id": owner, "order_number": number})
        if (outbox.get("claim") != operation[FIELD]["claim"]
                or outbox.get("lease_until", "") <= now().isoformat()):
            raise shipping.ShippingLabelError("completion_lease_lost", "انتهت صلاحية محاولة الاستكمال.")
        allowed = {"in_progress"} if effect == "status" else {"in_progress", "completed"}
        if canonical_status(source) not in allowed:
            raise shipping.ShippingLabelError("assembly_canonical_status_blocked", "حالة الطلب الحالية غير مؤكدة أو لا تسمح بالشحن.")
        shipping_fence = {
            "epoch": current.get("shipping_status_epoch", 0),
            "shipping": await shipping._label_baseline(scoped, owner, number),
            "assignment": {key: current.get(key) for key in (
                "store_courier_assignee_id", "store_courier_assignee_name", "store_delivery_assignment_id")},
        }
        if "_shipping_fence" in operation and shipping_fence != operation["_shipping_fence"]:
            raise shipping._stale_label()
        legacy = outbox.get("legacy_readback_only") is True
        material = outbox.get("workflow_fingerprint")
        unchanged_workflow = (
            material == operation[FIELD].get("workflow_fingerprint")
            and workflow_fingerprint(current) == material
        ) if material else current.get("revision") == outbox.get("workflow_revision")
        if not legacy and (not unchanged_workflow or not source
                or source_fingerprint(source) != outbox.get("source_fingerprint")):
            raise shipping.ShippingLabelError("assembly_completion_evidence_changed", "تغيرت أدلة التجهيز؛ يلزم التحقق.")
        if not legacy:
            from fulfillment_v2_routes import assert_component_execution, allow_legacy_component_execution
            from stock_component_consumption_service import PLANS
            plan = await scoped[PLANS].find_one({"user_id": owner, "order_id": number})
            if plan:
                await assert_component_execution(scoped, user_id=owner, order_number=number, plan=plan)
            elif not await allow_legacy_component_execution(scoped, user_id=owner, order_number=number):
                raise shipping.ShippingLabelError("component_reservation_missing", "تعذر إثبات حجز المكونات.")
        if effect or patch is not None:
            selector = {
                "user_id": owner, "order_number": number, "revision": current.get("revision"),
                "assembly_status": "completed", f"{FIELD}.claim": outbox["claim"],
            }
            updates = dict(patch or {})
            if effect:
                if legacy:
                    raise shipping.ShippingLabelError("completion_effect_unconfirmed", "المحاولة السابقة غير مؤكدة؛ لن يتكرر الإرسال.")
                selector[f"{FIELD}.{effect}_attempted"] = False
                updates.update({f"{FIELD}.{effect}_attempted": True,
                                f"{FIELD}.{effect}_attempted_at": now().isoformat()})
            # Use the current revision only for atomic CAS, never as material
            # identity across provider awaits. All persistence rechecks evidence.
            updates[f"{FIELD}.workflow_revision"] = current.get("revision")
            claimed = await scoped[WORKFLOWS].update_one(selector, {"$set": updates})
            if claimed.matched_count != 1:
                raise shipping.ShippingLabelError("completion_effect_unconfirmed", "المحاولة السابقة غير مؤكدة؛ لن يتكرر الإرسال.")
        return shipping_fence
    return await operational_owner(db, owner, check)


def public_status(workflow, *, canonical=None):
    operation = workflow.get(FIELD) or {}
    confirmed = operation.get("order_confirmed") is True and canonical in {"in_progress", "completed"}
    available = confirmed and operation.get("state") == "confirmed" and workflow.get("carrier_label_ready") is True
    return {"order_number": workflow.get("order_number"),
            "assembly_completion_confirmed": workflow.get("assembly_status") == "completed",
            "order_completion_status": "confirmed" if confirmed else "pending",
            "label_status": "available" if available else "pending",
            "requires_attention": operation.get("state") == "requires_attention",
            "error_code": operation.get("error_code"),
            "ready": available, "order_status_completed": confirmed}


async def read_status(db, user_id, order_number):
    workflow = await shipping_workflow(db, user_id, order_number)
    return {**label_fields(workflow), **public_status(workflow, canonical=await source_status(db, user_id, order_number))}

def label_fields(workflow):
    return {"label_url": workflow.get("carrier_label_url"),
            "label_type": workflow.get("carrier_label_type"),
            "courier_name": workflow.get("carrier_name"),
            "shipment_id": workflow.get("carrier_shipment_id"),
            "tracking_number": workflow.get("carrier_tracking_number"),
            "print_data": workflow.get("carrier_label_print_data")}


async def shipping_workflow(db, user_id, order_number):
    from fulfillment_carrier_label import _require_print_completed_workflow
    return await _require_print_completed_workflow(db, user_id=user_id, order_number=order_number)


async def _deliver(db, operation):
    owner, number = operation["user_id"], operation["order_number"]
    # Capture before the FIRST provider await, including legacy readback. Every
    # subsequent guard compares this identity under the owner lock through CAS.
    operation["_shipping_fence"] = await guard_effect(db, operation)
    # Every attempt, including manual recovery, starts with current provider GET.
    internal_id, order = await shipping._resolve_order(db, owner, number)
    if str(order.get("reference_id")) != number or str(order.get("id")) != internal_id:
        raise shipping.ShippingLabelError("salla_order_reference_mismatch", "تعذر تأكيد هوية الطلب في سلة.")
    await guard_effect(db, operation)
    if not shipping._order_is_completed(order):
        await guard_effect(db, operation, patch={
            f"{FIELD}.order_confirmed": False, "salla_order_status": "unknown",
            "salla_order_status_verified_at": None, "carrier_label_ready": False})
        status = order.get("status") or {}
        status_values = {shipping._status(status).replace("_", " ")}
        if isinstance(status, dict) and status.get("name"):
            status_values.add(str(status["name"]).strip())
        if not status_values or not status_values.issubset({"in progress", "قيد التنفيذ"}):
            raise shipping.ShippingLabelError("assembly_order_not_ready", "الطلب ليس قيد التنفيذ في سلة.")
        if operation[FIELD].get("status_attempted"):
            raise shipping.ShippingLabelError("completion_status_unconfirmed", "تحديث الحالة غير مؤكد؛ لن يتكرر الإرسال.")
        # Fence BEFORE sending. A crash here deliberately requires readback,
        # never an automatic repeated POST, even if no bytes reached Salla.
        await guard_effect(db, operation, effect="status")
        # Salla documents no expected-status/version precondition. A Mongo
        # check cannot fence a later remote transition. Never dispatch this
        # unconditional write; a merchant must reconcile in Salla then GET.
        raise shipping.ShippingLabelError("completion_remote_precondition_unavailable",
            "تعذر ضمان تحديث مشروط لدى سلة؛ راجع الطلب في سلة ثم أعد التحقق بالقراءة.")
    await guard_effect(db, operation, patch={
        f"{FIELD}.order_confirmed": True, "salla_order_status": "completed",
        "salla_order_status_verified_at": now().isoformat()})
    # Refresh verifies status and the current active shipment before any AWB IO.
    result = await shipping.refresh_shipping_label(db, owner, number)
    if not result.get("ready") and not operation[FIELD].get("awb_attempted"):
        rows = await shipping._print_shipment_rows(db, owner, internal_id)
        active = shipping._active_outbound(rows)
        source = active[0] if active else {}
        # Preserve the provider rule: pending/tracked shipments are already
        # being issued by Salla. Only a current unissued row qualifies.
        if (source and shipping._status(source.get("status")) == "draft"
                and not shipping._tracking(source)):
            await guard_effect(db, operation, effect="awb")
            raise shipping.ShippingLabelError("completion_remote_precondition_unavailable",
                "تعذر ضمان إصدار مشروط لدى سلة؛ راجع الشحنة في سلة ثم أعد التحقق بالقراءة.")
    from fulfillment_carrier_label import _workflow_patch
    patch = _workflow_patch({**result, "order_status_completed": True}, now=now().isoformat())
    patch.update({f"{FIELD}.state": "confirmed" if result.get("ready") else (
                      "requires_attention" if operation[FIELD]["attempts"] >= MAX_ATTEMPTS else "pending"),
                  f"{FIELD}.error_code": None, f"{FIELD}.lease_until": "",
                  f"{FIELD}.claim": None,
                    f"{FIELD}.due_at": next_read_at(operation)})
    await guard_effect(db, operation, patch=patch)
    return result


def next_read_at(operation):
    count = (operation.get(FIELD) or {}).get("read_attempts", 1)
    seconds = min(3600, 60 * (2 ** min(max(count - 1, 0), 6)))
    return (now() + timedelta(seconds=seconds)).isoformat()


async def resume(db, *, user_id, order_number, actor_id="", actor_name="", manual=False):
    if manual:
        async def enroll(scoped):
            workflow = await shipping_workflow(scoped, user_id, order_number)
            pending = pending_operation(actor_id, actor_name)
            # Historic issue paths did not all journal before provider POST.
            # Enrollment cannot prove non-delivery: only readback is safe.
            pending["status_attempted"] = True
            pending["awb_attempted"] = True
            pending["legacy_readback_only"] = True
            await scoped[WORKFLOWS].update_one(
                {"user_id": user_id, "order_number": order_number, FIELD: {"$exists": False}},
                {"$set": {FIELD: pending}})
        await operational_owner(db, user_id, enroll)
    instant = now()
    query = {"user_id": user_id, "order_number": order_number, "assembly_status": "completed",
             f"{FIELD}.version": 1, f"{FIELD}.lease_until": {"$lte": instant.isoformat()}}
    if not manual:
        query.update({f"{FIELD}.state": {"$in": ["pending", "requires_attention"]},
                      f"{FIELD}.due_at": {"$lte": instant.isoformat()},
                      "$or": [{f"{FIELD}.read_attempts": {"$exists": False}},
                              {f"{FIELD}.read_attempts": {"$lt": MAX_READ_ATTEMPTS}}]})
    token = uuid.uuid4().hex
    operation = await db[WORKFLOWS].find_one_and_update(query, {"$set": {
        f"{FIELD}.claim": token, f"{FIELD}.lease_until": (instant + timedelta(seconds=LEASE_SECONDS)).isoformat()},
        "$inc": {f"{FIELD}.attempts": 1, f"{FIELD}.read_attempts": 1}}, return_document=ReturnDocument.AFTER)
    result = {}
    if operation:
        try:
            with read_budget(12, claim=token):
                result = await asyncio.wait_for(_deliver(db, operation), READ_TIMEOUT_SECONDS)
        except Exception as exc:
            # No provider messages, identifiers or customer data in diagnostics.
            code = getattr(exc, "code", type(exc).__name__)
            await db[WORKFLOWS].update_one(
                {"user_id": user_id, "order_number": order_number,
                 "$or": [{f"{FIELD}.claim": token},
                         {f"{FIELD}.claim": None, f"{FIELD}.state": "requires_attention",
                          f"{FIELD}.read_attempts": operation[FIELD]["read_attempts"]}]},
                {"$set": {f"{FIELD}.state": "requires_attention",
                          f"{FIELD}.error_code": code, f"{FIELD}.claim": None, f"{FIELD}.lease_until": "",
                          f"{FIELD}.due_at": next_read_at(operation),
                          f"{FIELD}.order_confirmed": False,
                          "salla_order_status": "unknown", "salla_order_status_verified_at": None,
                          "carrier_label_ready": False, "carrier_label_url": None,
                          "carrier_label_print_data": None}})
    workflow = await shipping_workflow(db, user_id, order_number)
    return {"ok": True, **label_fields(workflow), **result, **public_status(workflow, canonical=await source_status(db, user_id, order_number))}


async def _take_read_slot(db, key):
    instant, token = now(), uuid.uuid4().hex
    try:
        await db[LIMITS].update_one({"_id": key}, {"$setOnInsert": {"available_at": ""}}, upsert=True)
    except DuplicateKeyError:
        pass
    row = await db[LIMITS].find_one_and_update(
        {"_id": key, "available_at": {"$lte": instant.isoformat()}},
        {"$set": {"claim": token, "available_at": (instant + timedelta(seconds=LEASE_SECONDS)).isoformat()}},
        return_document=ReturnDocument.AFTER)
    return token if row else None


async def _release_read_slot(db, key, token, interval):
    await db[LIMITS].update_one({"_id": key, "claim": token}, {"$set": {
        "claim": None, "available_at": (now() + timedelta(seconds=interval)).isoformat()}})


async def _advance_owner_cursor(db, token, owner, cutoff):
    # An expired/superseded worker must neither move the durable cursor nor start
    # another job. Release never erases cursor progress after a process restart.
    result = await db[LIMITS].update_one({"_id": "global", "claim": token,
        "available_at": {"$gt": now().isoformat()}}, {"$set": {
            "owner_cursor": owner, "round_due_before": cutoff}})
    return result.matched_count == 1


async def run_once(db):
    # Shared across processes: one bounded provider-read job, then cooldown.
    # Select DISTINCT owners in persisted round-robin order, not a window of
    # orders that one large owner can fill. Freeze the due cutoff per round:
    # newly enrolled/backed-off jobs join the next round, not ahead of waiters.
    token = await _take_read_slot(db, "global")
    if not token:
        return 0
    try:
        scheduler = await db[LIMITS].find_one({"_id": "global", "claim": token})
        if not scheduler:
            return 0
        cursor = scheduler.get("owner_cursor", "")
        cutoff = scheduler.get("round_due_before") or now().isoformat()
        # Exhausted legacy pending rows still become visible attention work.
        # One bounded cleanup per tick; they never hide eligible work or GET.
        await db[WORKFLOWS].find_one_and_update({"assembly_status": "completed",
            f"{FIELD}.version": 1, f"{FIELD}.state": "pending",
            f"{FIELD}.read_attempts": {"$gte": MAX_READ_ATTEMPTS},
            f"{FIELD}.lease_until": {"$lte": now().isoformat()}}, {"$set": {
                f"{FIELD}.state": "requires_attention", f"{FIELD}.claim": None,
                f"{FIELD}.error_code": "completion_read_budget_exhausted"}},
            maxTimeMS=QUEUE_QUERY_TIMEOUT_MS)
        visited, wrapped = set(), False
        for _ in range(OWNER_SCAN_LIMIT):
            query = {"assembly_status": "completed", f"{FIELD}.version": 1,
                f"{FIELD}.state": {"$in": ["pending", "requires_attention"]},
                f"{FIELD}.due_at": {"$lte": cutoff},
                f"{FIELD}.lease_until": {"$lte": now().isoformat()},
                "user_id": {"$gt": cursor},
                "$or": [{f"{FIELD}.read_attempts": {"$exists": False}},
                        {f"{FIELD}.read_attempts": {"$lt": MAX_READ_ATTEMPTS}}]}
            row = await db[WORKFLOWS].find_one(query,
                {"user_id": 1, "order_number": 1},
                sort=[("user_id", 1), (f"{FIELD}.due_at", 1), ("order_number", 1)],
                max_time_ms=QUEUE_QUERY_TIMEOUT_MS)
            if not row:
                if wrapped:
                    return 0
                cursor, cutoff, wrapped = "", now().isoformat(), True
                if not await _advance_owner_cursor(db, token, cursor, cutoff):
                    return 0
                # Wrapping is bounded independently of the distinct-owner cap.
                query["user_id"], query[f"{FIELD}.due_at"] = {"$gt": ""}, {"$lte": cutoff}
                row = await db[WORKFLOWS].find_one(query,
                    {"user_id": 1, "order_number": 1},
                    sort=[("user_id", 1), (f"{FIELD}.due_at", 1), ("order_number", 1)],
                    max_time_ms=QUEUE_QUERY_TIMEOUT_MS)
                if not row:
                    return 0
            cursor = row["user_id"]
            if cursor in visited:
                return 0
            visited.add(cursor)
            if not await _advance_owner_cursor(db, token, cursor, cutoff):
                return 0
            owner_key = "owner:" + cursor
            owner_token = await _take_read_slot(db, owner_key)
            if not owner_token:
                continue
            try:
                # Owner acquisition itself awaited Mongo. Recheck global CAS
                # after that await before starting the existing operation CAS.
                if not await _advance_owner_cursor(db, token, cursor, cutoff):
                    return 0
                await resume(db, user_id=cursor, order_number=row["order_number"])
                return 1
            finally:
                await _release_read_slot(db, owner_key, owner_token, OWNER_READ_INTERVAL)
        return 0
    finally:
        await _release_read_slot(db, "global", token, GLOBAL_READ_INTERVAL)


async def loop(db):
    while True:
        try:
            await run_once(db)
        except Exception as exc:
            logger.warning("Assembly delivery deferred: %s", type(exc).__name__)
        await asyncio.sleep(5)


async def start_worker(db):
    await db[WORKFLOWS].create_index([(f"{FIELD}.version", 1), (f"{FIELD}.state", 1),
                                    (f"{FIELD}.due_at", 1)], name="assembly_delivery_due")
    await db[WORKFLOWS].create_index([("user_id", 1), (f"{FIELD}.due_at", 1),
        ("order_number", 1)], name="assembly_delivery_owner_round",
        partialFilterExpression={"assembly_status": "completed", f"{FIELD}.version": 1})
    return asyncio.create_task(loop(db), name="assembly-completion-delivery")
