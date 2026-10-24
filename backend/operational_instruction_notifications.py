"""Account-scoped operational instruction inbox and durable lifecycle notifications."""
from __future__ import annotations
import hashlib
import logging
from datetime import datetime, timezone
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS, instruction_targets, public_instruction, text

NOTIFICATIONS = "operational_instruction_notifications_v1"
ACTIVE = ["active", "waiting_customer_service_approval"]
log = logging.getLogger(__name__)
LABELS = {
    "created": "تعليمات جديدة للطلب", "assigned": "تعليمات تخص مهمتك الحالية",
    "submitted": "تنفيذ بانتظار موافقة خدمة العملاء", "approved": "تمت الموافقة وفك الإيقاف؛ استكمل التجهيز",
    "rejected": "رُفض التنفيذ؛ راجع سبب الرفض", "completed": "اكتملت التعليمات وفُك الإيقاف",
    "closed": "أُغلقت الملاحظة بواسطة صاحبها",
    "progress": "تحديث تقدم تجهيز المنتج",
    "comment": "رد جديد على طلب الإيقاف",
    "acknowledged": "تم الاطلاع على التعليمات",
}

async def recipients(db, merchant_id, instruction):
    """Resolve current responsibility; driver IDs are never treated as account IDs."""
    order = text(instruction.get("order_number"))
    result = {text(instruction.get("created_by"))} if (instruction.get("task_protocol") or instruction.get("thread_protocol")) else {merchant_id, text(instruction.get("created_by"))}
    pieces = await db["mezan_preparation_pieces_v1"].find(
        {"user_id": merchant_id, "order_number": order}, {"_id": 0}).to_list(5000)
    workflow = await db["order_review_workflows"].find_one(
        {"user_id": merchant_id, "order_number": order}, {"_id": 0}) or {}
    from order_tracking_notes_routes import _piece_stage, _current_stage
    stages = set(instruction.get("target_stages") or [])
    activated = (instruction.get("activated_piece_stages") or {}) if instruction.get("task_protocol") or instruction.get("operational_hold") else {}
    latched = instruction.get("task_protocol") and instruction.get("activation_state") == "active"
    reached = (_current_stage(workflow, []) in stages or latched) if not pieces else False
    reached_stages = set()
    for piece in pieces:
        if instruction_targets(instruction, piece_id=text(piece.get("piece_id")), order_item_id=text(piece.get("order_item_id"))):
            stage = activated.get(text(piece.get("piece_id"))) or _piece_stage(piece, workflow)
            persistent_piece = latched and (instruction.get("activated_order") or not instruction.get("activated_piece_stages") or text(piece.get("piece_id")) in instruction.get("activated_piece_stages", {}))
            if stage not in stages and not persistent_piece:
                continue
            reached = True
            reached_stages.add(stage)
            account = text(piece.get("responsible_employee_id"))
            if account and stage in {"preparation", "supplier_dispatch", "supplier_receiving", "preparation_receiving"}:
                result.add(account)
    if not reached and instruction.get("status") in ACTIVE:
        return set()
    field_stages = {"preparation_received_by": {"assembly_labeling", "preparation_receiving"},
        "claimed_by": {"assembly_labeling"}, "carrier_handoff_employee_id": {"carrier_handoff"}}
    for field, allowed in field_stages.items():
        if text(workflow.get(field)) and (reached_stages & allowed):
            result.add(text(workflow[field]))
    assignments = await db["store_delivery_assignments"].find({
        "user_id": merchant_id, "$or": [{"order_id": order}, {"order_number": order}],
        "active": True, "status": {"$in": ["assigned", "out_for_delivery"]},
    }, {"_id": 0, "driver_id": 1}).to_list(100)
    if stages == {"store_courier"} and not assignments and instruction.get("status") in ACTIVE:
        return set()
    for assignment in assignments if "store_courier" in stages and ("store_courier" in reached_stages or _current_stage(workflow, []) == "store_courier") else []:
        driver = await db["store_drivers"].find_one({
            "user_id": merchant_id, "id": assignment.get("driver_id"), "status": "active",
        }, {"_id": 0, "account_user_id": 1})
        if driver and text(driver.get("account_user_id")):
            result.add(text(driver["account_user_id"]))
    if instruction.get("task_protocol") or instruction.get("thread_protocol"):
        claimed = await db[TASKS].find({"merchant_id": merchant_id, "instruction_id": instruction["id"],
            "claimed_for_review": True}, {"_id": 0, "assignee_id": 1}).to_list(100)
        result.update(text(task.get("assignee_id")) for task in claimed if text(task.get("assignee_id")))
    users = await db["users"].find({
        "$or": [{"id": merchant_id}, {"created_by": merchant_id}], "id": {"$in": list(result)},
        "disabled": {"$ne": True}, "is_active": {"$ne": False},
        "deleted_at": {"$in": [None, ""]},
    }, {"_id": 0, "id": 1, "role": 1, "extra_permissions": 1, "denied_permissions": 1}).to_list(5000)
    valid = {text(row.get("id")) for row in users}
    for row in users if not (instruction.get("task_protocol") or instruction.get("thread_protocol")) else []:
        if "customer_intelligence.inbox.read" not in (row.get("denied_permissions") or []) and (
            row.get("role") in {"admin", "customer_service"} or
            "customer_intelligence.inbox.read" in (row.get("extra_permissions") or [])):
            result.add(text(row.get("id")))
    return result & valid

