"""Append-only, nonfinancial late delivery proof; existing Track F consumes it.

Neither submission nor review calls a financial entrypoint. Original delivery,
collection and C3 records are read-only throughout this lifecycle.
"""
import hashlib
from typing import Literal

from bson.binary import Binary
from fastapi import Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import Field, ValidationError

from accounting_module_contract import accounting_owner_id
from accounting_shipping_native_contract import Input, EVIDENCE, digest, fail, instant, now, money, amount
from operational_atomic import operational_owner

EVENTS = "store_delivery_late_evidence_events_v1"
PROFILE = "driver_late_delivery_evidence"
PERMISSION = "accounting.shipping.contracts.review"
SCHEMA = "mz2.driver.late_delivery_evidence.v1"


class Submission(Input):
    assignment_id: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=8, max_length=120)
    reason: str = Field(min_length=3, max_length=1000)
    captured_at: str | None = Field(default=None, max_length=64)


class Review(Input):
    request_id: str = Field(min_length=8, max_length=120)
    decision: Literal["approved", "rejected"]
    note: str = Field(min_length=3, max_length=1000)


def verify_event(row, owner):
    if (not row or row.get("user_id") != owner or row.get("schema") != SCHEMA
            or row.get("financial_effect") != "none" or row.get("kind") not in {"attachment", "review"}
            or row.get("seal") != digest({k: v for k, v in row.items() if k not in {"_id", "seal"}})
            or row.get("_id") != row.get("id")):
        fail("late_delivery_evidence_integrity_failure")
    expected = digest([owner, "late-attachment", row.get("request_id")]) if row["kind"] == "attachment" else digest(
        [owner, "late-review", row.get("attachment_id")])
    if row["id"] != expected:
        fail("late_delivery_evidence_integrity_failure")
    return row


def seal(row):
    row["seal"] = digest({k: v for k, v in row.items() if k != "_id"})
    return row


async def one(db, collection, query, code):
    rows = await db[collection].find(query).limit(2).to_list(2)
    if len(rows) != 1:
        fail(code)
    return rows[0]


async def driver_actor(db, actor_id):
    actor = await db.users.find_one({"id": actor_id})
    if (not actor or actor.get("role") != "store_driver" or not actor.get("created_by")
            or actor.get("disabled") is True or actor.get("is_active") is False):
        fail("store_driver_account_required", 403)
    owner = actor["created_by"]
    driver = await one(db, "store_drivers", {"user_id": owner, "account_user_id": actor_id,
        "status": "active"}, "store_driver_profile_not_linked")
    return owner, driver


async def reviewer(db, owner, actor_id):
    # User-authorized reuse of an existing explicit grant, never a posting grant.
    from accounting_shipping_native import _actor
    return await _actor(db, owner, actor_id, PERMISSION)


async def pin_source(db, collection, row):
    """Financial transaction CAS only; no historical field or C3 seal changes."""
    query = {key: {"$eq": value} for key, value in row.items() if key != "mz2_shipping_pin"}
    changed = await db[collection].update_one(query, {"$inc": {"mz2_shipping_pin": 1}})
    if changed.matched_count != 1:
        fail("late_delivery_source_changed")


