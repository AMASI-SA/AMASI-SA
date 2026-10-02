"""Real HTTP and isolated Mongo acceptance for append-only late delivery proof.

The imported fixture owns a UUID database and monitors forbidden Legacy IO.
Evidence approval is operational; only the existing native recognition route
may subsequently post, using its unchanged permission and financial gates.
"""
import asyncio
from datetime import datetime
import hashlib
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient

from test_mz2_shipping_native import db, OWNER, AT, source, rate
from accounting_ledger_v2 import read_reporting_entries_v2
from accounting_shipping_native_contract import EVIDENCE
from accounting_shipping_native_setup import save_setup
from accounting_shipping_native_routes import BASE, install_shipping_native_routes
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router

DRIVER = "late-driver-user"
REVIEWER = "late-evidence-reviewer"
UPLOAD = "/store-delivery/evidence/late-delivery"
REVIEW = BASE + "/late-delivery-evidence"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-late-delivery-image" * 8


async def seed_delivery(database, number="1", *, original=False, absent_c3=False):
    await source(database, number)
    assignment = {"user_id": OWNER, "id": "assignment-" + number, "driver_id": "driver-f",
        "order_id": "salla-" + number, "order_number": number, "status": "delivered",
        "active": True, "delivered_at": AT, "delivery_fee_snapshot": "20.00"}
    collection = {"user_id": OWNER, "id": "collection-" + number,
        "assignment_id": assignment["id"], "driver_id": "driver-f", "order_id": assignment["order_id"],
        "order_number": number, "payment_method": "cash", "amount": "500.00", "cod_custody_amount": "500.00",
        "accounting_status": "operational_only", "review_status": "not_required", "collected_at": AT}
    if original or absent_c3:
        from store_delivery_cash_evidence import build_cash_evidence
        token = "original-proof-" + number
        if original:
            collection["delivery_proof_reference"] = token
        else:
            collection["delivery_proof_reference"] = None
        driver = await database.store_drivers.find_one({"user_id": OWNER, "id": "driver-f"})
        collection["physical_cash_evidence"] = build_cash_evidence(owner=OWNER, driver=driver,
            actor={"id": DRIVER}, assignment=assignment, collection=collection,
            actual_amount="475.00", confirmed_at=AT)
        if original:
            await database.store_delivery_delivery_proofs.insert_one({"user_id": OWNER,
                "driver_id": "driver-f", "assignment_id": assignment["id"], "token": token,
                "status": "bound", "bound_assignment_id": assignment["id"], "content": PNG + b"original",
                "sha256": hashlib.sha256(PNG + b"original").hexdigest(), "created_at": AT})
    await database.store_delivery_assignments.insert_one(assignment)
    await database.store_delivery_collections.insert_one(collection)
    await database.order_review_workflows.insert_one({"user_id": OWNER, "order_number": number,
        "store_delivery_assignment_id": assignment["id"], "store_courier_delivered_by_id": DRIVER,
        "store_courier_delivered_at": AT})
    return assignment["id"]


@pytest_asyncio.fixture
async def late(db):
    await db.store_drivers.update_one({"id": "driver-f", "user_id": OWNER},
        {"$set": {"account_user_id": DRIVER}})
    await db.users.insert_many([
        {"id": DRIVER, "role": "store_driver", "created_by": OWNER, "is_active": True},
        {"id": REVIEWER, "role": "employee", "created_by": OWNER, "is_active": True,
         "accounting_permissions": ["accounting.shipping.view", "accounting.shipping.contracts.review"]},
        {"id": "foreign-owner", "role": "owner", "is_active": True,
         "accounting_permissions": ["accounting.shipping.view", "accounting.shipping.contracts.review"]},
        {"id": "other-driver-user", "role": "store_driver", "created_by": OWNER, "is_active": True},
    ])
    await db.store_drivers.insert_one({"user_id": OWNER, "id": "other-driver",
        "account_user_id": "other-driver-user", "status": "active", "name": "Other driver"})
    await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
    async def current_user(x_actor: str = Header(default=OWNER)):
        return await db.users.find_one({"id": x_actor}, {"_id": 0})
    app = FastAPI()
    app.include_router(make_store_delivery_payment_evidence_router(db, current_user))
    router = APIRouter()
    install_shipping_native_routes(router, db, current_user)
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated.test") as http:
        yield SimpleNamespace(db=db, http=http)