async def _enqueue(db, row):
    try:
        from operational_push import enqueue_operational_push
        await enqueue_operational_push(db, recipient_id=row["recipient_id"],
            event_id=row["id"], order_number=row["order_number"], instruction_id=row["instruction_id"], priority=row.get("priority", "normal"))
    except Exception:
        # The durable inbox survives transport failures; refresh retries enqueue.
        log.warning("Operational notification push enqueue deferred", exc_info=False)

async def actor_assignment_key(db, merchant_id, instruction_id, actor_id):
    tasks = await db[TASKS].find({"merchant_id": merchant_id, "instruction_id": instruction_id,
        "assignee_id": actor_id, "current": {"$ne": False}}, {"_id": 0, "id": 1}).to_list(5000)
    return hashlib.sha256(":".join(sorted(task["id"] for task in tasks)).encode()).hexdigest()

async def emit_instruction_notification(db, *, merchant_id, instruction, event_type, recipient_ids=None):
    targets = set(recipient_ids) if recipient_ids is not None else await recipients(db, merchant_id, instruction)
    if recipient_ids is None and event_type in {"approved", "rejected", "completed"}:
        # Submitter needs the result even if the work has since changed hands.
        submitter = text(instruction.get("submitted_by"))
        if submitter and await db["users"].find_one({"id": submitter, "created_by": merchant_id,
                "disabled": {"$ne": True}, "is_active": {"$ne": False}}):
            targets.add(submitter)
    if event_type in {"approved", "completed", "closed"} and (instruction.get("task_protocol") or instruction.get("thread_protocol")):
        tasks = await db[TASKS].find({"merchant_id": merchant_id, "instruction_id": instruction["id"]}, {"_id": 0, "assignee_id": 1}).to_list(5000)
        targets.update(text(task.get("assignee_id")) for task in tasks if text(task.get("assignee_id")))
    revision = str(instruction.get("notification_revision") or instruction.get("rejected_at") or instruction.get("submitted_at") or instruction.get("created_at") or "")
    now = datetime.now(timezone.utc)
    for recipient in sorted(targets):
        assignment_key = await actor_assignment_key(db, merchant_id, instruction["id"], recipient)
        event_revision = revision + assignment_key if event_type == "assigned" else revision
        key = hashlib.sha256(f"{merchant_id}:{instruction['id']}:{event_type}:{event_revision}:{recipient}".encode()).hexdigest()
        row = {"id": key, "merchant_id": merchant_id, "recipient_id": recipient,
            "instruction_id": instruction["id"], "order_number": text(instruction.get("order_number")),
            "event_type": event_type, "assignment_key": assignment_key, "title": LABELS[event_type],
            "priority": instruction.get("priority", "normal"), "metadata": instruction.get("notification_metadata") or {},
            "message": text(instruction.get("rejection_note")) if event_type == "rejected" else text(instruction.get("note")),
            "created_at": now, "read_at": None}
        await db[NOTIFICATIONS].update_one({"_id": key}, {"$setOnInsert": row}, upsert=True)
        await _enqueue(db, row)
    return len(targets)