async def source(db, owner, assignment_id, *, driver_id=None, pin=False):
    from order_engine.repository import MongoOrderRepository
    from accounting_shipping_native_evidence import canonical_facts
    from store_delivery_cash_evidence import validate_evidence
    assignment = await one(db, "store_delivery_assignments", {"user_id": owner, "id": assignment_id,
        "active": True, "status": "delivered"}, "late_delivery_assignment_required")
    identity = assignment.get("driver_id")
    if not identity or (driver_id is not None and identity != driver_id):
        fail("late_delivery_driver_mismatch", 403)
    driver = await one(db, "store_drivers", {"user_id": owner, "id": identity, "status": "active"},
                       "store_driver_profile_not_linked")
    driver_owner, current_driver = await driver_actor(db, driver.get("account_user_id"))
    if driver_owner != owner or current_driver["id"] != identity:
        fail("late_delivery_driver_mismatch")
    collection = await one(db, "store_delivery_collections", {"user_id": owner,
        "assignment_id": assignment_id, "driver_id": identity}, "late_delivery_collection_required")
    number = str(assignment.get("order_number") or "")
    snapshot = await MongoOrderRepository(db).financial_delivery_snapshot(user_id=owner, order_number=number)
    watermark = (snapshot or {}).get("g47_salla_snapshot") or {}
    if not snapshot or watermark.get("requires_authoritative_refresh") or watermark.get("cancelled"):
        fail("shipping_source_conflict")
    facts = canonical_facts(snapshot["raw_by_source"]["salla_direct"], order_number=number,
        source_revision=assignment.get("delivered_at"), require_cod=False, require_carrier=False,
        driver_amount=money(collection.get("amount")), driver_delivered_at=assignment.get("delivered_at"))
    if (not collection.get("id") or collection.get("accounting_status") != "operational_only"
            or collection.get("order_number") != number or collection.get("order_id") != assignment.get("order_id")
            or str(assignment.get("order_id")) != facts["order_id"]
            or not driver.get("account_user_id") or (money(collection["amount"]) > 0
                and collection.get("payment_method") not in {"cash", "bank_transfer", "card_terminal"})):
        fail("late_delivery_identity_conflict")
    c3 = validate_evidence(collection, owner, identity)
    workflow = None
    if c3:
        actor_source = {"collection": "store_delivery_collections", "id": collection["id"],
            "actor_id": c3["confirmation_actor"], "delivered_at": c3["confirmed_at"], "seal": c3["seal"]}
    else:
        # Existing completion persists this actor before any account recreation.
        # Today's driver login alone cannot assert a historic delivery actor.
        workflow = await one(db, "order_review_workflows", {"user_id": owner, "order_number": number,
            "store_delivery_assignment_id": assignment_id}, "late_delivery_actor_evidence_required")
        actor_source = {"collection": "order_review_workflows", "assignment_id": assignment_id,
            "actor_id": workflow.get("store_courier_delivered_by_id"),
            "delivered_at": workflow.get("store_courier_delivered_at")}
    if (actor_source["actor_id"] != driver["account_user_id"]
            or instant(actor_source["delivered_at"]) != instant(assignment["delivered_at"])):
        fail("late_delivery_driver_mismatch")
    if pin:
        # Account/workflow editors do not all serialize on the financial owner.
        # Pin the exact snapshot so a concurrent change aborts this transaction.
        await pin_source(db, "store_drivers", driver)
        account = await db.users.find_one({"id": driver["account_user_id"]})
        await pin_source(db, "users", account)
        if workflow is not None:
            await pin_source(db, "order_review_workflows", workflow)
    original = collection.get("delivery_proof_reference")
    result = {"user_id": owner, "order_id": facts["order_id"], "order_number": number,
        "driver_id": identity, "driver_account_user_id": driver["account_user_id"],
        "delivery_actor_source": actor_source,
        "assignment_id": assignment_id, "collection_id": collection["id"],
        "delivered_at": assignment["delivered_at"], "order_created_at": facts["order_created_at"],
        "original_proof_reference": original, "original_proof_present": bool(original),
        "cod_amount": amount(money(collection["amount"])), "currency": facts["currency"],
        "payment_method": collection["payment_method"],
        "cod_custody_amount": amount(money(collection.get("cod_custody_amount"))),
        "c3": {"present": c3 is not None, "id": c3["id"] if c3 else None, "seal": c3["seal"] if c3 else None},
        "canonical_source_hash": facts["source_hash"]}
    return result


async def unrecognized(db, owner, assignment_id):
    if await db[EVIDENCE].find_one({"user_id": owner, "party_type": "store_driver", "assignment_id": assignment_id}):
        fail("late_delivery_already_recognized")


async def artifact(db, owner, attachment):
    # Resolve the existing image implementation only when consuming an image;
    # registering accounting routes must not import operational driver models.
    from store_delivery_payment_evidence_routes import DELIVERY_PROOFS, _detected_type
    row = await one(db, DELIVERY_PROOFS, {"user_id": owner, "token": attachment["proof_reference"],
        "driver_id": attachment["driver_id"], "assignment_id": attachment["assignment_id"]}, "late_delivery_proof_required")
    content = bytes(row.get("content") or b"")
    if (not content or row.get("evidence_kind") != "delivery_proof" or row.get("origin") != "late_attachment"
            or row.get("status") != "uploaded" or row.get("sha256") != attachment["proof_sha256"]
            or hashlib.sha256(content).hexdigest() != attachment["proof_sha256"]
            or row.get("size") != len(content) or row.get("created_at") != attachment["uploaded_at"]
            or _detected_type(content) != row.get("content_type")
            or row.get("created_by_account_user_id") != attachment["uploader_id"]):
        fail("late_delivery_proof_integrity_failure")
    return row


async def item(db, owner, attachment, *, replayed=False):
    verify_event(attachment, owner)
    decision = await db[EVENTS].find_one({"_id": digest([owner, "late-review", attachment["id"]]), "user_id": owner})
    if decision:
        verify_event(decision, owner)
    return {"attachment": {k: v for k, v in attachment.items() if k != "_id"},
        "decision": {k: v for k, v in decision.items() if k != "_id"} if decision else None,
        "state": decision["decision"] if decision else "pending", "replayed": replayed}