async def upload(late, *, assignment="assignment-1", key="late-upload-request-1", content=PNG, actor=DRIVER):
    return await late.http.post(UPLOAD, headers={"x-actor": actor},
        data={"assignment_id": assignment, "request_id": key, "reason": "Original delivery image attached late",
              "captured_at": AT}, files={"file": ("delivery.png", content, "image/png")})


async def attach(late, **kwargs):
    response = await upload(late, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


async def review(late, attachment, *, decision="approved", key="late-review-request-1", actor=REVIEWER):
    return await late.http.post(REVIEW + "/" + attachment["id"] + "/review",
        headers={"x-actor": actor}, json={"request_id": key, "decision": decision,
        "note": "Reviewed original image and matched delivered order"})


async def recognize(late, assignment="assignment-1", actor=OWNER):
    return await late.http.post(BASE + "/recognize-driver", headers={"x-actor": actor},
        json={"assignment_id": assignment})


async def financial(late):
    return await read_reporting_entries_v2(late.db, user_id=OWNER,
        effective_before="2027-01-01T00:00:00Z")


async def original_sources(late):
    return {name: await late.db[name].find({"origin": {"$ne": "late_attachment"}}).sort("_id").to_list(None) for name in
        ("store_delivery_assignments", "store_delivery_collections", "store_delivery_delivery_proofs")}


@pytest.mark.asyncio
async def test_late_upload_review_then_existing_native_recognition_once_at_original_period(late):
    await seed_delivery(late.db)
    before_sources, before_financial = await original_sources(late), await financial(late)
    denied = await recognize(late)
    assert denied.status_code == 409, denied.text
    uploaded = await attach(late)
    attachment = uploaded["attachment"]
    assert uploaded["state"] == "pending" and uploaded["decision"] is None
    assert attachment["user_id"] == OWNER and attachment["driver_id"] == "driver-f"
    assert attachment["assignment_id"] == "assignment-1" and attachment["collection_id"] == "collection-1"
    assert attachment["order_id"] == "salla-1" and attachment["order_number"] == "1"
    assert attachment["uploader_id"] == DRIVER and attachment["financial_effect"] == "none"
    assert attachment["proof_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert attachment["proof_reference"] and attachment["source"]
    assert attachment["uploaded_at"] and attachment["attached_at"]
    assert (await recognize(late)).status_code == 409
    downloaded = await late.http.get(REVIEW + "/" + attachment["id"] + "/original",
        headers={"x-actor": REVIEWER})
    assert downloaded.status_code == 200 and downloaded.content == PNG
    approved = await review(late, attachment)
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved" and approved.json()["decision"]
    assert await financial(late) == before_financial
    assert await original_sources(late) == before_sources
    # No historical C3 attestation or original collection reference is fabricated.
    assert "physical_cash_evidence" not in (await late.db.store_delivery_collections.find_one({}))
    assert (await recognize(late, actor=REVIEWER)).status_code == 403
    posted = await recognize(late)
    assert posted.status_code == 200, posted.text
    replay = await recognize(late)
    assert replay.status_code == 200 and replay.json()["txn_group_id"] == posted.json()["txn_group_id"]
    rows = [r for r in await financial(late) if r["txn_group_id"] == posted.json()["txn_group_id"]]
    driver = [r for r in rows if r["entity_type"] == "store_driver"]
    assert len(driver) == 1 and driver[0]["side"] == "debit" and driver[0]["amount"] == "500.00"
    assert all(datetime.fromisoformat(r["effective_at"].replace("Z", "+00:00")) == datetime.fromisoformat(AT) for r in rows)
    assert await late.db[EVIDENCE].count_documents({"user_id": OWNER}) == 1
    assert (await upload(late, key="after-recognition-request", content=PNG + b"later")).status_code == 409
    assert (await review(late, attachment)).status_code == 200


@pytest.mark.asyncio
async def test_original_bound_proof_and_sealed_c3_remain_immutable(late):
    await seed_delivery(late.db, original=True)
    # A sealed original C3 supplies its own historic actor and timestamp.
    await late.db.order_review_workflows.delete_many({"user_id": OWNER})
    before_sources, before_financial = await original_sources(late), await financial(late)
    attachment = (await attach(late))["attachment"]
    assert (await review(late, attachment)).status_code == 200
    assert await original_sources(late) == before_sources
    assert await financial(late) == before_financial
    result = await recognize(late)
    assert result.status_code == 200, result.text
    collection = await late.db.store_delivery_collections.find_one({"assignment_id": "assignment-1"})
    original = before_sources["store_delivery_collections"][0]
    assert collection["physical_cash_evidence"] == original["physical_cash_evidence"]
    assert collection["delivery_proof_reference"] == "original-proof-1"
    evidence = await late.db[EVIDENCE].find_one({"assignment_id": "assignment-1"})
    assert evidence["delivery_proof_reference"] == "original-proof-1"


@pytest.mark.asyncio
async def test_rejection_never_posts_or_allows_recognition(late):
    await seed_delivery(late.db)
    before = await financial(late)
    attachment = (await attach(late))["attachment"]
    rejected = await review(late, attachment, decision="rejected")
    assert rejected.status_code == 200 and rejected.json()["state"] == "rejected", rejected.text
    assert (await recognize(late)).status_code == 409
    assert await financial(late) == before


@pytest.mark.asyncio
async def test_paused_evidence_approval_preserves_controls_and_writer_stays_423(late):
    await seed_delivery(late.db)
    await late.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    controls = await late.db.mz2_atomic_owners.find_one({"_id": OWNER})
    settings = await late.db.settings.find_one({"user_id": OWNER})
    before = await financial(late)
    attachment = (await attach(late))["attachment"]
    approved = await review(late, attachment)
    assert approved.status_code == 200, approved.text
    after_controls = await late.db.mz2_atomic_owners.find_one({"_id": OWNER})
    assert {k: v for k, v in after_controls.items() if k != "revision"} == {k: v for k, v in controls.items() if k != "revision"}
    assert after_controls["revision"] >= controls["revision"]
    assert await late.db.settings.find_one({"user_id": OWNER}) == settings
    assert (await recognize(late)).status_code == 423
    assert await financial(late) == before


@pytest.mark.asyncio
async def test_upload_replay_and_conflicting_request_or_duplicate_bytes(late):
    await seed_delivery(late.db)
    first = await attach(late)
    repeat = await attach(late)
    assert repeat["replayed"] is True and repeat["attachment"] == first["attachment"]
    assert (await upload(late, content=PNG + b"changed")).status_code == 409
    assert (await upload(late, key="another-request-same-image")).status_code == 409


@pytest.mark.asyncio
async def test_concurrent_identical_upload_and_approval_have_one_event_each(late):
    from store_delivery_late_evidence import EVENTS
    await seed_delivery(late.db)
    before = await financial(late)
    results = await asyncio.gather(upload(late), upload(late))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert results[0].json()["attachment"] == results[1].json()["attachment"]
    assert sorted(r.json()["replayed"] for r in results) == [False, True]
    attachment = results[0].json()["attachment"]
    count = await late.db[EVENTS].count_documents({"user_id": OWNER})
    results = await asyncio.gather(review(late, attachment), review(late, attachment))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert sorted(r.json()["replayed"] for r in results) == [False, True]
    assert await late.db[EVENTS].count_documents({"user_id": OWNER}) == count + 1
    assert (await review(late, attachment, key="another-approval-request")).status_code == 409
    assert (await review(late, attachment, decision="rejected")).status_code == 409
    assert await financial(late) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["other-driver-user", "foreign-owner"])
async def test_wrong_driver_or_owner_cannot_upload_or_read_original(late, actor):
    await seed_delivery(late.db)
    response = await upload(late, actor=actor)
    assert response.status_code in (403, 404), response.text
    attachment = (await attach(late))["attachment"]
    response = await late.http.get(REVIEW + "/" + attachment["id"] + "/original", headers={"x-actor": actor})
    assert response.status_code == (409 if actor == "foreign-owner" else 403), response.text
    assert (await review(late, attachment, actor=actor)).status_code == (409 if actor == "foreign-owner" else 403)


@pytest.mark.asyncio
async def test_driver_list_is_assignment_scoped_and_accountant_list_is_owner_scoped(late):
    await seed_delivery(late.db)
    attachment = (await attach(late))["attachment"]
    listed = await late.http.get(UPLOAD, params={"assignment_id": "assignment-1"}, headers={"x-actor": DRIVER})
    assert listed.status_code == 200 and attachment["id"] in listed.text
    listed = await late.http.get(REVIEW, headers={"x-actor": REVIEWER})
    assert listed.status_code == 200 and attachment["id"] in listed.text
    listed = await late.http.get(REVIEW, headers={"x-actor": "foreign-owner"})
    assert listed.status_code == 200 and listed.json()["items"] == [], listed.text
    listed = await late.http.get(UPLOAD, params={"assignment_id": "assignment-1"}, headers={"x-actor": "other-driver-user"})
    assert listed.status_code == 200 and listed.json()["items"] == [], listed.text


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"order_id": "different-order"}, {"driver_id": "other-driver"}, {"user_id": "foreign-owner"}])
async def test_collection_identity_mismatch_rejects_attachment_without_financial_effect(late, changes):
    await seed_delivery(late.db)
    await late.db.store_delivery_collections.update_one({"assignment_id": "assignment-1"}, {"$set": changes})
    before = await financial(late)
    response = await upload(late)
    assert response.status_code in (404, 409), response.text
    assert await financial(late) == before


