"""Claim noncash delivery once and atomically commit its operational evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException

from store_delivery_domain import StoreDeliveryRuleError, collection_requirements, money, normalize_text


ATTEMPT = "noncash_delivery_attempt"
PROFILE = "driver_noncash_delivery"
LEASE_SECONDS = 120
ACCOUNTING = {"accounting_status": "operational_only", "ledger_txn_group_id": None,
    "accounting_operation_id": None, "financial_handoff_status": "pending_mz2_driver_balance_link",
    "financial_source": "store_delivery_operational"}


def _conflict(code="driver_delivery_idempotency_conflict", status=409):
    raise HTTPException(status, detail={"code": code})


def _pending(code="driver_delivery_in_progress"):
    raise HTTPException(503, headers={"Retry-After": "2"}, detail={"code": code,
        "message": "جارٍ التحقق من تأكيد التسليم؛ أعد المحاولة بنفس الإيصال وإثبات التسليم."})


def _now():
    return datetime.now(timezone.utc)


def _phase(attempt):
    return attempt.get("phase", "delivery")


def _request_fields(payload):
    get = payload.get if isinstance(payload, dict) else lambda key: getattr(payload, key)
    values = {key: normalize_text(get(key)) for key in (
        "payment_method", "receipt_reference", "delivery_proof_reference", "bank_account_id",
        "conversation_evidence_reference")}
    values.update(physical_cash_amount=get("physical_cash_amount"), physical_cash_confirmed=get("physical_cash_confirmed"))
    return values


def _fingerprint(payload):
    values = _request_fields(payload)
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _saved_request(attempt):
    request = attempt.get("request_payload")
    if (not isinstance(request, dict) or request != _request_fields(request)
            or request.get("physical_cash_amount") is not None or request.get("physical_cash_confirmed") is not False
            or request.get("payment_method") not in {"", "cash", "bank_transfer", "card_terminal"}
            or not request.get("delivery_proof_reference") or _fingerprint(request) != attempt.get("fingerprint")):
        return None
    return request


def pending_delivery_completion(assignment, *, owner, driver_id):
    """Owner/driver-bound restart DTO; never expose the internal journal."""
    attempt = assignment.get(ATTEMPT)
    if (not isinstance(attempt, dict) or not driver_id or assignment.get("driver_id") != driver_id
            or assignment.get("user_id", owner) != owner or not assignment.get("active")
            or not assignment.get("id") or not assignment.get("order_id") or not assignment.get("order_number")
            or assignment.get("status") not in {"assigned", "out_for_delivery"}
            or _phase(attempt) not in {"pickup", "delivery"}
            or assignment.get("status") != ("assigned" if _phase(attempt) == "pickup" else "out_for_delivery")
            or attempt.get("state") not in {"dispatching", "reconciling", "uncertain", "confirmed"}):
        return None
    request = _saved_request(attempt)
    collection = attempt.get("collection") or {}
    if (request is None or not isinstance(collection, dict)
            or collection.get("user_id") != owner or collection.get("driver_id") != driver_id
            or collection.get("assignment_id") != assignment.get("id")
            or normalize_text(collection.get("order_id")) != normalize_text(assignment.get("order_id"))
            or normalize_text(collection.get("order_number")) != normalize_text(assignment.get("order_number"))
            or any(request[key] != normalize_text(collection.get(key)) for key in
                ("delivery_proof_reference", "receipt_reference", "bank_account_id"))):
        return None
    try:
        amount = money(collection.get("amount"))
        requirements = collection_requirements(outstanding_amount=amount, payment_method=request["payment_method"])
        if requirements["payment_method"] != collection.get("payment_method"):
            return None
    except StoreDeliveryRuleError:
        return None
    payload = {key: value or None for key, value in request.items() if key not in {"physical_cash_amount", "physical_cash_confirmed"}}
    payload.update(barcode=normalize_text(assignment.get("order_number") or assignment.get("order_id")),
        target_status="delivered", physical_cash_amount=None, physical_cash_confirmed=False)
    return {"version": 1, "assignment_id": assignment["id"], "order_number": normalize_text(assignment.get("order_number")),
        "collection_amount": format(amount, ".2f"), "bank_name_snapshot": collection.get("bank_name_snapshot"), "payload": payload}


def _public(assignment):
    return {key: value for key, value in assignment.items() if key not in {"_id", "user_id", ATTEMPT}}


def _order_scope(order, owner, assignment):
    """Pin both identities, retaining the actual BSON type of each saved key."""
    raw = ((order or {}).get("raw_by_source") or {}).get("salla_direct") or {}
    identifiers = [value for value in ((order or {}).get("order_id"), raw.get("id")) if value is not None]
    if (not order or normalize_text(order.get("order_number")) != normalize_text(assignment.get("order_number"))
            or not identifiers or any(normalize_text(value) != normalize_text(assignment.get("order_id")) for value in identifiers)):
        _conflict("canonical_order_identity_conflict")
    query = {"user_id": owner, "order_number": order["order_number"]}
    if order.get("order_id") is not None:
        query["order_id"] = order["order_id"]
    if raw.get("id") is not None:
        query["raw_by_source.salla_direct.id"] = raw["id"]
    return query


async def _workflow_binding(db, owner, assignment):
    number = normalize_text(assignment.get("order_number"))
    variants = [number]
    if number.isdecimal():
        variants.append(int(number))
    rows = await db.order_review_workflows.find({"user_id": owner, "order_number": {"$in": variants}},
        {"_id": 1, "order_number": 1, "store_delivery_assignment_id": 1,
         "store_courier_driver_profile_id": 1}).limit(2).to_list(2)
    if len(rows) > 1:
        _conflict("driver_delivery_workflow_identity_conflict")
    if not rows:
        return {"present": False}
    row = rows[0]
    link = normalize_text(row.get("store_delivery_assignment_id"))
    if not link:
        _conflict("driver_delivery_workflow_assignment_missing")
    if (link != assignment["id"] or (normalize_text(row.get("store_courier_driver_profile_id"))
            and normalize_text(row["store_courier_driver_profile_id"]) != assignment["driver_id"])):
        _conflict("driver_delivery_workflow_assignment_conflict")
    row_id = row["_id"]
    if not isinstance(row_id, (ObjectId, str)) and type(row_id) is not int:
        _conflict("driver_delivery_workflow_identity_conflict")
    return {"present": True, "row_id": str(row_id) if isinstance(row_id, ObjectId) else row_id,
        "row_id_type": "object_id" if isinstance(row_id, ObjectId) else "value", "order_number": row["order_number"],
        "assignment_id": link, "driver_profile_id": row.get("store_courier_driver_profile_id")}


async def _workflow_scope(db, owner, assignment, attempt):
    binding = await _workflow_binding(db, owner, assignment)
    pinned = attempt.get("workflow_binding") or {"present": False}
    if pinned.get("present") and binding != pinned:
        _conflict("driver_delivery_workflow_changed")
    if not binding["present"]:
        return None
    row_id = ObjectId(binding["row_id"]) if binding["row_id_type"] == "object_id" else binding["row_id"]
    return {"user_id": owner, "_id": row_id, "order_number": binding["order_number"],
        "store_delivery_assignment_id": assignment["id"],
        "store_courier_driver_profile_id": binding["driver_profile_id"]}


async def noncash_delivery_retry(db, *, owner, actor, driver, assignment, payload):
    user = await db.users.find_one({"id": actor.get("id")})
    if (not user or user.get("role") != "store_driver" or user.get("created_by") != owner
            or user.get("disabled") is True or user.get("is_active") is False):
        raise HTTPException(403, detail={"code": "store_driver_account_required"})
    if not await db.store_drivers.find_one({"user_id": owner, "id": driver["id"],
            "account_user_id": actor["id"], "status": "active"}):
        raise HTTPException(403, detail={"code": "store_driver_profile_not_linked"})
    identity = {"user_id": owner, "driver_id": driver["id"], "assignment_id": assignment["id"]}
    rows = await db.store_delivery_collections.find(identity).limit(2).to_list(2)
    stored = rows[0] if len(rows) == 1 else None
    if not stored or payload.payment_method not in {None, "cash", "bank_transfer", "card_terminal"}:
        _conflict()
    try:
        requirements = collection_requirements(outstanding_amount=stored["amount"], payment_method=payload.payment_method)
    except (StoreDeliveryRuleError, KeyError):
        _conflict()
    # Zero-balance deliveries normalize any legacy payment selection to None.
    # They are still proof-bound, without cash custody or a payment approval.
    method = requirements["payment_method"]
    proof = normalize_text(payload.delivery_proof_reference)
    receipt = normalize_text(payload.receipt_reference)
    if (not stored or not assignment.get("active") or assignment.get("status") != "delivered"
            or assignment.get("driver_id") != driver["id"] or assignment.get("user_id") != owner
            or method not in {None, "bank_transfer", "card_terminal"}
            or stored.get("payment_method") != method or assignment.get("collection_method") != method
            or not proof or (method is not None and not receipt)
            or stored.get("delivery_proof_reference") != proof or assignment.get("delivery_proof_reference") != proof
            or normalize_text(stored.get("receipt_reference")) != receipt
            or normalize_text(assignment.get("receipt_reference")) != receipt
            or normalize_text(stored.get("bank_account_id")) != normalize_text(payload.bank_account_id)
            or payload.physical_cash_amount is not None or payload.physical_cash_confirmed
            or normalize_text(payload.conversation_evidence_reference) != normalize_text(assignment.get("delivery_status_evidence_reference"))
            or stored.get("order_id") != assignment.get("order_id")
            or stored.get("order_number") != assignment.get("order_number")
            or not assignment.get("delivered_at")):
        _conflict()
    evidence = [("store_delivery_delivery_proofs", proof)]
    if receipt:
        evidence.append(("store_delivery_receipts", receipt))
    if normalize_text(payload.conversation_evidence_reference):
        evidence.append(("store_delivery_customer_conversation_evidence", normalize_text(payload.conversation_evidence_reference)))
    for collection, token in evidence:
        if not await db[collection].find_one({**identity, "token": token, "status": "bound"}, {"_id": 1}):
            _conflict()
    # Read the completed capture by its bound identity, before any current
    # carrier guard. An identical replay performs no new provider operation.
    orders = await db.unified_orders.find({"user_id": owner,
        "store_delivery_assignment_id": assignment["id"],
        "store_delivery_driver_id": driver["id"], "store_delivery_status": "delivered",
        "store_delivery_proof_reference": proof, "store_delivery_receipt_reference": receipt or None},
        {"order_id": 1, "order_number": 1, "raw_by_source.salla_direct.id": 1}).limit(2).to_list(2)
    earnings = await db.store_delivery_driver_earnings.find(identity).limit(2).to_list(2)
    if len(orders) != 1 or len(earnings) != 1:
        _conflict()
    _order_scope(orders[0], owner, assignment)
    try:
        if (money(earnings[0].get("amount")) != money(assignment.get("delivery_fee_snapshot"))
                or money(stored.get("amount")) != money(assignment.get("collection_amount"))):
            _conflict()
    except StoreDeliveryRuleError:
        _conflict()
    requirements["review_status"] = stored.get("review_status") or requirements["review_status"]
    return {**_public(assignment),
        "earning_amount": float(money(earnings[0]["amount"])), "collection": requirements,
        "authoritative_outstanding_amount": requirements["amount"]}


async def _current(db, owner, actor, driver, assignment):
    user = await db.users.find_one({"id": actor.get("id")})
    if (not user or user.get("role") != "store_driver" or user.get("created_by") != owner
            or user.get("disabled") is True or user.get("is_active") is False):
        _conflict("store_driver_account_required", 403)
    if not await db.store_drivers.find_one({"user_id": owner, "id": driver["id"],
            "account_user_id": actor["id"], "status": "active"}):
        _conflict("store_driver_profile_not_linked", 403)
    row = await db.store_delivery_assignments.find_one({"user_id": owner,
        "id": assignment["id"], "driver_id": driver["id"], "active": True})
    if (not row or row.get("order_id") != assignment.get("order_id")
            or row.get("order_number") != assignment.get("order_number")):
        _conflict("driver_delivery_status_conflict")
    return row


async def _reserve(db, *, owner, actor, driver, assignment, payload, collection, earning):
    from operational_atomic import operational_owner
    from store_delivery_payment_evidence_routes import canonical_order_for_assignment, authoritative_outstanding_amount

    async def reserve(scoped):
        current = await _current(scoped, owner, actor, driver, assignment)
        if current.get("status") == "delivered":
            return {"replay": await noncash_delivery_retry(scoped, owner=owner, actor=actor,
                driver=driver, assignment=current, payload=payload)}
        if current.get("status") not in {"assigned", "out_for_delivery"}:
            _conflict("driver_delivery_status_conflict")
        order = await canonical_order_for_assignment(scoped, user_id=owner, assignment=current)
        _order_scope(order, owner, current)
        fingerprint = _fingerprint(payload)
        attempt = current.get(ATTEMPT)
        if attempt:
            if (attempt.get("fingerprint") != fingerprint
                    or ("request_payload" in attempt and _saved_request(attempt) is None)):
                _conflict()
            await _workflow_scope(scoped, owner, current, attempt)
            if current["status"] != ("assigned" if _phase(attempt) == "pickup" else "out_for_delivery"):
                _conflict("driver_delivery_status_conflict")
            if attempt.get("state") == "confirmed":
                return {"attempt": attempt, "order": order, "dispatch": False}
            if attempt.get("lease_until", "") > _now().isoformat():
                _pending()
            # A dispatch journal is never reset. Recovery can only GET Salla.
            attempt = {**attempt, "token": uuid4().hex, "state": "reconciling"}
            dispatch = False
        else:
            if collection is None or earning is None:
                _conflict("driver_delivery_capture_missing")
            for name in ("store_delivery_collections", "store_delivery_driver_earnings", "store_delivery_payment_reviews"):
                if await scoped[name].find_one({"user_id": owner, "assignment_id": current["id"]}):
                    _conflict("driver_delivery_capture_already_exists")
            requirements = collection_requirements(outstanding_amount=authoritative_outstanding_amount(order),
                                                   payment_method=payload.payment_method)
            if (requirements["payment_method"] != collection["payment_method"]
                    or money(requirements["amount"]) != money(collection["amount"])
                    or money(current.get("delivery_fee_snapshot")) != money(earning["amount"])):
                _conflict("driver_delivery_amount_changed")
            if collection.get("payment_method") == "bank_transfer":
                from store_delivery_bank_identity import SNAPSHOT_FIELDS, require_collection_bank_snapshot
                await require_collection_bank_snapshot(scoped, owner, {"id": collection.get("bank_account_id"),
                    **{field: collection.get(field) for field in SNAPSHOT_FIELDS}})
            attempt = {"token": uuid4().hex, "fingerprint": fingerprint, "state": "dispatching",
                "phase": "pickup" if current["status"] == "assigned" else "delivery",
                "started_at": _now().isoformat(), "collection": deepcopy(collection), "earning": deepcopy(earning),
                "request_payload": _request_fields(payload),
                "workflow_binding": await _workflow_binding(scoped, owner, current)}
            dispatch = True
        attempt["lease_until"] = (_now() + timedelta(seconds=LEASE_SECONDS)).isoformat()
        changed = await scoped.store_delivery_assignments.update_one({"user_id": owner,
            "id": current["id"], "driver_id": driver["id"], "active": True, "status": current["status"]},
            {"$set": {ATTEMPT: attempt}})
        if changed.matched_count != 1:
            _conflict("driver_delivery_status_conflict")
        return {"attempt": attempt, "order": order, "dispatch": dispatch}
    return await operational_owner(db, owner, reserve, profile=PROFILE)


async def _save_provider_state(db, *, owner, actor, driver, assignment, attempt, confirmed,
                               confirmed_at=None, provider_status=None):
    from operational_atomic import operational_owner
    async def save(scoped):
        current = await _current(scoped, owner, actor, driver, assignment)
        if current.get("status") == "delivered":
            return
        stored = current.get(ATTEMPT) or {}
        if current.get("status") not in {"assigned", "out_for_delivery"}:
            _conflict("driver_delivery_status_conflict")
        if stored.get("token") != attempt["token"]:
            if stored.get("fingerprint") == attempt.get("fingerprint"):
                _pending()
            _conflict()
        if current["status"] != ("assigned" if _phase(stored) == "pickup" else "out_for_delivery"):
            _conflict("driver_delivery_status_conflict")
        updated = {**stored, "state": "confirmed" if confirmed else "uncertain", "lease_until": ""}
        if confirmed:
            updated["confirmed_at"] = confirmed_at or _now().isoformat()
            updated["provider_status"] = provider_status
        changed = await scoped.store_delivery_assignments.update_one({"user_id": owner, "id": current["id"],
            "driver_id": driver["id"], "active": True, "status": current["status"],
            f"{ATTEMPT}.token": attempt["token"]}, {"$set": {ATTEMPT: updated}})
        if changed.matched_count != 1:
            _conflict("driver_delivery_status_conflict")
    await operational_owner(db, owner, save, profile=PROFILE)


async def _persist_pickup(scoped, *, owner, actor, driver, assignment, payload, attempt):
    """Commit the proved pickup and journal the next dispatch in one transaction."""
    from operational_atomic import _DRIVER_CASH_UNSETS
    from store_delivery_payment_evidence_routes import canonical_order_for_assignment

    current = await _current(scoped, owner, actor, driver, assignment)
    if current.get("status") == "delivered":
        return {"replay": await noncash_delivery_retry(scoped, owner=owner, actor=actor,
            driver=driver, assignment=current, payload=payload)}
    stored = current.get(ATTEMPT) or {}
    if stored.get("fingerprint") != _fingerprint(payload):
        _conflict()
    if stored.get("token") != attempt["token"] or _phase(stored) != "pickup":
        _pending()
    if current.get("status") != "assigned" or stored.get("state") != "confirmed":
        _conflict("driver_delivery_status_conflict")
    order = await canonical_order_for_assignment(scoped, user_id=owner, assignment=current)
    scope = _order_scope(order, owner, current)
    workflow_scope = await _workflow_scope(scoped, owner, current, stored)
    now = stored["confirmed_at"]
    observed = stored["provider_status"]
    if observed not in {"delivering", "delivered"}:
        _pending("driver_delivery_provider_confirmation_pending")
    final_confirmed = observed == "delivered"
    following = {**stored, "token": uuid4().hex, "phase": "delivery",
        "state": "confirmed" if final_confirmed else "dispatching",
        "lease_until": "" if final_confirmed else (_now() + timedelta(seconds=LEASE_SECONDS)).isoformat()}
    if not final_confirmed:
        following.pop("confirmed_at", None)
        following.pop("provider_status", None)
    changed = await scoped.store_delivery_assignments.update_one({"user_id": owner,
        "id": current["id"], "driver_id": driver["id"], "active": True, "status": "assigned",
        f"{ATTEMPT}.token": attempt["token"]}, {"$set": {"status": "out_for_delivery",
            "out_for_delivery_at": now, "updated_at": now, "salla_status_slug": observed,
            "salla_status_updated_at": now, ATTEMPT: following},
            "$unset": {key: "" for key in _DRIVER_CASH_UNSETS["store_delivery_assignments"]}})
    if changed.matched_count != 1:
        _conflict("driver_delivery_status_conflict")
    changed = await scoped.unified_orders.update_one(scope, {"$set": {"store_delivery_status": "out_for_delivery",
        "store_delivery_updated_at": now, "store_delivery_salla_status_slug": observed,
        "store_delivery_salla_status_updated_at": now},
        "$unset": {key: "" for key in _DRIVER_CASH_UNSETS["unified_orders"]}})
    if changed.matched_count != 1:
        _conflict("canonical_order_not_found")
    if workflow_scope:
        workflow_result = await scoped.order_review_workflows.update_one(workflow_scope, {"$set": {"stage": "delivering",
            "store_courier_assignment_state": "delivering", "store_courier_picked_up_at": now,
            "store_courier_picked_up_by_id": actor["id"], "updated_at": now},
            "$unset": {key: "" for key in _DRIVER_CASH_UNSETS["order_review_workflows"]}})
        if workflow_result.matched_count != 1:
            _conflict("driver_delivery_workflow_changed")
    await scoped.store_delivery_events.insert_one({"id": str(uuid4()), "user_id": owner,
        "event_type": "store_delivery_out_for_delivery", "assignment_id": current["id"], "driver_id": driver["id"],
        "order_id": current["order_id"], "order_number": current["order_number"], "salla_status_slug": observed,
        "occurred_at": now, "actor_account_user_id": actor["id"]})
    return {"attempt": following, "order": order, "dispatch": not final_confirmed}


async def _persist_delivery(scoped, *, owner, actor, driver, assignment, payload, attempt):
    """Database-only callback; a failure aborts every local completion document."""
    from operational_atomic import _DRIVER_CASH_UNSETS
    from store_delivery_payment_evidence_routes import (canonical_order_for_assignment,
        validate_delivery_proof_reference, validate_receipt_reference, validate_customer_conversation_reference)

    current = await _current(scoped, owner, actor, driver, assignment)
    if current.get("status") == "delivered":
        result = await noncash_delivery_retry(scoped, owner=owner, actor=actor, driver=driver,
            assignment=current, payload=payload)
        return {**result, "_noncash_capture_replayed": True}
    stored = current.get(ATTEMPT) or {}
    if current.get("status") != "out_for_delivery" or stored.get("fingerprint") != _fingerprint(payload):
        _conflict("driver_delivery_status_conflict")
    if stored.get("token") != attempt["token"] or stored.get("state") != "confirmed" or _phase(stored) != "delivery":
        _pending()
    order = await canonical_order_for_assignment(scoped, user_id=owner, assignment=current)
    order_scope = _order_scope(order, owner, current)
    workflow_scope = await _workflow_scope(scoped, owner, current, stored)
    collection, earning = deepcopy(stored["collection"]), deepcopy(stored["earning"])
    # A delivery webhook may settle the balance. Retain the amount captured
    # before dispatch, instead of recalculating collection on a recovery GET.
    requirements = collection_requirements(outstanding_amount=collection["amount"], payment_method=collection["payment_method"])
    if money(current.get("delivery_fee_snapshot")) != money(earning["amount"]):
        _conflict("driver_delivery_amount_changed")
    for name in ("store_delivery_collections", "store_delivery_driver_earnings", "store_delivery_payment_reviews"):
        if await scoped[name].find_one({"user_id": owner, "assignment_id": current["id"]}):
            _conflict("driver_delivery_capture_already_exists")
    proof = collection["delivery_proof_reference"]
    receipt = normalize_text(collection.get("receipt_reference"))
    conversation = normalize_text(payload.conversation_evidence_reference)
    await validate_delivery_proof_reference(scoped, user_id=owner, driver_id=driver["id"],
        assignment_id=current["id"], proof_reference=proof)
    if requirements["receipt_required"]:
        await validate_receipt_reference(scoped, user_id=owner, driver_id=driver["id"],
            assignment_id=current["id"], receipt_reference=receipt)
    if conversation:
        await validate_customer_conversation_reference(scoped, user_id=owner, driver_id=driver["id"],
            assignment_id=current["id"], evidence_reference=conversation)
    now = stored["confirmed_at"]
    collection.update(ACCOUNTING)
    earning.update(ACCOUNTING)
    collection["collected_at"] = now
    earning["earned_at"] = now
    await scoped.store_delivery_driver_earnings.insert_one(earning)
    await scoped.store_delivery_collections.insert_one(collection)
    if requirements["review_status"] == "pending_accountant_review":
        from store_delivery_bank_identity import SNAPSHOT_FIELDS
        await scoped.store_delivery_payment_reviews.insert_one({"id": str(uuid4()), "user_id": owner,
            "assignment_id": current["id"], "driver_id": driver["id"],
            "driver_name_snapshot": current.get("driver_name_snapshot"), "order_id": current["order_id"],
            "order_number": current.get("order_number"), "amount": requirements["amount"],
            "amount_source": collection["amount_source"], "payment_method": requirements["payment_method"],
            "receipt_reference": receipt, "receipt_url": collection.get("receipt_url"),
            "bank_account_id": collection.get("bank_account_id"), "bank_name_snapshot": collection.get("bank_name_snapshot"),
            **{field: collection[field] for field in SNAPSHOT_FIELDS if field in collection},
            "status": "pending", "submitted_at": now})
    patch = {"status": "delivered", "delivered_at": now, "updated_at": now,
        "collection_amount": requirements["amount"], "collection_method": requirements["payment_method"],
        "payment_review_status": requirements["review_status"], "receipt_reference": receipt or None,
        "receipt_url": collection.get("receipt_url"), "delivery_proof_reference": proof,
        "delivery_proof_url": collection.get("delivery_proof_url"), "salla_status_slug": "delivered",
        "salla_status_updated_at": now, **ACCOUNTING}
    order_patch = {"store_delivery_assignment_id": current["id"], "store_delivery_driver_id": driver["id"],
        "store_delivery_status": "delivered", "store_delivery_delivered_at": now,
        "store_delivery_collection_amount": requirements["amount"], "store_delivery_collection_method": requirements["payment_method"],
        "store_delivery_payment_status": "pending_accountant_review" if requirements["amount"] else "not_required",
        "store_delivery_payment_review_status": requirements["review_status"],
        "store_delivery_receipt_reference": receipt or None, "store_delivery_receipt_url": collection.get("receipt_url"),
        "store_delivery_proof_reference": proof, "store_delivery_proof_url": collection.get("delivery_proof_url"),
        "store_delivery_salla_status_slug": "delivered", "store_delivery_salla_status_updated_at": now,
        "store_delivery_updated_at": now}
    evidence = [("store_delivery_delivery_proofs", proof, {"bound_assignment_id": current["id"]})]
    if receipt:
        evidence.append(("store_delivery_receipts", receipt, {}))
    if conversation:
        url = f"/api/store-delivery/evidence/customer-conversation/{conversation}"
        patch.update(delivery_status_evidence_reference=conversation, delivery_status_evidence_url=url,
                     delivery_status_evidence_at=now)
        order_patch.update(store_delivery_status_evidence_reference=conversation,
            store_delivery_status_evidence_url=url, store_delivery_status_evidence_at=now)
        event_id = str(uuid4())
        evidence.append(("store_delivery_customer_conversation_evidence", conversation, {"bound_event_id": event_id}))
        await scoped.store_delivery_events.insert_one({"id": event_id, "user_id": owner,
            "event_type": "store_delivery_status_evidence", "assignment_id": current["id"], "driver_id": driver["id"],
            "order_id": current["order_id"], "order_number": current.get("order_number"), "target_status": "delivered",
            "evidence_reference": conversation, "evidence_url": url, "occurred_at": now,
            "actor_account_user_id": actor["id"]})
    result = await scoped.store_delivery_assignments.find_one_and_update({"user_id": owner,
        "id": current["id"], "driver_id": driver["id"], "active": True, "status": "out_for_delivery",
        f"{ATTEMPT}.token": attempt["token"]}, {"$set": patch,
        "$unset": {**{key: "" for key in _DRIVER_CASH_UNSETS["store_delivery_assignments"]}, ATTEMPT: ""}},
        return_document=True, projection={"_id": 0, "user_id": 0})
    if not result:
        _conflict("driver_delivery_status_conflict")
    for name, token, extra in evidence:
        bound = await scoped[name].update_one({"user_id": owner, "driver_id": driver["id"],
            "assignment_id": current["id"], "token": token, "status": "uploaded"},
            {"$set": {"status": "bound", "bound_at": now, **extra}})
        if bound.matched_count != 1:
            _conflict("driver_delivery_evidence_changed")
    changed = await scoped.unified_orders.update_one(order_scope, {"$set": order_patch,
        "$unset": {key: "" for key in _DRIVER_CASH_UNSETS["unified_orders"]}})
    if changed.matched_count != 1:
        _conflict("canonical_order_not_found")
    if workflow_scope:
        workflow_result = await scoped.order_review_workflows.update_one(workflow_scope, {"$set": {"stage": "delivered",
            "store_courier_assignment_state": "delivered", "store_courier_delivered_at": now,
            "store_courier_delivered_by_id": actor["id"], "updated_at": now},
            "$unset": {key: "" for key in _DRIVER_CASH_UNSETS["order_review_workflows"]}})
        if workflow_result.matched_count != 1:
            _conflict("driver_delivery_workflow_changed")
    await scoped.store_delivery_events.insert_one({"id": str(uuid4()), "user_id": owner,
        "event_type": "store_delivery_delivered", "assignment_id": current["id"], "driver_id": driver["id"],
        "order_id": current["order_id"], "earning_amount": earning["amount"], "collection_amount": requirements["amount"],
        "payment_method": requirements["payment_method"], "amount_source": collection["amount_source"],
        "receipt_reference": receipt or None, "delivery_proof_reference": proof,
        "delivery_proof_url": collection.get("delivery_proof_url"), "salla_status_slug": "delivered", "occurred_at": now})
    return {**_public(result), "earning_amount": earning["amount"], "collection": requirements,
        "authoritative_outstanding_amount": requirements["amount"]}


async def complete_noncash_delivery(db, *, owner, actor, driver, assignment, payload,
                                    sync_status, read_status, collection=None, earning=None, clock=None):
    """Keep provider awaits outside retryable Mongo callbacks; uncertain dispatches only GET."""
    from operational_atomic import operational_owner
    reserved = await _reserve(db, owner=owner, actor=actor, driver=driver, assignment=assignment,
        payload=payload, collection=collection, earning=earning)
    while True:
        if "replay" in reserved:
            return {**reserved["replay"], "_noncash_capture_replayed": True}
        attempt = reserved["attempt"]
        phase = _phase(attempt)
        if attempt["state"] != "confirmed":
            try:
                if reserved["dispatch"]:
                    observed = await sync_status(reserved["order"], phase)
                else:
                    observed = await read_status(reserved["order"], phase)
                slug = observed.get("verified_slug") or observed.get("slug")
                if slug not in ({"delivering", "delivered"} if phase == "pickup" else {"delivered"}):
                    _pending("driver_delivery_provider_confirmation_pending")
            except Exception:
                await _save_provider_state(db, owner=owner, actor=actor, driver=driver,
                    assignment=assignment, attempt=attempt, confirmed=False)
                _pending("driver_delivery_provider_confirmation_pending")
            await _save_provider_state(db, owner=owner, actor=actor, driver=driver, assignment=assignment,
                attempt=attempt, confirmed=True, confirmed_at=clock() if clock else None, provider_status=slug)
        if phase == "delivery":
            break
        async def pickup(scoped):
            return await _persist_pickup(scoped, owner=owner, actor=actor, driver=driver,
                assignment=assignment, payload=payload, attempt=attempt)
        reserved = await operational_owner(db, owner, pickup, profile=PROFILE)
    async def commit(scoped):
        return await _persist_delivery(scoped, owner=owner, actor=actor, driver=driver,
            assignment=assignment, payload=payload, attempt=attempt)
    return await operational_owner(db, owner, commit, profile=PROFILE)
