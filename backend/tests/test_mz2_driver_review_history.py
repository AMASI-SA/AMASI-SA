"""Read-only native driver decision history against isolated real Mongo.

All financial decisions use the delivered writer through its real HTTP route.
The imported fixture rejects remote Mongo and monitors Legacy collection access.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from accounting_shipping_native_routes import BASE, install_shipping_native_routes
from accounting_shipping_native_contract import EVENTS, digest
from accounting_shipping_native import recognize_cod
from accounting_atomic import atomic_owner
from accounting_ledger_v2 import reverse_journal_v2
from store_delivery_payment_resubmission_routes import make_store_delivery_payment_resubmission_router
from test_mz2_driver_pos_manual_review import (
    manual, snapshot, delivered, approval, post,
)
from test_mz2_driver_payment_review import driver_delivery, accept
from test_mz2_bank_evidence_adapters import imported
from test_mz2_shipping_native import OWNER
import accounting_driver_payment_review as review_writer


HISTORY = BASE + "/driver-payment-history"


@pytest_asyncio.fixture
async def history(manual):
    actor = {"id": OWNER, "role": "owner"}

    async def current_user():
        return actor

    app, router = FastAPI(), APIRouter()
    install_shipping_native_routes(router, manual.db, current_user)
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://isolated") as http:
        yield SimpleNamespace(native=manual, db=manual.db, http=http, actor=actor)


async def page(history, **params):
    response = await history.http.get(HISTORY, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def decided(history, number="1", *, decision="approved", method="card_terminal"):
    if method == "card_terminal":
        assignment = await delivered(history.native, number)
        payload = approval(history.native)
    else:
        assignment = await driver_delivery(history.db, method, number)
        await recognize_cod(history.db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
        payload = None
        if decision == "approved":
            await history.db.mz2_financial_accounts.update_one(
                {"user_id": OWNER, "id": "bank-f"},
                {"$set": {"account_type": "bank", "currency": "SAR", "status": "active",
                          "name": "Synthetic native bank"}}, upsert=True,
            )
            movement = await imported(history.db, OWNER, "bank-f", value="500.00",
                                      direction="in", day="2026-09-03", reference="HISTORY-" + number)
            payload = accept(method, reference=movement["id"]).model_dump(mode="json")
    if decision == "rejected":
        payload = {"decision": "rejected", "note": "Accountant rejected original receipt " + number}
    response = await post(history.native, assignment, payload)
    assert response.status_code == 200, response.text
    event = await history.db[EVENTS].find_one({"user_id": OWNER, "kind": "driver_payment_review",
                                              "assignment_id": assignment})
    assert event is not None
    return assignment, event, payload


def reseal(event):
    event["seal"] = digest({key: value for key, value in event.items() if key not in {"_id", "seal"}})
    return event


@pytest.mark.asyncio
async def test_empty_native_history_is_explicit_and_read_only(history):
    indexes = await history.db[EVENTS].index_information()
    assert indexes["ix_shipping_driver_review_history"]["key"] == [
        ("user_id", 1), ("kind", 1), ("approval_at", -1), ("_id", -1)]
    before = await snapshot(history.db)
    response = await history.http.get(HISTORY)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["schema"] == "mz2.driver.review_history.v1"
    assert data["ledger_source"] == "accounting_v2"
    assert data["scope"] == "native_v2_decisions_only"
    assert data["read_only"] is True
    assert data["items"] == [] and data["next_cursor"] is None
    assert data["has_more"] is False
    assert data["coverage"]["native_only"] is True
    assert data["coverage"]["unlinked_current_decisions"] == 0
    assert data["coverage"]["unlinked_current_review_ids"] == []
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["card_terminal", "bank_transfer"])
async def test_approved_history_uses_real_journal_and_canonical_destination(history, method):
    assignment, event, payload = await decided(history, method=method)
    # Identical approval retries must not create duplicate history either.
    replay = await post(history.native, assignment, payload)
    assert replay.status_code == 200 and replay.json()["state"] == "already_posted"
    before = await snapshot(history.db)
    result = await page(history)
    assert len(result["items"]) == 1
    row = result["items"][0]
    assert row["id"] == event["_id"]
    assert row["status"] == "approved" and row["review_revision"] == 1
    assert row["review_id"] == event["review_id"]
    assert row["party_id"] == row["driver_id"] == "driver-f"
    assert row["order_number"] == "1" and row["amount"] == "500.00"
    assert row["payment_method"] == method and row["receipt_reference"] == "receipt-1"
    assert row["reviewed_by"] == OWNER and row["reviewed_at"] == event["approval_at"]
    assert row["financial_handoff_status"] == "posted"
    assert row["financial_txn_group_id"] == event["txn_group_id"]
    assert row["journal_reversed"] is False
    target = row["destination"]
    if method == "card_terminal":
        assert (target["entity_type"], target["entity_id"], target["sub_account"]) == (
            "asset", history.native.fact["id"], "other_receivable")
        assert target["display_name"] == history.native.fact["display_name"]
        assert target["destination_kind"] == "pos_receivable"
    else:
        assert (target["entity_type"], target["entity_id"], target["sub_account"]) == (
            "bank", "bank-f", "main")
        assert target["destination_kind"] == "bank"
    assert await snapshot(history.db) == before


async def resubmit_rejected(history, assignment):
    await history.db.store_drivers.update_one({"user_id": OWNER, "id": "driver-f"},
        {"$set": {"account_user_id": "history-driver"}})
    await history.db.store_delivery_assignments.update_one({"user_id": OWNER, "id": assignment},
        {"$set": {"active": True}})
    content = b"Distinct replacement receipt for actual native review"
    await history.db.store_delivery_receipts.insert_one({
        "user_id": OWNER, "driver_id": "driver-f", "assignment_id": assignment,
        "token": "history-replacement", "status": "uploaded", "content": content,
        "sha256": hashlib.sha256(content).hexdigest(),
    })
    app = FastAPI()

    async def driver_user():
        return {"id": "history-driver", "role": "store_driver", "created_by": OWNER}

    app.include_router(make_store_delivery_payment_resubmission_router(history.db, driver_user))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://isolated") as http:
        resubmitted = await http.post("/store-delivery/app/payment-review/" + assignment + "/resubmit",
                                    json={"receipt_reference": "history-replacement"})
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["status"] == "pending" and resubmitted.json()["revision"] == 2


@pytest.mark.asyncio
async def test_rejected_revision_survives_real_resubmission_and_later_approval(history):
    assignment, rejected, _ = await decided(history, decision="rejected")
    await resubmit_rejected(history, assignment)
    original_receipt = await history.db.store_delivery_receipts.find_one({"token": "receipt-1"})
    assert original_receipt["status"] == "superseded"
    during = await page(history)
    assert [(item["review_revision"], item["status"]) for item in during["items"]] == [(1, "rejected")]
    assert during["items"][0]["financial_handoff_status"] == "rejected_no_financial_effect"
    assert during["items"][0]["financial_txn_group_id"] is None
    assert during["items"][0]["destination"] is None
    pending = await history.native.http.get("/store-delivery/payment-review/pending")
    assert pending.status_code == 200, pending.text
    assert [(item["revision"], item["status"]) for item in pending.json()["items"]] == [(2, "pending")]
    approved = await post(history.native, assignment, approval(history.native))
    assert approved.status_code == 200, approved.text
    before = await snapshot(history.db)
    result = await page(history)
    by_revision = {item["review_revision"]: item for item in result["items"]}
    assert len(result["items"]) == 2 and set(by_revision) == {1, 2}
    assert by_revision[1]["id"] == rejected["_id"]
    assert by_revision[1]["status"] == "rejected" and by_revision[1]["receipt_reference"] == "receipt-1"
    assert by_revision[2]["status"] == "approved" and by_revision[2]["receipt_reference"] == "history-replacement"
    assert by_revision[2]["financial_txn_group_id"] == approved.json()["txn_group_id"]
    assert result["coverage"]["unlinked_current_decisions"] == 0
    assert result["coverage"]["missing_native_revisions"] == []
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("next_status", ["pending", "approved"])
async def test_missing_prior_native_revision_is_explicit_after_resubmission(history, next_status):
    assignment, rejected, _ = await decided(history, decision="rejected")
    await resubmit_rejected(history, assignment)
    if next_status == "approved":
        approved = await post(history.native, assignment, approval(history.native))
        assert approved.status_code == 200, approved.text
    healthy = await page(history)
    assert healthy["coverage"]["unlinked_current_decisions"] == 0
    assert healthy["coverage"]["missing_native_revisions"] == []
    await history.db[EVENTS].delete_one({"user_id": OWNER, "_id": rejected["_id"]})
    before = await snapshot(history.db)
    result = await page(history)
    assert result["scope"] == "native_v2_decisions_only"
    assert result["coverage"]["unlinked_current_decisions"] == 1
    assert result["coverage"]["unlinked_current_review_ids"] == [rejected["review_id"]]
    assert result["coverage"]["missing_native_revisions"] == [
        {"review_id": rejected["review_id"], "revision": 1}]
    assert [(row["review_revision"], row["status"]) for row in result["items"]] == (
        [(2, "approved")] if next_status == "approved" else [])
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_pending_and_unlinked_operational_history_are_not_financial_decisions(history):
    await delivered(history.native, "pending")
    await history.db.store_delivery_payment_reviews.insert_one({
        "user_id": OWNER, "id": "pre-v2-closed", "assignment_id": "old-assignment",
        "driver_id": "driver-f", "status": "approved", "payment_method": "card_terminal",
        "amount": "123.00", "financial_handoff_status": "posted", "financial_txn_group_id": "unsupported",
    })
    await history.db.store_delivery_payment_reviews.insert_one({
        "user_id": "foreign-owner", "id": "foreign-pre-v2", "assignment_id": "foreign-old",
        "driver_id": "driver-f", "status": "rejected", "payment_method": "card_terminal",
    })
    before = await snapshot(history.db)
    result = await page(history)
    assert result["items"] == [] and result["scope"] == "native_v2_decisions_only"
    assert result["coverage"]["unlinked_current_decisions"] == 1
    assert result["coverage"]["unlinked_current_review_ids"] == ["pre-v2-closed"]
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_timestamp_ties_paginate_without_duplicate_or_missing_decisions(history, monkeypatch):
    # Freeze only the writer clock: each row and journal is still produced normally.
    monkeypatch.setattr(review_writer, "now", lambda: "2026-10-01T08:15:30.000000+00:00")
    events = []
    for number, decision, method in [
        ("1", "approved", "card_terminal"),
        ("2", "rejected", "card_terminal"),
        ("3", "rejected", "bank_transfer"),
    ]:
        _, event, _ = await decided(history, number, decision=decision, method=method)
        events.append(event)
    before = await snapshot(history.db)
    rows, cursor = [], None
    for index in range(3):
        result = await page(history, limit=1, **({"cursor": cursor} if cursor else {}))
        assert len(result["items"]) == 1
        rows.extend(result["items"])
        assert result["has_more"] is (index < 2)
        cursor = result["next_cursor"]
        assert bool(cursor) is (index < 2)
    assert [row["id"] for row in rows] == sorted((event["_id"] for event in events), reverse=True)
    assert len({row["id"] for row in rows}) == 3
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_filters_and_cursor_cannot_cross_owner_or_filter_scope(history):
    await decided(history, "1", decision="rejected")
    await decided(history, "2", decision="approved")
    await decided(history, "3", decision="rejected", method="bank_transfer")
    first = await page(history, limit=1, payment_method="card_terminal", driver_id="driver-f")
    assert first["has_more"] and first["next_cursor"]
    cursor = first["next_cursor"]
    result = await page(history, limit=1, payment_method="card_terminal", driver_id="driver-f", cursor=cursor)
    assert len(result["items"]) == 1 and result["items"][0]["id"] != first["items"][0]["id"]
    rejected = await page(history, decision="rejected", payment_method="bank_transfer", driver_id="driver-f")
    assert [(row["status"], row["payment_method"]) for row in rejected["items"]] == [("rejected", "bank_transfer")]
    assert (await page(history, driver_id="another-driver"))["items"] == []
    for changes in [{"payment_method": "bank_transfer", "driver_id": "driver-f"},
                    {"payment_method": "card_terminal", "driver_id": "another-driver"},
                    {"payment_method": "card_terminal", "driver_id": "driver-f", "decision": "approved"}]:
        response = await history.http.get(HISTORY, params={"limit": 1, "cursor": cursor, **changes})
        assert response.status_code in {409, 422}, response.text
    await history.db.users.insert_one({"id": "foreign-owner", "role": "owner", "is_active": True})
    history.actor["id"] = "foreign-owner"
    assert (await page(history))["items"] == []
    response = await history.http.get(HISTORY, params={"limit": 1, "cursor": cursor,
        "payment_method": "card_terminal", "driver_id": "driver-f"})
    assert response.status_code in {403, 409, 422}, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 251}, {"limit": "not-an-int"},
    {"payment_method": "cash"}, {"decision": "pending"},
    {"cursor": "not-a-valid-cursor"}, {"cursor": "x" * 10000},
])
async def test_invalid_history_query_fails_closed_without_writes(history, params):
    before = await snapshot(history.db)
    response = await history.http.get(HISTORY, params=params)
    assert response.status_code in {409, 422}, response.text
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["event_seal", "event_id", "event_metadata", "foreign_journal", "journal_audit", "wrong_journal"])
async def test_corrupt_native_decision_or_journal_never_becomes_history(history, mutation):
    _, event, _ = await decided(history)
    if mutation == "event_seal":
        await history.db[EVENTS].update_one({"_id": event["_id"]}, {"$set": {"note": "tampered"}})
    elif mutation == "event_id":
        await history.db[EVENTS].delete_one({"_id": event["_id"]})
        event["_id"] = "f" * 64  # _id is deliberately outside the existing seal.
        await history.db[EVENTS].insert_one(event)
    elif mutation == "event_metadata":
        event["amount"] = "499.00"
        await history.db[EVENTS].replace_one({"_id": event["_id"]}, reseal(event))
    elif mutation == "foreign_journal":
        await history.db.accounting_journal_groups_v2.update_one({"txn_group_id": event["txn_group_id"]},
            {"$set": {"user_id": "foreign-owner"}})
    elif mutation == "journal_audit":
        await history.db.accounting_audit_log_v2.delete_many({"txn_group_id": event["txn_group_id"]})
    else:
        original = await history.db.accounting_journal_groups_v2.find_one({"user_id": OWNER,
            "txn_type": "opening_balance"})
        event["txn_group_id"] = original["txn_group_id"]
        await history.db[EVENTS].replace_one({"_id": event["_id"]}, reseal(event))
    before = await snapshot(history.db)
    response = await history.http.get(HISTORY)
    assert response.status_code in {403, 409}, response.text
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_rejection_cannot_claim_a_financial_journal(history):
    _, rejected, _ = await decided(history, decision="rejected")
    _, approved_event, _ = await decided(history, "2")
    rejected["txn_group_id"] = approved_event["txn_group_id"]
    await history.db[EVENTS].replace_one({"_id": rejected["_id"]}, reseal(rejected))
    before = await snapshot(history.db)
    response = await history.http.get(HISTORY)
    assert response.status_code == 409, response.text
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_rejection_cannot_hide_an_existing_journal_for_the_same_review_key(history):
    _, event, _ = await decided(history)
    # An intact journal exists for this exact native decision key. A corrupted
    # event cannot redefine that posted decision as having no financial effect.
    event.update({"decision": "rejected", "txn_group_id": None, "destination": None})
    await history.db[EVENTS].replace_one({"_id": event["_id"]}, reseal(event))
    before = await snapshot(history.db)
    response = await history.http.get(HISTORY)
    assert response.status_code == 409, response.text
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_owner_is_resolved_from_fresh_user_and_native_read_permission(history):
    await decided(history)
    await history.db.users.insert_one({"id": "history-viewer", "role": "employee", "created_by": OWNER,
        "is_active": True, "accounting_permissions": ["accounting.shipping.view"]})
    history.actor.update({"id": "history-viewer", "role": "owner", "created_by": "forged-owner",
                          "is_owner": True, "accounting_permissions": ["accounting.shipping.view"]})
    assert len((await page(history))["items"]) == 1
    await history.db.users.update_one({"id": "history-viewer"},
        {"$set": {"accounting_permissions": ["accounting.home.view"]}})
    before = await snapshot(history.db)
    response = await history.http.get(HISTORY)
    assert response.status_code == 403, response.text
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"disabled": True}, {"is_active": False}])
async def test_disabled_actor_cannot_read_history_using_stale_claims(history, changes):
    await decided(history, decision="rejected")
    await history.db.users.update_one({"id": OWNER}, {"$set": changes})
    before = await snapshot(history.db)
    response = await history.http.get(HISTORY)
    assert response.status_code == 403, response.text
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_paused_read_preserves_controls_and_does_not_require_current_receipt_or_identity(history):
    assignment, event, payload = await decided(history)
    await history.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    await history.db.store_delivery_receipts.update_one({"token": "receipt-1"}, {"$set": {"status": "superseded"}})
    await history.db.mz2_opening_facts_v2.update_one({"id": history.native.fact["id"]},
        {"$set": {"status": "inactive", "display_name": "Later edited operational name"}})
    before = await snapshot(history.db)
    result = await page(history)
    assert len(result["items"]) == 1
    assert result["items"][0]["financial_txn_group_id"] == event["txn_group_id"]
    assert result["items"][0]["destination"]["display_name"] == history.native.fact["display_name"]
    assert await snapshot(history.db) == before
    # History availability never unlocks an existing financial route.
    attempted_write = await post(history.native, assignment, payload)
    assert attempted_write.status_code == 423, attempted_write.text
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_later_pos_bank_settlement_is_not_a_second_review_decision(history):
    assignment, approved_event, _ = await decided(history)
    await history.db.mz2_financial_accounts.update_one({"user_id": OWNER, "id": "bank-f"},
        {"$set": {"account_type": "bank", "currency": "SAR", "status": "active"}}, upsert=True)
    movement = await imported(history.db, OWNER, "bank-f", value="500.00", direction="in",
                              day="2026-10-01", reference="HISTORY-POS-LATER")
    settled = await history.native.http.post(
        "/store-delivery/payment-review/" + assignment + "/pos-bank-settlement",
        json={"request_id": "history-pos-bank-settlement", "movement_id": movement["id"],
              "note": "Actual separate native bank movement"})
    assert settled.status_code == 200, settled.text
    assert await history.db[EVENTS].count_documents({"kind": "pos_bank_settlement"}) == 1
    assert settled.json()["txn_group_id"] != approved_event["txn_group_id"]
    before = await snapshot(history.db)
    result = await page(history)
    assert len(result["items"]) == 1
    row = result["items"][0]
    assert row["id"] == approved_event["_id"]
    assert row["financial_txn_group_id"] == approved_event["txn_group_id"]
    assert row["destination"]["destination_kind"] == "pos_receivable"
    assert row["destination"]["entity_type"] == "asset"
    assert await snapshot(history.db) == before


@pytest.mark.asyncio
async def test_later_native_reversal_keeps_original_decision_and_sets_flag(history):
    _, event, _ = await decided(history)
    original = deepcopy(event)
    at = datetime.now(timezone.utc).isoformat()

    async def reverse(scoped):
        return await reverse_journal_v2(scoped._db, user_id=OWNER, actor_id=OWNER, actor_name=OWNER,
            original_txn_group_id=event["txn_group_id"], effective_at=at,
            reason="Synthetic existing native reversal contract", mongo_session=scoped._session)

    await atomic_owner(history.db, OWNER, reverse)
    before = await snapshot(history.db)
    result = await page(history)
    assert len(result["items"]) == 1
    row = result["items"][0]
    assert row["status"] == "approved" and row["financial_txn_group_id"] == event["txn_group_id"]
    assert row["journal_reversed"] is True
    assert await history.db[EVENTS].find_one({"_id": event["_id"]}) == original
    assert await snapshot(history.db) == before
