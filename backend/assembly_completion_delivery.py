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

import httpx

from pymongo import ReturnDocument

from operational_atomic import operational_owner
from assembly_status_policy import canonical_status, source_status, status_values, component_refresh_pending
from order_engine import shipping_label_service as shipping
import fulfillment_carrier_label as labels

WORKFLOWS = "order_review_workflows"
FIELD = "assembly_delivery"
LEASE_SECONDS = 120
TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 8
MAX_CONCURRENT_DELIVERIES = 4
logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc)


def pending_operation(actor_id, actor_name):
    return {"version": 2, "state": "pending", "attempts": 0,
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
        if component_refresh_pending(source):
            raise shipping.ShippingLabelError("completion_source_refresh_pending", "جارٍ التحقق من بيانات التجهيز المحدثة.")
        if (outbox.get("claim") != operation[FIELD]["claim"]
                or outbox.get("lease_until", "") <= now().isoformat()):
            raise shipping.ShippingLabelError("completion_lease_lost", "انتهت صلاحية محاولة الاستكمال.")
        current_status = canonical_status(source)
        if current_status not in {"in_progress", "completed"}:
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
        if effect == "status" and operation.get("_authorize_first_issue"):
            if current.get("stage") != "completed" or outbox.get("status_attempted") is not False:
                raise shipping.ShippingLabelError("completion_readback_pending", "يلزم التحقق من حالة التنفيذ.")
            await operation["_authorize_first_issue"]()
        if effect == "status" and current_status == "completed":
            # A webhook won the race after the provider GET. Never dispatch a
            # second status POST; require a fresh read of this completion.
            raise shipping.ShippingLabelError("completion_readback_pending", "جارٍ التحقق من تنفيذ الطلب في سلة.")
        if effect or patch is not None:
            selector = {
                "user_id": owner, "order_number": number, "revision": current.get("revision"),
                "assembly_status": "completed", f"{FIELD}.claim": outbox["claim"],
            }
            updates = dict(patch or {})
            if effect:
                if legacy:
                    raise shipping.ShippingLabelError("completion_effect_unconfirmed", "المحاولة السابقة غير مؤكدة؛ لن يتكرر الإرسال.")
                if effect != "status" or outbox.get("version") != 2:
                    selector[f"{FIELD}.{effect}_attempted"] = False
                else:
                    updates[f"{FIELD}.status_dispatch_count"] = outbox.get("status_dispatch_count", 0) + 1
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
            "error_message": ({
                "assembly_order_not_ready": "حالة الطلب في سلة لا تسمح بالاستكمال.",
                "assembly_canonical_status_blocked": "تغيرت حالة الطلب؛ الاستكمال متوقف.",
                "assembly_completion_evidence_changed": "تغيرت بيانات التجهيز؛ يلزم التحقق.",
                "completion_label_pending": "لم تصبح البوليصة الحالية متاحة بعد.",
                "completion_status_unconfirmed": "العملية السابقة غير مؤكدة؛ تحقق من سلة.",
                "TimeoutError": "تعذر تأكيد استجابة سلة خلال المهلة.",
            }.get(operation.get("error_code"), "تعذر تأكيد الاستكمال؛ أعد التحقق من الطلب والشحنة.")
                if operation.get("state") == "requires_attention" else None),
            "ready": available, "order_status_completed": confirmed}