@pytest.mark.asyncio
async def test_absent_proof_original_c3_survives_late_approval_and_recognition(late):
    from store_delivery_cash_evidence import ABSENT_PROOF_SCHEMA, validate_evidence
    await seed_delivery(late.db, absent_c3=True)
    before = await late.db.store_delivery_collections.find_one({"assignment_id": "assignment-1"})
    c3 = validate_evidence(before, OWNER, "driver-f")
    assert c3["schema"] == ABSENT_PROOF_SCHEMA and c3["delivery_proof_state"] == "absent_at_delivery"
    assert c3["physical_cash_amount"] == "475.00" and c3["cod_amount"] == "500.00"
    attachment = (await attach(late))["attachment"]
    assert attachment["source"]["c3"]["seal"] == c3["seal"]
    assert (await review(late, attachment)).status_code == 200
    assert (await recognize(late)).status_code == 200
    after = await late.db.store_delivery_collections.find_one({"assignment_id": "assignment-1"})
    assert after["physical_cash_evidence"] == before["physical_cash_evidence"]
    assert after["delivery_proof_reference"] is None
    assert validate_evidence(after, OWNER, "driver-f") == c3


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [{"content": PNG + b"corrupted"}, {"sha256": "0" * 64},
    {"content_type": "text/html"},
    {"user_id": "foreign-owner"}, {"driver_id": "other-driver"}, {"assignment_id": "assignment-2"}])
