"""Atomic approval -> native settlement; rejection never moves money.

Operational cash custody is not the driver's financial responsibility. POS
approval moves that responsibility to an exact POS receivable, never a bank.
"""
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Literal
from pydantic import Field

from accounting_atomic import atomic_owner
from accounting_shipping_native import _gate, _rows, _balance, _order_rows, _leg, _post
from accounting_shipping_native_contract import Input, EVIDENCE, EVENTS, digest, now, money, amount, fail
from accounting_shipping_native_setup import pin_setup, require_party
import accounting_driver_payment_port as destination_port
import accounting_shipping_bank_port as bank_port

REVIEWS = "store_delivery_payment_reviews"
COLLECTIONS = "store_delivery_collections"
RECEIPTS = "store_delivery_receipts"


class DriverReviewInput(Input):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=1000)
    destination_financial_id: str | None = Field(default=None, min_length=1, max_length=160)
    settlement_reference: str | None = Field(default=None, min_length=1, max_length=200)


class PosBankInput(Input):
    request_id: str = Field(min_length=8, max_length=120)
    movement_id: str = Field(min_length=1, max_length=160)
    note: str = Field(min_length=3, max_length=1000)


def sealed(row):
    return row and row.get("seal") == digest({k: v for k, v in row.items() if k not in {"_id", "seal"}})


async def single(db, collection, query, code):
    rows = await db[collection].find(query).limit(2).to_list(2)
    if len(rows) != 1:
        fail(code)
    return rows[0]


async def responsibility(db, owner, assignment_id):
    record = await single(db, EVIDENCE, {"user_id": owner, "party_type": "store_driver",
        "assignment_id": assignment_id, "status": "sealed"}, "driver_responsibility_recognition_required")
    if not sealed(record) or not record.get("txn_group_id"):
        fail("driver_responsibility_recognition_required")
    return record


async def review_actor(db, owner, actor_id):
    actor = await _gate(db, owner, actor_id)
    permissions = await db.users.find_one({"id": actor_id}, {
        "role": 1, "extra_permissions": 1, "denied_permissions": 1}) or {}
    permission = "store_delivery.payments.review"
    if (permission in (permissions.get("denied_permissions") or []) or
            (permissions.get("role") not in {"owner", "admin", "accountant"} and
             permission not in (permissions.get("extra_permissions") or []))):
        fail("accountant_permission_required", 403)
    return actor


def validate_destination(result, *, owner, payload, method, value, receipt_hash):
    expected = "bank" if method == "bank_transfer" else "pos_receivable"
    verification = "bank_arrival_confirmed" if expected == "bank" else "pos_transaction_successful"
    if (not isinstance(result, dict) or result.get("user_id") != owner
            or result.get("financial_account_id") != payload.destination_financial_id
            or result.get("destination_kind") != expected or result.get("verification") != verification
            or result.get("currency") != "SAR" or result.get("status") != "verified"
            or result.get("evidence_reference") != payload.settlement_reference
            or result.get("receipt_hash") != receipt_hash or not result.get("source_revision")
            or not result.get("source_namespace") or not result.get("source_record_id")
            or money(result.get("amount")) != value
            or not all(result.get(k) for k in ("entity_type", "entity_id", "sub_account"))):
        fail("driver_payment_destination_proof_invalid")
    if expected == "pos_receivable" and result["entity_type"] in {"bank", "cash", "store_driver", "courier", "revenue", "expense", "tax"}:
        fail("driver_pos_receivable_identity_required")
    if expected == "bank" and result["entity_type"] != "bank":
        fail("driver_bank_identity_required")
    return result["entity_type"], result["entity_id"], result["sub_account"]