async def read_status(db, user_id, order_number, *, authorize_repair=None):
    workflow = await shipping_workflow(db, user_id, order_number)
    if FIELD not in workflow:
        from assembly_completion_legacy_readback import read_legacy_completion
        legacy = await read_legacy_completion(db, user_id, order_number)
        if legacy is not None:
            return legacy
        # An outbox was enrolled concurrently. Use its current authority.
        workflow = await shipping_workflow(db, user_id, order_number)
    operation = workflow.get(FIELD) or {}
    if workflow.get("assembly_status") == "completed" and operation:
        from assembly_print_completion import read_print_completion
        try:
            proof = await read_print_completion(db, user_id, order_number)
            if proof is not None:
                return proof
        except shipping.ShippingLabelError as exc:
            if exc.code != "order_status_not_completed":
                raise
        # A fresh negative read must never revive an old outbox success.
        return {**label_fields(workflow), **public_status(workflow, canonical=None),
                "ready": False, "order_status_completed": False,
                "order_completion_status": "pending", "label_status": "pending",
                "label_url": None, "print_data": None}
    if operation.get("version") == 2 and operation.get("state") == "requires_attention":
        from assembly_completion_v2_readback import read_blocked_completion
        recovered = await read_blocked_completion(db, user_id, order_number, authorize_repair=authorize_repair)
        if recovered is not None:
            return recovered
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
        observed = status_values(order.get("status")) | status_values(order.get("status_slug"))
        if observed != {"in_progress"}:
            raise shipping.ShippingLabelError("assembly_order_not_ready", "الطلب ليس قيد التنفيذ في سلة.")
        if operation[FIELD].get("version") != 2 or operation[FIELD].get("legacy_readback_only"):
            raise shipping.ShippingLabelError("completion_status_unconfirmed", "المحاولة السابقة غير مؤكدة؛ يلزم الاستكمال في سلة.")
        # Journal before dispatch, with current local evidence and a fresh
        # provider in_progress read on EVERY attempt. The owner accepts the
        # remaining remote GET/POST race: Salla has no conditional-write API.
        # A later attempt reads first; completed never causes another POST.
        try:
            await guard_effect(db, operation, effect="status")
        except shipping.ShippingLabelError as exc:
            if exc.code != "completion_readback_pending":
                raise
            # One immediate bounded readback for the expected local transition,
            # retaining the original shipment/material fence across both reads.
            read_id, order = await shipping._resolve_order(db, owner, number)
            if (read_id != internal_id or str(order.get("reference_id")) != number
                    or str(order.get("id")) != internal_id):
                raise shipping.ShippingLabelError("salla_order_reference_mismatch", "تعذر تأكيد هوية الطلب في سلة.")
            await guard_effect(db, operation)
            if not shipping._order_is_completed(order):
                raise exc
        else:
            await shipping.call_salla(db, owner, "POST", f"/orders/{internal_id}/status",
                                      json={"slug": "completed"}, single_post_attempt=True)
            # An acknowledgement alone is not completion proof.
            raise shipping.ShippingLabelError("completion_readback_pending", "جارٍ التحقق من تنفيذ الطلب في سلة.")
    await guard_effect(db, operation, patch={
        f"{FIELD}.order_confirmed": True, "salla_order_status": "completed",
        "salla_order_status_verified_at": now().isoformat()})
    async def publish(scoped, result):
        # For a Mezan courier this CAS shares the transaction that produced its QR.
        patch = labels._workflow_patch({**result, "order_status_completed": True}, now=now().isoformat())
        patch.update({f"{FIELD}.state": "confirmed" if result.get("ready") else (
                          "requires_attention" if operation[FIELD]["attempts"] >= MAX_ATTEMPTS else "pending"),
                      f"{FIELD}.error_code": None if result.get("ready") else "completion_label_pending",
                      f"{FIELD}.lease_until": "", f"{FIELD}.claim": None,
                      f"{FIELD}.due_at": (now() + timedelta(seconds=30)).isoformat()})
        await guard_effect(scoped, operation, patch=patch)

    return await labels.read_current_carrier_label(db, owner, number, publish=publish)


async def resume(db, *, user_id, order_number, actor_id="", actor_name="", manual=False, authorize_first_issue=None):
    if manual:
        async def enroll(scoped):
            workflow = await shipping_workflow(scoped, user_id, order_number)
            pending = pending_operation(actor_id, actor_name)
            # Historic issue paths did not all journal before provider POST.
            # Enrollment cannot prove non-delivery: only readback is safe.
            pending["version"] = 1
            pending["status_attempted"] = True
            pending["awb_attempted"] = True
            pending["legacy_readback_only"] = True
            await scoped[WORKFLOWS].update_one(
                {"user_id": user_id, "order_number": order_number, FIELD: {"$exists": False}},
                {"$set": {FIELD: pending}})
        await operational_owner(db, user_id, enroll)
    instant = now()
    query = {"user_id": user_id, "order_number": order_number, "assembly_status": "completed",
             f"{FIELD}.version": {"$in": [1, 2]}, f"{FIELD}.lease_until": {"$lte": instant.isoformat()}}
    if not manual:
        query.update({f"{FIELD}.state": "pending", f"{FIELD}.due_at": {"$lte": instant.isoformat()},
                      f"{FIELD}.attempts": {"$lt": MAX_ATTEMPTS}})
    if authorize_first_issue:
        query.update({"stage": "completed", f"{FIELD}.version": 2, f"{FIELD}.status_attempted": False,
                      f"{FIELD}.legacy_readback_only": {"$ne": True}})
    token = uuid.uuid4().hex
    operation = await db[WORKFLOWS].find_one_and_update(query, {"$set": {
        f"{FIELD}.claim": token, f"{FIELD}.lease_until": (instant + timedelta(seconds=LEASE_SECONDS)).isoformat()},
        "$inc": {f"{FIELD}.attempts": 1}}, return_document=ReturnDocument.AFTER)
    result = {}
    if operation:
        if authorize_first_issue:
            operation["_authorize_first_issue"] = authorize_first_issue
        try:
            result = await asyncio.wait_for(_deliver(db, operation), TIMEOUT_SECONDS)
        except Exception as exc:
            if authorize_first_issue and getattr(exc, "status_code", None) == 403:
                # Revoking the employee must not poison the worker's own outbox.
                await db[WORKFLOWS].update_one(
                    {"user_id": user_id, "order_number": order_number, f"{FIELD}.claim": token},
                    {"$set": {f"{FIELD}.claim": None, f"{FIELD}.lease_until": ""},
                     "$inc": {f"{FIELD}.attempts": -1}})
                raise
            # No provider messages, identifiers or customer data in diagnostics.
            code = getattr(exc, "code", type(exc).__name__)
            status = getattr(exc, "status_code", 0) or 0
            retryable = (code in {"completion_readback_pending", "completion_source_refresh_pending"}
                         or isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError))
                         or status in {408, 429} or status >= 500)
            retry = (retryable and operation[FIELD].get("version") == 2
                     and operation[FIELD]["attempts"] < MAX_ATTEMPTS)
            delay = min(120, 5 * 2 ** min(operation[FIELD]["attempts"] - 1, 5))
            await db[WORKFLOWS].update_one(
                {"user_id": user_id, "order_number": order_number, f"{FIELD}.claim": token},
                {"$set": {f"{FIELD}.state": "pending" if retry else "requires_attention",
                          f"{FIELD}.error_code": code, f"{FIELD}.claim": None, f"{FIELD}.lease_until": "",
                          f"{FIELD}.due_at": (now() + timedelta(seconds=delay)).isoformat(),
                          f"{FIELD}.order_confirmed": False,
                          "salla_order_status": "unknown", "salla_order_status_verified_at": None,
                          "carrier_label_ready": False}})
    workflow = await shipping_workflow(db, user_id, order_number)
    return {"ok": True, **label_fields(workflow), **result, **public_status(workflow, canonical=await source_status(db, user_id, order_number))}