async def test_tampered_proof_never_downloads_approves_or_recognizes(late, mutation):
    await seed_delivery(late.db)
    attachment = (await attach(late))["attachment"]
    before = await financial(late)
    await late.db.store_delivery_delivery_proofs.update_one({"token": attachment["proof_reference"]}, {"$set": mutation})
    response = await late.http.get(REVIEW + "/" + attachment["id"] + "/original", headers={"x-actor": REVIEWER})
    assert response.status_code == 409, response.text
    assert (await review(late, attachment)).status_code == 409
    assert (await recognize(late)).status_code == 409
    assert await financial(late) == before


@pytest.mark.asyncio
async def test_two_candidates_can_only_bind_one_approved_proof(late):
    from store_delivery_late_evidence import EVENTS
    await seed_delivery(late.db)
    first, second = await asyncio.gather(attach(late),
        attach(late, key="second-distinct-candidate", content=PNG + b"second"))
    results = await asyncio.gather(review(late, first["attachment"]),
        review(late, second["attachment"], key="second-candidate-review"))
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    assert await late.db[EVENTS].count_documents({"user_id": OWNER, "kind": "review", "decision": "approved"}) == 1


@pytest.mark.asyncio
async def test_upload_event_failure_rolls_back_new_proof_and_owner_revision(late, monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorCollection
    from store_delivery_late_evidence import EVENTS
    await seed_delivery(late.db)
    sources, rows = await original_sources(late), await financial(late)
    controls = await late.db.mz2_atomic_owners.find_one({"_id": OWNER})
    insert = AsyncIOMotorCollection.insert_one
    async def fail_event(collection, document, *args, **kwargs):
        if collection.name == EVENTS:
            raise RuntimeError("synthetic late attachment event failure")
        return await insert(collection, document, *args, **kwargs)
    monkeypatch.setattr(AsyncIOMotorCollection, "insert_one", fail_event)
    with pytest.raises(RuntimeError, match="synthetic late attachment event failure"):
        await upload(late)
    assert await late.db.store_delivery_delivery_proofs.count_documents({"origin": "late_attachment"}) == 0
    assert await late.db[EVENTS].count_documents({"user_id": OWNER}) == 0
    assert await original_sources(late) == sources and await financial(late) == rows
    assert await late.db.mz2_atomic_owners.find_one({"_id": OWNER}) == controls


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [{"content": PNG + b"after-approval-corruption"}, {"content_type": "text/html"}])
async def test_approved_late_proof_tampering_still_blocks_existing_writer(late, mutation):
    await seed_delivery(late.db)
    attachment = (await attach(late))["attachment"]
    assert (await review(late, attachment)).status_code == 200
    await late.db.store_delivery_delivery_proofs.update_one({"token": attachment["proof_reference"]},
        {"$set": mutation})
    before = await financial(late)
    response = await recognize(late)
    assert response.status_code == 409, response.text
    assert await financial(late) == before