async def review_driver_payment(db, *, owner, actor_id, assignment_id, payload):
    async def commit(scoped):
        actor = await review_actor(scoped, owner, actor_id)
        review = await single(scoped, REVIEWS, {"user_id": owner, "assignment_id": assignment_id}, "driver_payment_review_required")
        if not review.get("id"):
            fail("driver_payment_review_identity_required")
        key = digest([owner, "driver-review", review["id"], review.get("revision", 1)])
        request_hash = digest(payload.model_dump(mode="json"))
        prior = await scoped[EVENTS].find_one({"_id": key, "user_id": owner})
        if prior:
            if not sealed(prior):
                fail("driver_payment_review_integrity_failure")
            if prior["decision"] == "approved" and payload.decision == "rejected":
                fail("driver_payment_reversal_contract_required")
            if prior["request_hash"] != request_hash:
                fail("driver_payment_review_idempotency_conflict")
            return {"state": "already_posted" if prior["decision"] == "approved" else "already_rejected",
                    "decision": prior["decision"], "txn_group_id": prior["txn_group_id"]}
        if review.get("status") != "pending":
            fail("driver_payment_review_not_pending")
        evidence = await responsibility(scoped, owner, assignment_id)
        driver = evidence["party_id"]
        setup = await pin_setup(scoped, owner)
        await require_party(scoped, owner, setup, "store_driver", driver)
        assignment = await single(scoped, "store_delivery_assignments", {"user_id": owner,
            "id": assignment_id, "driver_id": driver, "status": "delivered"}, "driver_delivery_required")
        collection = await single(scoped, COLLECTIONS, {"user_id": owner, "assignment_id": assignment_id,
            "driver_id": driver}, "driver_collection_required")
        method = review.get("payment_method")
        value = money(evidence["cod_amount"], positive=True)
        if (method not in {"bank_transfer", "card_terminal"} or collection.get("payment_method") != method
                or review.get("driver_id") != driver or str(review.get("order_id")) != evidence["order_id"]
                or str(assignment.get("order_id")) != evidence["order_id"]
                or collection.get("order_number") != evidence["order_number"]
                or str(collection.get("order_id")) != evidence["order_id"]
                or money(review.get("amount")) != value or money(collection.get("amount")) != value):
            fail("driver_payment_review_source_conflict")
        at = now()
        journal_id, destination = None, None
        receipt_ref = review.get("receipt_reference")
        metadata = {"review_id": review["id"], "review_revision": review.get("revision", 1),
            "assignment_id": assignment_id, "order_id": evidence["order_id"], "order_number": evidence["order_number"],
            "party_type": "store_driver", "party_id": driver, "amount": amount(value), "payment_method": method,
            "receipt_reference": receipt_ref, "approval_actor": actor_id, "approval_at": at,
            "decision": payload.decision, "note": payload.note, "review_idempotency_key": key,
            "action": "receive_cod", "settlement_origin": "driver_payment_review", "evidence_id": evidence["id"]}
        if payload.decision == "approved":
            if not payload.destination_financial_id or not payload.settlement_reference:
                fail("driver_payment_destination_required")
            if not receipt_ref or collection.get("receipt_reference") != receipt_ref:
                fail("driver_payment_receipt_required")
            receipt = await single(scoped, RECEIPTS, {"user_id": owner, "driver_id": driver,
                "assignment_id": assignment_id, "token": receipt_ref, "status": "bound"}, "driver_payment_receipt_required")
            actual_hash = hashlib.sha256(bytes(receipt.get("content") or b"")).hexdigest()
            if not receipt.get("content") or actual_hash != receipt.get("sha256"):
                fail("driver_payment_receipt_integrity_failure")
            destination = await destination_port.require_driver_payment_destination(scoped, owner,
                payment_method=method, financial_account_id=payload.destination_financial_id,
                evidence_reference=payload.settlement_reference, amount=amount(value), receipt_hash=actual_hash)
            target = validate_destination(destination, owner=owner, payload=payload, method=method,
                                          value=value, receipt_hash=actual_hash)
            bank_movement = None
            if method == "bank_transfer":
                if not destination.get("bank_movement_id"):
                    fail("driver_verified_bank_arrival_required")
                bank_movement = await single(scoped, "mz2_daily_movements", {"user_id": owner,
                    "id": destination["bank_movement_id"], "status": "unclassified"}, "driver_verified_bank_arrival_required")
                if (bank_movement.get("direction") != "in" or bank_movement.get("currency") != "SAR"
                        or bank_movement.get("bank_account_id") != payload.destination_financial_id
                        or money(bank_movement.get("amount")) != value
                        or any(bank_movement.get(k) for k in ("receipt_id", "accounting_event_id", "confirmed_provider", "explicit_provider", "suggested_provider"))):
                    fail("driver_verified_bank_arrival_required")
            driver_key = ("store_driver", driver, "cod_receivable")
            rows = await _rows(scoped, owner, [driver_key, target])
            if _balance(_order_rows(rows, evidence), driver_key) < value or _balance(rows, driver_key) < value:
                fail("driver_payment_exceeds_order_responsibility")
            # One verified external transaction cannot discharge two reviews.
            reference_key = digest([owner, "driver-payment-reference", destination["source_namespace"], destination["source_record_id"]])
            if await scoped[EVENTS].find_one({"_id": reference_key}):
                fail("driver_payment_reference_already_consumed")
            metadata["destination"] = destination
            journal = await _post(scoped, owner, actor, key, "driver_payment_review_settlement", at,
                [_leg(target, "debit", value, "destination", "driver_payment_settlement"),
                 _leg(driver_key, "credit", value, "driver", "driver_payment_settlement")], metadata)
            journal_id = journal["txn_group_id"]
            if bank_movement:
                changed = await scoped.mz2_daily_movements.update_one({"_id": bank_movement["_id"], "user_id": owner,
                    "status": "unclassified"}, {"$set": {"status": "accounting_posted", "accounting_event_id": key,
                        "accounting_txn_group_id": journal_id, "accounting_action": "driver_payment_review"}})
                if changed.matched_count != 1:
                    fail("shipping_movement_changed")
            await scoped[EVENTS].insert_one({"_id": reference_key, "user_id": owner, "kind": "driver_payment_reference",
                "review_event_id": key, "txn_group_id": journal_id})
            await scoped[RECEIPTS].update_one({"_id": receipt["_id"], "user_id": owner}, {"$set": {
                "review_status": "approved", "reviewed_at": at, "reviewed_by": actor_id, "financial_txn_group_id": journal_id}})
        event = {"_id": key, "user_id": owner, "kind": "driver_payment_review", **metadata,
                 "request_hash": request_hash, "txn_group_id": journal_id, "destination": destination}
        event["seal"] = digest({k: v for k, v in event.items() if k != "_id"})
        await scoped[EVENTS].insert_one(event)
        if payload.decision == "rejected" and receipt_ref:
            await scoped[RECEIPTS].update_one({"user_id": owner, "driver_id": driver,
                "assignment_id": assignment_id, "token": receipt_ref}, {"$set": {
                    "review_status": "rejected", "reviewed_at": at, "reviewed_by": actor_id}})
        financial = {"financial_handoff_status": "posted" if journal_id else "rejected_no_financial_effect",
                     "financial_event_id": key, "financial_txn_group_id": journal_id}
        changed = await scoped[REVIEWS].update_one({"_id": review["_id"], "user_id": owner, "status": "pending"},
            {"$set": {"status": payload.decision, "reviewed_at": at, "reviewed_by": actor_id,
                       "review_note": payload.note, **financial}})
        if changed.matched_count != 1:
            fail("driver_payment_review_changed")
        confirmed = journal_id is not None
        payment_status = "paid" if confirmed else "payment_evidence_rejected"
        await scoped[COLLECTIONS].update_one({"_id": collection["_id"], "user_id": owner}, {"$set": {
            "review_status": payload.decision, "payment_confirmed": confirmed, "payment_status": payment_status,
            "reviewed_at": at, "reviewed_by": actor_id, **financial}})
        await scoped.store_delivery_assignments.update_one({"_id": assignment["_id"], "user_id": owner}, {"$set": {
            "payment_review_status": payload.decision, "payment_confirmed": confirmed, "payment_status": payment_status,
            "payment_reviewed_at": at, "payment_reviewed_by": actor_id, **financial}})
        await scoped.unified_orders.update_one({"user_id": owner, "order_number": evidence["order_number"]}, {"$set": {
            "store_delivery_payment_review_status": payload.decision, "store_delivery_payment_confirmed": confirmed,
            "store_delivery_payment_status": payment_status, "store_delivery_financial_event_id": key}})
        return {"state": "posted" if confirmed else "rejected", "decision": payload.decision,
                "txn_group_id": journal_id, "review_id": review["id"]}
    return await atomic_owner(db, owner, commit)


