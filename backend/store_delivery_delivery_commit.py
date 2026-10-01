"""Atomic local cash-delivery capture; external sync and finance stay outside."""
from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException

from operational_atomic import operational_owner, _DRIVER_CASH_UNSETS
from store_delivery_cash_evidence import build_cash_evidence, validate_evidence, validate_cash_confirmation
from store_delivery_domain import collection_requirements, money
from store_delivery_payment_evidence_routes import (
    authoritative_outstanding_amount, validate_delivery_proof_reference,
)


def conflict(code="driver_delivery_status_conflict", status=409):
    raise HTTPException(status, detail={"code": code})


async def cash_delivery_retry(db, *, owner, actor, driver, assignment, payment_method, actual_amount,
                              confirmed, proof_reference, receipt_reference=None, bank_account_id=None):
    """Read back only an identical captured cash delivery, without any effects."""
    user = await db.users.find_one({"id": actor.get("id")})
    if (not user or user.get("role") != "store_driver" or user.get("created_by") != owner
            or user.get("disabled") is True or user.get("is_active") is False):
        conflict("store_driver_account_required", 403)
    if not await db.store_drivers.find_one({"user_id": owner, "id": driver["id"],
            "account_user_id": actor["id"], "status": "active"}):
        conflict("store_driver_profile_not_linked", 403)
    rows = await db.store_delivery_collections.find({"user_id": owner, "driver_id": driver["id"],
        "assignment_id": assignment["id"]}).limit(2).to_list(2)
    stored = rows[0] if len(rows) == 1 else None
    if not stored or assignment.get("status") != "delivered" or payment_method != "cash":
        conflict()
    evidence = validate_evidence(stored, owner, driver["id"])
    requirements = collection_requirements(outstanding_amount=stored["amount"], payment_method="cash")
    actual = validate_cash_confirmation(requirements, actual_amount, confirmed)
    if (not evidence or evidence["physical_cash_amount"] != actual or evidence["confirmation_actor"] != actor["id"]
            or evidence["delivery_proof_reference"] != proof_reference
            or (stored.get("receipt_reference") or "") != (receipt_reference or "")
            or (stored.get("bank_account_id") or "") != (bank_account_id or "")):
        conflict("driver_physical_cash_idempotency_conflict")
    return {**{k: v for k, v in assignment.items() if k not in {"_id", "user_id"}},
        "earning_amount": assignment.get("delivery_fee_snapshot"), "collection": requirements,
        "authoritative_outstanding_amount": requirements["amount"], "physical_cash_evidence": evidence,
        "_cash_capture_replayed": True}