async def _resume_queued(db, row):
    try:
        await resume(db, user_id=row["user_id"], order_number=row["order_number"])
    except Exception as exc:
        logger.warning("Assembly delivery deferred: %s", type(exc).__name__)


async def run_once(db, *, running=None):
    wait_for_batch = running is None
    if running is None:
        running = {}
    for key, task in list(running.items()):
        if task.done():
            del running[key]
    await db[WORKFLOWS].update_many({"assembly_status": "completed", f"{FIELD}.version": {"$in": [1, 2]},
        f"{FIELD}.state": "pending", f"{FIELD}.attempts": {"$gte": MAX_ATTEMPTS},
        f"{FIELD}.lease_until": {"$lte": now().isoformat()}},
        {"$set": {f"{FIELD}.state": "requires_attention", f"{FIELD}.claim": None,
                  f"{FIELD}.error_code": "completion_attempt_budget_exhausted"}})
    capacity = MAX_CONCURRENT_DELIVERIES - len(running)
    if capacity <= 0:
        return 0
    query = {"assembly_status": "completed", f"{FIELD}.version": {"$in": [1, 2]},
        f"{FIELD}.state": "pending", f"{FIELD}.due_at": {"$lte": now().isoformat()},
        f"{FIELD}.lease_until": {"$lte": now().isoformat()}, f"{FIELD}.attempts": {"$lt": MAX_ATTEMPTS}}
    if running:
        query["$nor"] = [{"user_id": owner, "order_number": number} for owner, number, _ in running]
    candidates = await db[WORKFLOWS].find(query, {"user_id": 1, "order_number": 1,
        f"{FIELD}.status_attempted": 1}).sort([
        (f"{FIELD}.status_attempted", 1),
        (f"{FIELD}.attempts", 1), (f"{FIELD}.due_at", 1), ("_id", 1),
    ]).limit(capacity).to_list(capacity)
    started = 0
    for row in candidates:
        # Never fill the last slot with old label/status readback. A last-piece
        # wakeup can dispatch a new completion even while three labels stall.
        first_attempt = (row.get(FIELD) or {}).get("status_attempted") is False
        if (not first_attempt and len(running) >= MAX_CONCURRENT_DELIVERIES - 1
                and not any(key[2] for key in running)):
            continue
        key = row["user_id"], row["order_number"], first_attempt
        running[key] = asyncio.create_task(_resume_queued(db, row), name="assembly-delivery-item")
        started += 1
    if wait_for_batch:
        await asyncio.gather(*running.values())
    return started


async def loop(db, *, wake_event=None):
    # Keep polling free slots while another order awaits Salla or its label.
    # The durable per-order claim in resume still excludes other replicas.
    running = {}
    try:
        while True:
            if wake_event is not None:
                wake_event.clear()
            try:
                await run_once(db, running=running)
            except Exception as exc:
                logger.warning("Assembly delivery deferred: %s", type(exc).__name__)
            if wake_event is None:
                await asyncio.sleep(5)
            else:
                try:
                    await asyncio.wait_for(wake_event.wait(), 5)
                except asyncio.TimeoutError:
                    pass
    finally:
        for task in running.values():
            task.cancel()
        await asyncio.gather(*running.values(), return_exceptions=True)


async def start_worker(db, *, wake_event=None):
    await db[WORKFLOWS].create_index([(f"{FIELD}.version", 1), (f"{FIELD}.state", 1),
                                    (f"{FIELD}.due_at", 1)], name="assembly_delivery_due")
    return asyncio.create_task(loop(db, wake_event=wake_event), name="assembly-completion-delivery")