async def refresh_actor_instructions(db, *, merchant_id, actor_id):
    """Called by the background device sweep and inbox fetch; writes only alerts."""
    actor = await db["users"].find_one({"id": actor_id, "$or": [{"id": merchant_id}, {"created_by": merchant_id}],
        "disabled": {"$ne": True}, "is_active": {"$ne": False}})
    if not actor:
        return
    orders = set()
    owned_pieces = await db["mezan_preparation_pieces_v1"].find({"user_id": merchant_id,
        "responsible_employee_id": actor_id}, {"_id": 0, "order_number": 1}).to_list(10000)
    orders.update(text(piece.get("order_number")) for piece in owned_pieces)
    workflows = await db["order_review_workflows"].find({"user_id": merchant_id,
        "$or": [{field: actor_id} for field in ("preparation_received_by", "claimed_by", "carrier_handoff_employee_id", "reviewed_by")]},
        {"_id": 0, "order_number": 1}).to_list(10000)
    orders.update(text(row.get("order_number")) for row in workflows)
    driver = await db["store_drivers"].find_one({"user_id": merchant_id, "account_user_id": actor_id, "status": "active"})
    if driver:
        assigned = await db["store_delivery_assignments"].find({"user_id": merchant_id,
            "driver_id": driver["id"], "active": True, "status": {"$in": ["assigned", "out_for_delivery"]}},
            {"_id": 0, "order_number": 1, "order_id": 1}).to_list(10000)
        orders.update(text(row.get("order_number") or row.get("order_id")) for row in assigned)
    last_id = ""
    while True:
        query = {"user_id": merchant_id, "status": {"$in": ACTIVE},
            "$or": [{"created_by": actor_id}, {"order_number": {"$in": sorted(orders - {""})}}]}
        if last_id:
            query["id"] = {"$gt": last_id}
        rows = await db[ORDER_TRACKING_INSTRUCTIONS].find(query, {"_id": 0}).sort("id", 1).to_list(200)
        if not rows:
            break
        for instruction in rows:
            if actor_id in await recipients(db, merchant_id, instruction):
                await ensure_instruction_tasks(db, merchant_id, instruction)
                assignment_key = await actor_assignment_key(db, merchant_id, instruction["id"], actor_id)
                existing = await db[NOTIFICATIONS].find_one({"merchant_id": merchant_id,
                    "recipient_id": actor_id, "instruction_id": instruction["id"], "assignment_key": assignment_key})
                if not existing:
                    await emit_instruction_notification(db, merchant_id=merchant_id,
                        instruction=instruction, event_type="assigned", recipient_ids={actor_id})
        last_id = rows[-1]["id"]
    pending = await db[NOTIFICATIONS].find({"merchant_id": merchant_id,
        "recipient_id": actor_id, "read_at": None}, {"_id": 0}).to_list(500)
    for row in pending:
        await _enqueue(db, row)

async def notification_feed(db, *, merchant_id, actor_id):
    await refresh_actor_instructions(db, merchant_id=merchant_id, actor_id=actor_id)
    rows = await db[NOTIFICATIONS].find({"merchant_id": merchant_id, "recipient_id": actor_id},
        {"_id": 0, "merchant_id": 0, "recipient_id": 0}).sort("created_at", -1).to_list(200)
    for row in rows:
        instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one({"user_id": merchant_id,
            "id": row["instruction_id"]}, {"_id": 0})
        row["instruction"] = await scoped_instruction(db, merchant_id, instruction, actor_id, actor_id == text(instruction.get("created_by"))) if instruction else None
        row["read"] = bool(row.get("read_at"))
    return {"items": rows, "unread_count": await db[NOTIFICATIONS].count_documents({
        "merchant_id": merchant_id, "recipient_id": actor_id, "read_at": None})}

TASKS = "operational_instruction_tasks_v1"