async def submit(db, actor_id, payload, content, content_type, filename):
    from store_delivery_payment_evidence_routes import DELIVERY_PROOFS
    owner, _ = await driver_actor(db, actor_id)
    at, proof_hash = now(), hashlib.sha256(content).hexdigest()
    request_hash = digest([payload.model_dump(), actor_id, proof_hash, content_type])
    key = digest([owner, "late-attachment", payload.request_id])

    async def commit(scoped):
        fresh_owner, driver = await driver_actor(scoped, actor_id)
        if fresh_owner != owner:
            fail("late_delivery_owner_conflict", 403)
        previous = await scoped[EVENTS].find_one({"_id": key, "user_id": owner})
        if previous:
            verify_event(previous, owner)
            if previous["request_hash"] != request_hash:
                fail("late_delivery_idempotency_conflict")
            await artifact(scoped, owner, previous)
            return await item(scoped, owner, previous, replayed=True)
        anchor = await source(scoped, owner, payload.assignment_id, driver_id=driver["id"])
        await unrecognized(scoped, owner, payload.assignment_id)
        if instant(at) < instant(anchor["delivered_at"]):
            fail("late_delivery_timestamp_conflict")
        if payload.captured_at and not instant(anchor["order_created_at"]) <= instant(payload.captured_at) <= instant(at):
            fail("late_delivery_timestamp_conflict")
        if await scoped[EVENTS].find_one({"user_id": owner, "kind": "attachment",
                "assignment_id": payload.assignment_id, "proof_sha256": proof_hash}):
            fail("late_delivery_duplicate_evidence")
        if await scoped[EVENTS].find_one({"user_id": owner, "kind": "review",
                "assignment_id": payload.assignment_id, "decision": "approved"}):
            fail("late_delivery_binding_exists")
        token = "late-" + key
        proof = {"token": token, "user_id": owner, "driver_id": driver["id"],
            "assignment_id": payload.assignment_id, "evidence_kind": "delivery_proof", "origin": "late_attachment",
            "filename": filename[:180], "content_type": content_type, "size": len(content),
            "sha256": proof_hash, "content": Binary(content), "status": "uploaded", "created_at": at,
            "created_by_account_user_id": actor_id}
        row = seal({"_id": key, "id": key, "user_id": owner, "kind": "attachment", "schema": SCHEMA,
            **{k: anchor[k] for k in ("order_id", "order_number", "driver_id", "assignment_id", "collection_id")},
            "source": anchor, "source_hash": digest(anchor), "request_id": payload.request_id,
            "request_hash": request_hash, "reason": payload.reason, "captured_at": payload.captured_at,
            "proof_reference": token, "proof_sha256": proof_hash, "uploader_id": actor_id,
            "uploaded_at": at, "attached_at": at, "financial_effect": "none"})
        await scoped[DELIVERY_PROOFS].insert_one(proof)
        await scoped[EVENTS].insert_one(row)
        return await item(scoped, owner, row)
    return await operational_owner(db, owner, commit, profile=PROFILE)


async def review(db, owner, actor_id, attachment_id, payload):
    async def commit(scoped):
        await reviewer(scoped, owner, actor_id)
        attachment = verify_event(await scoped[EVENTS].find_one({"_id": attachment_id, "user_id": owner,
            "kind": "attachment"}), owner)
        await artifact(scoped, owner, attachment)
        key = digest([owner, "late-review", attachment_id])
        request_hash = digest([payload.model_dump(), actor_id])
        prior = await scoped[EVENTS].find_one({"_id": key, "user_id": owner})
        if prior:
            verify_event(prior, owner)
            if prior["request_hash"] != request_hash:
                fail("late_delivery_review_conflict")
            return await item(scoped, owner, attachment, replayed=True)
        anchor = await source(scoped, owner, attachment["assignment_id"])
        if digest(anchor) != attachment["source_hash"]:
            fail("late_delivery_source_changed")
        await unrecognized(scoped, owner, attachment["assignment_id"])
        if payload.decision == "approved" and await scoped[EVENTS].find_one({"user_id": owner,
                "kind": "review", "assignment_id": attachment["assignment_id"], "decision": "approved"}):
            fail("late_delivery_binding_exists")
        at = now()
        if instant(at) < instant(attachment["attached_at"]):
            fail("late_delivery_timestamp_conflict")
        decision = seal({"_id": key, "id": key, "user_id": owner, "schema": SCHEMA, "kind": "review",
            **{k: attachment[k] for k in ("order_id", "order_number", "driver_id", "assignment_id", "collection_id",
                "proof_reference", "proof_sha256", "uploader_id", "source_hash", "uploaded_at", "attached_at")},
            "attachment_id": attachment_id, "attachment_seal": attachment["seal"],
            "request_id": payload.request_id, "request_hash": request_hash, "decision": payload.decision,
            "note": payload.note, "reviewer_id": actor_id, "reviewed_at": at, "permission": PERMISSION,
            "financial_effect": "none"})
        await scoped[EVENTS].insert_one(decision)
        return await item(scoped, owner, attachment)
    return await operational_owner(db, owner, commit, profile=PROFILE)