@pytest.mark.asyncio
async def test_review_permission_is_fresh_and_separate_from_read_and_post(late):
    await seed_delivery(late.db)
    attachment = (await attach(late))["attachment"]
    await late.db.users.update_one({"id": REVIEWER},
        {"$set": {"accounting_permissions": ["accounting.shipping.view"]}})
    before = await financial(late)
    listed = await late.http.get(REVIEW, headers={"x-actor": REVIEWER})
    assert listed.status_code == 200 and attachment["id"] in listed.text
    original = await late.http.get(REVIEW + "/" + attachment["id"] + "/original", headers={"x-actor": REVIEWER})
    assert original.status_code == 403
    assert (await review(late, attachment)).status_code == 403
    assert (await recognize(late, actor=REVIEWER)).status_code == 403
    assert await financial(late) == before


@pytest.mark.asyncio
async def test_owner_can_read_metadata_but_needs_explicit_grant_for_original_and_review(late):
    await seed_delivery(late.db)
    attachment = (await attach(late))["attachment"]
    response = await late.http.get(REVIEW, headers={"x-actor": OWNER})
    assert response.status_code == 200 and attachment["id"] in response.text
    response = await late.http.get(REVIEW + "/" + attachment["id"] + "/original", headers={"x-actor": OWNER})
    assert response.status_code == 403, response.text
    assert (await review(late, attachment, actor=OWNER)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_workflow", "missing_actor", "wrong_time", "new_login"])
async def test_late_attachment_requires_immutable_original_delivery_actor(late, change):
    from store_delivery_late_evidence import EVENTS
    await seed_delivery(late.db)
    actor = DRIVER
    if change == "missing_workflow":
        await late.db.order_review_workflows.delete_many({"user_id": OWNER})
    elif change == "missing_actor":
        await late.db.order_review_workflows.update_one({"user_id": OWNER},
            {"$unset": {"store_courier_delivered_by_id": ""}})
    elif change == "wrong_time":
        await late.db.order_review_workflows.update_one({"user_id": OWNER},
            {"$set": {"store_courier_delivered_at": "2026-09-03T12:01:00+00:00"}})
    else:
        actor = "replacement-login"
        await late.db.users.insert_one({"id": actor, "role": "store_driver", "created_by": OWNER, "is_active": True})
        await late.db.store_drivers.update_one({"user_id": OWNER, "id": "driver-f"},
            {"$set": {"account_user_id": actor}})
    before = await financial(late)
    response = await upload(late, actor=actor)
    assert response.status_code == 409, response.text
    assert await late.db[EVENTS].count_documents({"user_id": OWNER}) == 0
    assert await late.db.store_delivery_delivery_proofs.count_documents({"origin": "late_attachment"}) == 0
    assert await financial(late) == before


@pytest.mark.asyncio
async def test_original_actor_change_between_upload_and_review_is_rejected(late):
    await seed_delivery(late.db)
    attachment = (await attach(late))["attachment"]
    await late.db.order_review_workflows.update_one({"user_id": OWNER},
        {"$set": {"store_courier_delivered_by_id": "different-historical-actor"}})
    before = await financial(late)
    response = await review(late, attachment)
    assert response.status_code == 409, response.text
    assert await financial(late) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("approved_first", [False, True])
async def test_cancelled_source_blocks_review_or_recognition(late, approved_first):
    await seed_delivery(late.db)
    attachment = (await attach(late))["attachment"]
    if approved_first:
        assert (await review(late, attachment)).status_code == 200
    await late.db.unified_orders.update_one({"user_id": OWNER, "order_number": "1"},
        {"$set": {"raw_by_source.salla_direct.status": {"slug": "cancelled"}}})
    before = await financial(late)
    response = await recognize(late) if approved_first else await review(late, attachment)
    assert response.status_code == 409, response.text
    assert await financial(late) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden", ["journal", "original_collection", "original_proof"])
async def test_operational_profile_cannot_write_finance_or_patch_originals_even_when_error_caught(late, forbidden):
    from accounting_ledger_v2 import GROUPS_COLLECTION
    from operational_atomic import operational_owner
    from store_delivery_late_evidence import PROFILE
    await seed_delivery(late.db, original=True)
    before, rows = await original_sources(late), await financial(late)
    controls = await late.db.mz2_atomic_owners.find_one({"_id": OWNER})
    async def attempt(scoped):
        await scoped.store_delivery_delivery_proofs.insert_one({"user_id": OWNER,
            "token": "must-rollback", "origin": "late_attachment", "evidence_kind": "delivery_proof",
            "status": "uploaded", "driver_id": "driver-f", "assignment_id": "assignment-1"})
        try:
            if forbidden == "journal":
                await scoped[GROUPS_COLLECTION].insert_one({"user_id": OWNER, "id": "prohibited"})
            else:
                name = "store_delivery_collections" if forbidden == "original_collection" else "store_delivery_delivery_proofs"
                await scoped[name].update_one({"user_id": OWNER}, {"$set": {"delivery_proof_reference": "must-not-change"}})
        except HTTPException:
            pass  # The capability must retain failure despite a caught exception.
    with pytest.raises(HTTPException, match="operational_financial_write_forbidden"):
        await operational_owner(late.db, OWNER, attempt, profile=PROFILE)
    assert await late.db.store_delivery_delivery_proofs.count_documents({"token": "must-rollback"}) == 0
    assert await original_sources(late) == before and await financial(late) == rows
    assert await late.db.mz2_atomic_owners.find_one({"_id": OWNER}) == controls


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["store_drivers", "order_review_workflows"])
async def test_concurrent_source_mutation_aborts_recognition_and_retry_fails_closed(late, monkeypatch, target):
    import store_delivery_late_evidence as evidence
    from accounting_shipping_native_contract import EVENTS as SHIPPING_EVENTS
    await seed_delivery(late.db)
    await late.db.users.insert_one({"id": "replacement-concurrent-driver", "role": "store_driver",
        "created_by": OWNER, "is_active": True})
    attachment = (await attach(late))["attachment"]
    assert (await review(late, attachment)).status_code == 200
    before_rows = await financial(late)
    history_names = (evidence.EVENTS, EVIDENCE, SHIPPING_EVENTS)
    before_history = {name: await late.db[name].find({}).sort("_id").to_list(None) for name in history_names}
    controls = await late.db.mz2_atomic_owners.find_one({"_id": OWNER})
    original_source, original_pin = evidence.source, evidence.pin_source
    attempts, mutations = 0, 0

    async def counted_source(*args, **kwargs):
        nonlocal attempts
        if kwargs.get("pin"):
            attempts += 1
        return await original_source(*args, **kwargs)

    async def racing_pin(scoped, collection, row):
        nonlocal mutations
        if collection == target and mutations == 0:
            # An actual committed competing write outside the financial session,
            # after its source snapshot but immediately before its original CAS.
            change = ({"account_user_id": "replacement-concurrent-driver"} if target == "store_drivers"
                else {"store_courier_delivered_by_id": "different-historical-actor"})
            updated = await late.db[collection].update_one({"_id": row["_id"]}, {"$set": change})
            assert updated.modified_count == 1
            mutations += 1
        return await original_pin(scoped, collection, row)

    monkeypatch.setattr(evidence, "source", counted_source)
    monkeypatch.setattr(evidence, "pin_source", racing_pin)
    response = await recognize(late)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "late_delivery_driver_mismatch"
    assert mutations == 1 and attempts >= 2, "No real transaction retry observed"
    assert await financial(late) == before_rows
    assert {name: await late.db[name].find({}).sort("_id").to_list(None) for name in history_names} == before_history
    assert await late.db.mz2_atomic_owners.find_one({"_id": OWNER}) == controls
    # Earlier successful CAS pins from the aborted attempt must roll back too.
    for name in ("store_drivers", "users", "order_review_workflows"):
        assert await late.db[name].count_documents({"mz2_shipping_pin": {"$exists": True}}) == 0