async def ensure_instruction_tasks(db, merchant_id, instruction):
    if not (instruction.get("task_protocol") or instruction.get("thread_protocol")):
        return []
    pieces = await db["mezan_preparation_pieces_v1"].find({"user_id": merchant_id,
        "order_number": instruction["order_number"]}, {"_id": 0}).to_list(5000)
    targets = [p for p in pieces if instruction_targets(instruction,
        piece_id=text(p.get("piece_id")), order_item_id=text(p.get("order_item_id")))]
    records = [(text(p.get("piece_id")), text(p.get("responsible_employee_id")), p) for p in targets]
    workflow = await db["order_review_workflows"].find_one({"user_id": merchant_id, "order_number": instruction["order_number"]}, {"_id": 0}) or {}
    from order_tracking_notes_routes import _piece_stage, _current_stage
    stage_assignments = {"assembly_labeling": text(workflow.get("claimed_by") or workflow.get("preparation_received_by")),
        "carrier_handoff": text(workflow.get("carrier_handoff_employee_id"))}
    records = [(piece_id, stage_assignments.get(((instruction.get("activated_piece_stages") or {}).get(piece_id) if instruction.get("task_protocol") or instruction.get("operational_hold") else None) or _piece_stage(piece, workflow), assignee), piece)
        for piece_id, assignee, piece in records]
    if "store_courier" in instruction.get("target_stages", []):
        assignment = await db["store_delivery_assignments"].find_one({"user_id": merchant_id,
            "$or": [{"order_id": instruction["order_number"]}, {"order_number": instruction["order_number"]}],
            "active": True, "status": {"$in": ["assigned", "out_for_delivery"]}})
        driver = await db["store_drivers"].find_one({"user_id": merchant_id, "id": (assignment or {}).get("driver_id"), "status": "active"}) if assignment else None
        courier_id = text((driver or {}).get("account_user_id"))
        if set(instruction.get("target_stages", [])) == {"store_courier"}:
            records = [(text(piece.get("piece_id")), courier_id, piece) for piece in targets] or [("courier", courier_id, {})]
        elif courier_id:
            records.append(("courier", courier_id, {}))
    if not records:
        records = [("order", text(workflow.get("reviewed_by")), {})]
    if not instruction.get("task_protocol"):
        await db[TASKS].update_many({"merchant_id": merchant_id, "instruction_id": instruction["id"]}, {"$set": {"current": False}})
    for piece_id, assignee, piece in records:
        stage = ((instruction.get("activated_piece_stages") or {}).get(piece_id) if instruction.get("task_protocol") or instruction.get("operational_hold") else None) or (_piece_stage(piece, workflow) if piece else _current_stage(workflow, []))
        if not instruction.get("task_protocol") and stage not in instruction.get("target_stages", []):
            continue
        stage_key = "" if instruction.get("task_protocol") else ":" + stage
        identity = hashlib.sha256(f"{merchant_id}:{instruction['id']}:{piece_id}{stage_key}".encode()).hexdigest()
        await db[TASKS].update_one({"_id": identity}, {"$setOnInsert": {
            "id": identity, "merchant_id": merchant_id, "instruction_id": instruction["id"],
            "piece_id": piece_id, "stage": stage, "order_item_id": text(piece.get("order_item_id")),
            "product_name": text(piece.get("product_name") or piece.get("item_name")),
            "assignee_id": assignee, "status": "pending", "created_at": datetime.now(timezone.utc),
        }}, upsert=True)
        await db[TASKS].update_one({"id": identity}, {"$set": {"current": True}})
        # Reassignment transfers pending work, never another person's confirmation.
        if assignee:
            await db[TASKS].update_one({"id": identity, "status": "pending"},
                {"$set": {"assignee_id": assignee}})
    return await db[TASKS].find({"merchant_id": merchant_id,
        "instruction_id": instruction["id"]}, {"_id": 0, "merchant_id": 0}).to_list(5000)