async def approved_source(db, owner, assignment_id):
    decisions = await db[EVENTS].find({"user_id": owner, "kind": "review", "assignment_id": assignment_id,
        "decision": "approved"}).limit(2).to_list(2)
    if len(decisions) != 1:
        fail("shipping_driver_bound_delivery_proof_required")
    decision = verify_event(decisions[0], owner)
    attachment = verify_event(await db[EVENTS].find_one({"_id": decision["attachment_id"],
        "user_id": owner, "kind": "attachment"}), owner)
    anchor = await source(db, owner, assignment_id, pin=True)
    if (anchor["original_proof_present"] or digest(anchor) != attachment["source_hash"]
            or decision["attachment_seal"] != attachment["seal"]
            or any(decision[k] != attachment[k] for k in ("assignment_id", "driver_id", "order_id", "collection_id",
                "proof_reference", "proof_sha256", "source_hash", "uploader_id"))):
        fail("late_delivery_source_changed")
    proof = await artifact(db, owner, attachment)
    previous = await db[EVIDENCE].find_one({"user_id": owner, "party_type": "store_driver", "assignment_id": assignment_id})
    provenance = {"attachment_id": attachment["id"], "attachment_seal": attachment["seal"],
        "approval_id": decision["id"], "approval_seal": decision["seal"], "proof_sha256": proof["sha256"],
        "uploader_id": attachment["uploader_id"], "reviewer_id": decision["reviewer_id"],
        "uploaded_at": attachment["uploaded_at"], "attached_at": attachment["attached_at"],
        "reviewed_at": decision["reviewed_at"], "original_delivered_at": anchor["delivered_at"],
        "original_c3": anchor["c3"], "original_proof_reference": anchor["original_proof_reference"]}
    if previous and previous.get("late_delivery_proof") != provenance:
        fail("late_delivery_already_recognized")
    return proof, provenance


async def listing(db, owner, *, driver_id=None, assignment_id=None):
    query = {"user_id": owner, "kind": "attachment"}
    if driver_id:
        query["driver_id"] = driver_id
    if assignment_id:
        query["assignment_id"] = assignment_id
    rows = await db[EVENTS].find(query).sort("attached_at", -1).limit(251).to_list(251)
    if len(rows) > 250:
        fail("late_delivery_scope_too_large")
    return {"items": [await item(db, owner, row) for row in rows]}


def install_driver_routes(router, db, current_user, read_image):
    @router.post("/late-delivery")
    async def upload(assignment_id: str = Form(...), request_id: str = Form(...), reason: str = Form(...),
                     file: UploadFile = File(...), captured_at: str | None = Form(None), user=Depends(current_user)):
        try:
            payload = Submission(assignment_id=assignment_id, request_id=request_id, reason=reason, captured_at=captured_at)
        except ValidationError as exc:
            raise HTTPException(422, detail={"code": "late_delivery_input_invalid"}) from exc
        await driver_actor(db, user.get("id"))
        content, content_type = await read_image(file)
        return await submit(db, user.get("id"), payload, content, content_type, file.filename or "delivery-proof")

    @router.get("/late-delivery")
    async def history(assignment_id: str | None = Query(None), user=Depends(current_user)):
        owner, driver = await driver_actor(db, user.get("id"))
        return await listing(db, owner, driver_id=driver["id"], assignment_id=assignment_id)


def install_review_routes(router, db, current_user):
    base = "/accounting-module/shipping-v2/late-delivery-evidence"

    async def scope(user, *, require_review=True):
        from accounting_write_control import fresh_actor
        from accounting_shipping_native import _actor
        actor = await fresh_actor(db, user)
        owner = accounting_owner_id(actor)
        await _actor(db, owner, actor["id"], "accounting.shipping.view")
        if require_review:
            await reviewer(db, owner, actor["id"])
        return owner, actor["id"]

    @router.get(base)
    async def queue(user=Depends(current_user)):
        owner, _ = await scope(user, require_review=False)
        return await listing(db, owner)

    @router.get(base + "/{attachment_id}/original")
    async def original(attachment_id: str, user=Depends(current_user)):
        owner, _ = await scope(user)
        attachment = verify_event(await db[EVENTS].find_one({"_id": attachment_id, "user_id": owner,
            "kind": "attachment"}), owner)
        proof = await artifact(db, owner, attachment)
        return Response(bytes(proof["content"]), media_type=proof["content_type"],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.post(base + "/{attachment_id}/review")
    async def decide(attachment_id: str, payload: Review, user=Depends(current_user)):
        owner, actor_id = await scope(user)
        return await review(db, owner, actor_id, attachment_id, payload)