async def settle_pos_to_bank(db, *, owner, actor_id, assignment_id, payload):
    """Later, actual bank arrival settles POS AR; never touches driver or fees."""
    async def commit(scoped):
        actor = await review_actor(scoped, owner, actor_id)
        key = digest([owner, "pos-bank", payload.request_id])
        fingerprint = digest([assignment_id, payload.model_dump(mode="json")])
        prior = await scoped[EVENTS].find_one({"_id": key, "user_id": owner})
        if prior:
            if not sealed(prior) or prior.get("request_hash") != fingerprint:
                fail("driver_pos_bank_idempotency_conflict")
            return {"state": "already_posted", "txn_group_id": prior["txn_group_id"]}
        review = await single(scoped, REVIEWS, {"user_id": owner, "assignment_id": assignment_id,
            "status": "approved", "payment_method": "card_terminal"}, "driver_approved_pos_review_required")
        approval = await scoped[EVENTS].find_one({"_id": review.get("financial_event_id"), "user_id": owner})
        if not sealed(approval) or approval.get("decision") != "approved" or not approval.get("txn_group_id"):
            fail("driver_approved_pos_review_required")
        dest = approval["destination"]
        if dest.get("destination_kind") != "pos_receivable":
            fail("driver_pos_receivable_identity_required")
        pos_key = (dest["entity_type"], dest["entity_id"], dest["sub_account"])
        movement = await single(scoped, "mz2_daily_movements", {"user_id": owner,
            "id": payload.movement_id, "status": "unclassified"}, "driver_pos_bank_movement_required")
        if (movement.get("direction") != "in" or movement.get("currency") != "SAR"
                or any(movement.get(k) for k in ("receipt_id", "accounting_event_id", "confirmed_provider", "explicit_provider", "suggested_provider"))):
            fail("driver_pos_bank_movement_invalid")
        try:
            day = datetime.strptime(str(movement.get("movement_date")), "%Y-%m-%d").date()
        except ValueError:
            fail("shipping_movement_date_invalid")
        if day > datetime.now(ZoneInfo("Asia/Riyadh")).date():
            fail("shipping_settlement_in_future")
        bank = await bank_port.require_shipping_bank_identity(scoped, owner, movement.get("bank_account_id"))
        bank_key = (bank["entity_type"], bank["entity_id"], bank["sub_account"])
        if bank_key == pos_key or bank["entity_type"] != "bank":
            fail("driver_pos_bank_identity_required")
        rows = await _rows(scoped, owner, [pos_key, bank_key])
        allocated = [r for r in rows if (r.get("metadata") or {}).get("review_id") == review["id"]]
        value = money(movement.get("amount"), positive=True)
        if value > _balance(allocated, pos_key) or value > _balance(rows, pos_key):
            fail("driver_pos_bank_over_settlement")
        metadata = {"review_id": review["id"], "assignment_id": assignment_id,
            "movement_id": payload.movement_id, "movement_date": movement["movement_date"],
            "pos_destination": dest, "bank_identity": bank, "note": payload.note}
        result = await _post(scoped, owner, actor, key, "driver_pos_bank_settlement", now(),
            [_leg(bank_key, "debit", value, "bank", "driver_pos_bank_settlement"),
             _leg(pos_key, "credit", value, "pos", "driver_pos_bank_settlement")], metadata)
        changed = await scoped.mz2_daily_movements.update_one({"_id": movement["_id"], "user_id": owner,
            "status": "unclassified"}, {"$set": {"status": "accounting_posted", "accounting_event_id": key,
            "accounting_txn_group_id": result["txn_group_id"], "accounting_action": "pos_bank_settlement"}})
        if changed.matched_count != 1:
            fail("shipping_movement_changed")
        record = {"_id": key, "user_id": owner, "kind": "pos_bank_settlement", **metadata,
            "txn_group_id": result["txn_group_id"], "request_hash": fingerprint, "created_at": now()}
        record["seal"] = digest({k: v for k, v in record.items() if k != "_id"})
        await scoped[EVENTS].insert_one(record)
        return {"state": "posted", "txn_group_id": result["txn_group_id"]}
    return await atomic_owner(db, owner, commit)