async def scoped_instruction(db, merchant_id, instruction, actor_id, can_manage=False):
    can_manage = can_manage and text(instruction.get("created_by")) == actor_id
    tasks = await ensure_instruction_tasks(db, merchant_id, instruction)
    visible = tasks if can_manage else [task for task in tasks if task.get("assignee_id") == actor_id]
    result = public_instruction(instruction)
    result.pop("resolution_intent", None)
    result["tasks"] = visible
    result["can_approve_resume"] = (text(instruction.get("created_by")) == actor_id
        and instruction.get("status") == "waiting_customer_service_approval"
        and (not instruction.get("task_protocol") or bool(tasks) and all(task["status"] == "submitted" for task in tasks)))
    result["can_close_instruction"] = (text(instruction.get("created_by")) == actor_id
        and instruction.get("action_type") not in {"edit_product", "edit_order", "delete_product", "cancel_order"}
        and instruction.get("status") in ACTIVE and all(task.get("acknowledged_at") for task in tasks if task.get("assignee_id") and task.get("current", True)))
    result["proposed_changes_source"] = "requester_supplied"
    if not can_manage:
        item_ids = {task["order_item_id"] for task in visible}
        result["target_piece_ids"] = [task["piece_id"] for task in visible]
        result["target_ids"] = sorted(item_ids) if instruction.get("scope") == "item" else result["target_piece_ids"]
        result["ready_item_confirmations"] = {}
        result["target_id"] = result["target_ids"][0] if result["target_ids"] else None
        visible_piece_ids = {task["piece_id"] for task in visible}
        result["activated_piece_stages"] = {key: value for key, value in (result.get("activated_piece_stages") or {}).items() if key in visible_piece_ids}
        result["acknowledgment_history"] = [entry for entry in result.get("acknowledgment_history", []) if entry.get("actor_id") == actor_id]
        result["acknowledged_by_ids"] = [value for value in result.get("acknowledged_by_ids", []) if value == actor_id]
        result["evidence_ids"] = []
        result["evidence"] = [entry for entry in result.get("evidence", []) if entry.get("created_by") == actor_id]
        if result.get("submitted_by") != actor_id:
            for field in ("submitted_by", "submitted_by_name", "submitted_result"):
                result.pop(field, None)

        result["rejection_note"] = next((task.get("rejection_note") for task in visible if task.get("rejection_note")), None)
        result["proposed_changes"] = [change for change in result.get("proposed_changes", [])
            if not change.get("order_item_id") or change["order_item_id"] in item_ids]
    return result

async def instruction_inbox(db, *, merchant_id, actor_id, status="active", cursor=None, authored_only=False):
    """One row per conversation. Read receipts never change its classification."""
    import base64
    import json
    from fastapi import HTTPException
    if status not in {"active", "completed", "all"}:
        raise HTTPException(422, detail={"code": "instruction_inbox_status_invalid"})
    await refresh_actor_instructions(db, merchant_id=merchant_id, actor_id=actor_id)
    participated = await db[NOTIFICATIONS].distinct("instruction_id", {"merchant_id": merchant_id, "recipient_id": actor_id})
    owned = await db[TASKS].distinct("instruction_id", {"merchant_id": merchant_id, "assignee_id": actor_id})
    query = {"user_id": merchant_id, "created_by": actor_id} if authored_only else {
        "user_id": merchant_id, "$or": [{"created_by": actor_id}, {"id": {"$in": list(set(participated + owned))}}]}
    collected = []
    last_id = ""
    while True:
        page_query = {**query, **({"id": {"$gt": last_id}} if last_id else {})}
        rows = await db[ORDER_TRACKING_INSTRUCTIONS].find(page_query, {"_id": 0}).sort("id", 1).to_list(200)
        if not rows:
            break
        for row in rows:
            active = row.get("status") in ACTIVE
            if active and row.get("created_by") != actor_id and actor_id not in await recipients(db, merchant_id, row):
                continue
            collected.append(row)
        last_id = rows[-1]["id"]
    active_count = sum(row.get("status") in ACTIVE for row in collected)
    filtered = [row for row in collected if status == "all" or ((row.get("status") in ACTIVE) == (status == "active"))]
    def key(row):
        stamp = row.get("created_at")
        return (stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp or ""), row["id"])
    filtered.sort(key=key, reverse=True)
    if cursor:
        try:
            boundary = tuple(json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))))
            if len(boundary) != 2 or not all(isinstance(value, str) for value in boundary):
                raise ValueError()
        except Exception:
            raise HTTPException(422, detail={"code": "instruction_inbox_cursor_invalid"}) from None
        filtered = [row for row in filtered if key(row) < boundary]
    page = filtered[:100]
    next_cursor = base64.urlsafe_b64encode(json.dumps(key(page[-1])).encode()).decode().rstrip("=") if len(filtered) > 100 else None
    return {"items": [await scoped_instruction(db, merchant_id, row, actor_id, row.get("created_by") == actor_id) for row in page],
        "active_count": active_count, "next_cursor": next_cursor}