async def commit_cash_delivery(db, *, owner, actor, driver, assignment, collection, earning,
                               actual_amount, confirmed_at, salla_slug):
    """Commit expected responsibility and distinct actual evidence together.

    The callback is database-only and uses an allowlisted operational capability.
    It cannot invoke financial entrypoints, expose a raw session, or change a
    pause/activation field. A failed or repeated delivery never deletes prior rows.
    """
    event_id = str(uuid4())

    async def commit(scoped):
        fresh_actor = await scoped.users.find_one({"id": actor.get("id")})
        if (not fresh_actor or fresh_actor.get("role") != "store_driver"
                or fresh_actor.get("created_by") != owner or fresh_actor.get("disabled") is True
                or fresh_actor.get("is_active") is False):
            conflict("store_driver_account_required", 403)
        fresh_driver = await scoped.store_drivers.find_one({"user_id": owner, "id": driver["id"],
            "account_user_id": actor["id"], "status": "active"})
        if not fresh_driver:
            conflict("store_driver_profile_not_linked", 403)
        current = await scoped.store_delivery_assignments.find_one({"user_id": owner,
            "id": assignment["id"], "driver_id": driver["id"], "active": True})
        if current and current.get("status") == "delivered":
            return await cash_delivery_retry(scoped, owner=owner, actor=actor, driver=fresh_driver,
                assignment=current, payment_method="cash", actual_amount=actual_amount, confirmed=True,
                proof_reference=collection["delivery_proof_reference"], receipt_reference=collection.get("receipt_reference"),
                bank_account_id=collection.get("bank_account_id"))
        if (not current or current.get("status") != "out_for_delivery" or current.get("order_id") != assignment.get("order_id")
                or current.get("order_number") != assignment.get("order_number")
                or money(current.get("delivery_fee_snapshot")) != money(earning["amount"])):
            conflict()
        for name in ("store_delivery_collections", "store_delivery_driver_earnings", "store_delivery_payment_reviews"):
            if await scoped[name].find_one({"user_id": owner, "assignment_id": assignment["id"]}):
                conflict("driver_delivery_capture_already_exists")
        orders = await scoped.unified_orders.find({"user_id": owner,
            "order_number": current["order_number"]}).limit(2).to_list(2)
        order = orders[0] if len(orders) == 1 else None
        raw_id = (((order or {}).get("raw_by_source") or {}).get("salla_direct") or {}).get("id")
        source_id = raw_id if raw_id is not None else (order or {}).get("order_id")
        if (not order or str(source_id) != str(current["order_id"])
                or (order.get("order_id") is not None and str(order["order_id"]) != str(current["order_id"]))):
            conflict("canonical_order_identity_conflict")
        requirements = collection_requirements(outstanding_amount=authoritative_outstanding_amount(order), payment_method="cash")
        if (requirements["payment_method"] != "cash" or money(requirements["amount"]) != money(collection["amount"])
                or money(requirements["cod_custody_amount"]) != money(collection["cod_custody_amount"])):
            conflict("driver_delivery_amount_changed")
        proof = collection["delivery_proof_reference"]
        await validate_delivery_proof_reference(scoped, user_id=owner, driver_id=driver["id"],
                                               assignment_id=assignment["id"], proof_reference=proof)
        accounting = {"accounting_status": "operational_only", "ledger_txn_group_id": None,
            "accounting_operation_id": None, "financial_handoff_status": "pending_mz2_driver_balance_link",
            "financial_source": "store_delivery_operational"}
        stored = {**deepcopy(collection), **accounting}
        stored["physical_cash_evidence"] = build_cash_evidence(owner=owner, driver=fresh_driver,
            actor=fresh_actor, assignment=current, collection=stored, actual_amount=actual_amount, confirmed_at=confirmed_at)
        await scoped.store_delivery_driver_earnings.insert_one({**deepcopy(earning), **accounting})
        await scoped.store_delivery_collections.insert_one(stored)
        patch = {"status": "delivered", "delivered_at": confirmed_at, "updated_at": confirmed_at,
            "collection_amount": requirements["amount"], "collection_method": "cash",
            "payment_review_status": requirements["review_status"], "receipt_reference": None,
            "receipt_url": None, "delivery_proof_reference": proof, "delivery_proof_url": stored["delivery_proof_url"],
            "salla_status_slug": salla_slug, "salla_status_updated_at": confirmed_at, **accounting}
        result = await scoped.store_delivery_assignments.find_one_and_update({"user_id": owner,
            "id": assignment["id"], "driver_id": driver["id"], "active": True, "status": "out_for_delivery"},
            {"$set": patch, "$unset": {k: "" for k in _DRIVER_CASH_UNSETS["store_delivery_assignments"]}},
            return_document=True, projection={"_id": 0, "user_id": 0})
        if not result:
            conflict()
        changed = await scoped.store_delivery_delivery_proofs.update_one({"user_id": owner, "driver_id": driver["id"],
            "assignment_id": assignment["id"], "token": proof, "status": "uploaded"},
            {"$set": {"status": "bound", "bound_at": confirmed_at, "bound_assignment_id": assignment["id"]}})
        if changed.matched_count != 1:
            conflict("delivery_proof_invalid")
        order_patch = {"store_delivery_assignment_id": assignment["id"], "store_delivery_driver_id": driver["id"],
            "store_delivery_status": "delivered", "store_delivery_delivered_at": confirmed_at,
            "store_delivery_collection_amount": requirements["amount"], "store_delivery_collection_method": "cash",
            "store_delivery_payment_status": "cash_in_driver_custody", "store_delivery_payment_review_status": requirements["review_status"],
            "store_delivery_receipt_reference": None, "store_delivery_receipt_url": None,
            "store_delivery_proof_reference": proof, "store_delivery_proof_url": stored["delivery_proof_url"],
            "store_delivery_salla_status_slug": salla_slug, "store_delivery_salla_status_updated_at": confirmed_at,
            "store_delivery_updated_at": confirmed_at}
        changed = await scoped.unified_orders.update_one({"user_id": owner,
            "order_number": order["order_number"]},
            {"$set": order_patch, "$unset": {k: "" for k in _DRIVER_CASH_UNSETS["unified_orders"]}})
        if changed.matched_count != 1:
            conflict("canonical_order_not_found")
        await scoped.order_review_workflows.update_one({"user_id": owner, "order_number": assignment["order_number"],
            "store_delivery_assignment_id": assignment["id"]}, {"$set": {"stage": "delivered",
                "store_courier_assignment_state": "delivered", "store_courier_delivered_at": confirmed_at,
                "store_courier_delivered_by_id": actor["id"], "updated_at": confirmed_at},
                "$unset": {k: "" for k in _DRIVER_CASH_UNSETS["order_review_workflows"]}})
        await scoped.store_delivery_events.insert_one({"id": event_id, "user_id": owner,
            "event_type": "store_delivery_delivered", "assignment_id": assignment["id"], "driver_id": driver["id"],
            "order_id": assignment["order_id"], "earning_amount": earning["amount"],
            "collection_amount": requirements["amount"], "payment_method": "cash", "amount_source": stored["amount_source"],
            "receipt_reference": None, "delivery_proof_reference": proof, "delivery_proof_url": stored["delivery_proof_url"],
            "salla_status_slug": salla_slug, "occurred_at": confirmed_at, "physical_cash_evidence_id": stored["id"]})
        return {**result, "earning_amount": earning["amount"], "collection": requirements,
            "authoritative_outstanding_amount": requirements["amount"], "physical_cash_evidence": stored["physical_cash_evidence"]}

    return await operational_owner(db, owner, commit, profile="driver_cash_delivery")
