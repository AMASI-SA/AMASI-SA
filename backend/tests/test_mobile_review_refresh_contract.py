"""Synthetic provider reads + real Mongo owner transactions for explicit refresh.

No external Salla calls or canonical persistence/transaction mocks. Forbidden
observers are spies. The fixture requires a disposable loopback replica set.
"""
import asyncio
from copy import deepcopy
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

import accounting_shipping_native_observer as observer
import fulfillment_v2_routes as fulfillment
import order_engine.salla_refresh as refresh
import order_review_routes as review
from mobile_app_request_context import required_mobile_permissions
from operational_atomic import operational_owner
from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order
from salla_integration.service import SallaError
from accounting_ledger_v2 import GENERAL_LEDGER_COLLECTION, GROUPS_COLLECTION, AUDIT_COLLECTION
from accounting_shipping_native_contract import INBOX


OWNER = "synthetic-refresh-owner"
NUMBER = "700001"
SELECTOR = {"user_id": OWNER, "order_number": NUMBER}
PATH = f"/order-reviews-v1/{NUMBER}/refresh-from-salla"


@pytest_asyncio.fixture
async def fixture(monkeypatch):
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    assert urlparse(uri).hostname in {"127.0.0.1", "localhost"}, "Explicit loopback test replica required"
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = mongo["mobile_review_refresh_" + uuid4().hex]
    assert (await db.command("hello")).get("setName"), "Real transactions required"
    raw = {
        "id": 901, "reference_id": NUMBER, "date": "2026-10-15T10:00:00+00:00",
        "updated_at": "2026-10-15T10:00:00+00:00",
        "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
        "customer": {"full_name": "Synthetic customer"}, "payment_method": "credit_card",
        "amounts": {"total": {"amount": 10, "currency": "SAR"}},
        "items": [{"id": 501, "product_id": 1001, "name": "Synthetic product", "quantity": 1,
                   "amounts": {"total": {"amount": 10, "currency": "SAR"}}}],
    }
    await db.unified_orders.insert_one({
        **SELECTOR, "order_id": "901", "order_status": "under_review", "order_date": "2026-10-15",
        "raw_by_source": {"salla_direct": deepcopy(raw)},
    })
    workflow = {
        **SELECTOR, "stage": "pending_review", "revision": 7,
        "items": [{"order_item_id": "501", "preparation_note": "Keep this note",
                   "internal_note": "Keep internal note", "selected_image_url": "https://example.invalid/selected.png",
                   "selected_image_source": "mezan", "review_status": "pending_review", "revision": 2}],
        "operational_items": [{"id": "synthetic-operational", "name": "Synthetic service", "quantity": 1}],
        "customer_service_instructions": [],
    }
    await db[review.WORKFLOWS].insert_one(deepcopy(workflow))
    await db.mz2_atomic_owners.insert_one({"_id": OWNER, "revision": 0, "writes_paused": True})
    # These rows must remain unchanged even when Salla now reports delivery.
    await db.payment_transactions.insert_one({"id": "synthetic-payment", **SELECTOR, "provider": "tamara", "amount": 10})
    await db[GENERAL_LEDGER_COLLECTION].insert_one({"id": "synthetic-ledger", **SELECTOR, "amount": 10})
    actor = {"id": "synthetic-employee", "created_by": OWNER, "role": "employee",
             "mobile_app_permissions": ["app.page.pending_review"]}
    data = SimpleNamespace(db=db, actor=actor, raw=raw, workflow=workflow, calls=[], hold=None, arrivals=0,
                           entered=asyncio.Event(), release=asyncio.Event(), error=None)
    data.details = {**deepcopy(raw), "updated_at": "2026-10-15T11:00:00+00:00",
                    "customer": {"full_name": "Synthetic customer", "city": "Synthetic city", "country": "SA",
                                 "location": "Synthetic refreshed address"}}
    data.items = [{**deepcopy(raw["items"][0]), "options": [
        {"name": "نص الإهداء", "value": {"name": "خيار اصطناعي مستعاد"}},
    ]}]

    async def provider(_db, owner, method, path, params=None, json=None):
        assert owner == OWNER and method == "GET" and json is None
        data.calls.append((method, path, params))
        if path == "/orders/901":
            data.arrivals += 1
            data.entered.set()
            if data.hold:
                await asyncio.wait_for(data.release.wait(), timeout=10)
            if data.error:
                raise data.error
            return {"data": deepcopy(data.details)}
        assert path == "/orders/items" and params == {"order_id": "901"}
        return {"data": deepcopy(data.items)}

    async def bank(_db, owner, value):
        assert owner == OWNER
        return value

    async def current_user():
        return data.actor

    data.observer = AsyncMock(return_value={"state": "not_configured"})
    data.auto_route = AsyncMock(return_value={"promoted": False})
    monkeypatch.setattr(refresh, "call_salla", provider)
    monkeypatch.setattr(refresh, "_enrich_order_receiving_bank", bank)
    monkeypatch.setattr(observer, "observe_delivery", data.observer)
    monkeypatch.setattr(fulfillment, "auto_route_instant_order", data.auto_route)
    monkeypatch.setenv("JWT_SECRET", "synthetic-refresh-contract-only")
    app = FastAPI()
    app.include_router(review.make_order_review_router(db, current_user))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            data.client = client
            yield data
    finally:
        assert db.name.startswith("mobile_review_refresh_")
        await mongo.drop_database(db.name)
        mongo.close()


async def send(fixture, revision=7):
    return await fixture.client.post(PATH, json={"expected_revision": revision})


@pytest.mark.asyncio
async def test_recovers_options_and_address_preserves_review_and_tenant(fixture):
    f = fixture
    await f.db.unified_orders.insert_one({**SELECTOR, "user_id": "foreign", "raw_by_source": {"salla_direct": f.raw}})
    foreign = await f.db.unified_orders.find_one({"user_id": "foreign"})
    before = await f.db[review.WORKFLOWS].find_one(SELECTOR)
    source_id = (await f.db.unified_orders.find_one(SELECTOR))["_id"]
    payments = await f.db.payment_transactions.find({}).to_list(None)
    ledger = await f.db[GENERAL_LEDGER_COLLECTION].find({}).to_list(None)
    response = await send(f)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] and body["found"] and body["updated"] and body["approval_requires_reload"]
    assert body["revision"] == 8 and body["order_number"] == NUMBER and body["source"] == "salla"
    assert response.headers["cache-control"] == "no-store"
    source = await f.db.unified_orders.find_one(SELECTOR)
    assert source["_id"] == source_id
    assert source["raw_by_source"]["salla_direct"]["items"][0]["options"] == f.items[0]["options"]
    assert source["shipping_address"] == "Synthetic refreshed address"
    assert source["g47_salla_snapshot"]["revision"] == 1
    after = await f.db[review.WORKFLOWS].find_one(SELECTOR)
    assert {k: v for k, v in after.items() if k not in {"revision", "updated_at"}} == {
        k: v for k, v in before.items() if k not in {"revision", "updated_at"}}
    assert await f.db.unified_orders.find_one({"user_id": "foreign"}) == foreign
    assert await f.db.payment_transactions.find({}).to_list(None) == payments
    assert await f.db[GENERAL_LEDGER_COLLECTION].find({}).to_list(None) == ledger
    for name in (GROUPS_COLLECTION, AUDIT_COLLECTION, INBOX):
        assert await f.db[name].count_documents({}) == 0
    assert await f.db.order_review_completion_operations.count_documents({}) == 0
    f.observer.assert_not_awaited()
    f.auto_route.assert_not_awaited()
    assert len(f.calls) == 2
    loaded = await get_order(MongoOrderRepository(f.db), user_id=OWNER, order_number=NUMBER)
    assert "خيار اصطناعي مستعاد" in str([item.model_dump() for item in loaded.items])


@pytest.mark.asyncio
async def test_fresh_cache_does_not_skip_explicit_provider_reads(fixture):
    from datetime import datetime, timezone
    await fixture.db.unified_orders.update_one(SELECTOR, {"$set": {refresh.REFRESH_TIMESTAMP_FIELD: datetime.now(timezone.utc)}})
    response = await send(fixture)
    assert response.status_code == 200, response.text
    assert len(fixture.calls) == 2


@pytest.mark.asyncio
async def test_native_pending_review_bridge_does_not_require_orders_page(fixture):
    assert "app.page.pending_review" in required_mobile_permissions("/api" + PATH)
    fixture.actor = {"id": OWNER, "role": "owner", "_session_client": "amasi_mobile",
                     "_mobile_actor_id": "synthetic-employee", "_mobile_owner_id": OWNER,
                     "_mobile_app_permissions": ["app.page.pending_review"]}
    assert (await send(fixture)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [
    {"id": "ungranted", "created_by": OWNER, "role": "employee"},
    {"id": "unrelated", "created_by": OWNER, "role": "employee", "mobile_app_permissions": ["app.page.products"]},
])
async def test_missing_reviewer_permission_fails_before_provider(fixture, actor):
    fixture.actor = actor
    assert (await send(fixture)).status_code == 403
    assert not fixture.calls


@pytest.mark.asyncio
async def test_foreign_order_fails_before_provider(fixture):
    fixture.actor = {"id": "foreign", "role": "owner"}
    assert (await send(fixture)).status_code == 404
    assert not fixture.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "reviewed", "waiting_customer", "shipped", "stale_raw", "custom_reviewed", "revision", "operation"])
async def test_ineligible_state_fails_before_provider(fixture, case):
    f = fixture
    if case == "missing":
        await f.db.unified_orders.delete_one(SELECTOR)
    elif case in {"reviewed", "waiting_customer"}:
        await f.db[review.WORKFLOWS].update_one(SELECTOR, {"$set": {"stage": case}})
    elif case == "shipped":
        await f.db.unified_orders.update_one(SELECTOR, {"$set": {"order_status": "shipped",
            "raw_by_source.salla_direct.status": {"slug": "shipped", "name": "تم الشحن"}}})
    elif case == "stale_raw":
        await f.db.unified_orders.update_one(SELECTOR, {"$set": {"order_status": "shipped"}})
    elif case == "custom_reviewed":
        await f.db.unified_orders.update_one(SELECTOR, {"$set": {
            "raw_by_source.salla_direct.status.customized": {"name": "تم المراجعة"}}})
    elif case == "operation":
        await f.db.order_review_completion_operations.insert_one({**SELECTOR, "state": "prepared"})
    response = await send(f, revision=6 if case == "revision" else 7)
    assert response.status_code == (404 if case == "missing" else 409), response.text
    assert not f.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"expected_revision": -1}, {"expected_revision": True},
                                    {"expected_revision": 7, "force": True}, {"expected_revision": "7"}])
async def test_request_is_exact_revision_contract(fixture, payload):
    assert (await fixture.client.post(PATH, json=payload)).status_code == 422
    assert not fixture.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("reauth", [False, True])
async def test_provider_failure_is_json_and_preserves_source_and_review(fixture, reauth):
    fixture.error = SallaError("<html>upstream private detail</html>", needs_reauth=reauth, status_code=401 if reauth else 502)
    before = await fixture.db.unified_orders.find_one(SELECTOR)
    workflow = await fixture.db[review.WORKFLOWS].find_one(SELECTOR)
    result = await send(fixture)
    assert result.status_code == (409 if reauth else 502)
    assert "<html>" not in result.text and "private detail" not in result.text
    assert result.json()["detail"]["message"]
    assert await fixture.db.unified_orders.find_one(SELECTOR) == before
    assert await fixture.db[review.WORKFLOWS].find_one(SELECTOR) == workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["fresh_local_snapshot", "stale_salla_snapshot"])
async def test_skipped_result_never_claims_remote_success(fixture, monkeypatch, reason):
    mock = AsyncMock(return_value={"ok": True, "found": True, "updated": False, "skipped": True, "reason": reason})
    monkeypatch.setattr(review, "refresh_order_from_salla", mock)
    assert (await send(fixture)).status_code == 409
    assert (await fixture.db[review.WORKFLOWS].find_one(SELECTOR))["revision"] == 7


@pytest.mark.asyncio
async def test_older_provider_snapshot_keeps_canonical_version_and_review(fixture):
    await fixture.db.unified_orders.update_one(SELECTOR, {"$set": {"g47_salla_snapshot": {
        "source_updated_at": "2026-10-15T12:00:00.000000+00:00", "revision": 9}}})
    before = await fixture.db.unified_orders.find_one(SELECTOR)
    assert (await send(fixture)).status_code == 409
    assert await fixture.db.unified_orders.find_one(SELECTOR) == before
    assert (await fixture.db[review.WORKFLOWS].find_one(SELECTOR))["revision"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["completion", "edit", "operation"])
async def test_completion_or_edit_during_provider_read_fences_persistence(fixture, change):
    f = fixture
    f.hold = True
    before = await f.db.unified_orders.find_one(SELECTOR)
    request = asyncio.create_task(send(f))
    await asyncio.wait_for(f.entered.wait(), 5)
    async def concurrent(scoped):
        if change == "operation":
            await scoped.order_review_completion_operations.insert_one({**SELECTOR, "state": "syncing"})
        else:
            await scoped[review.WORKFLOWS].update_one(SELECTOR, {"$inc": {"revision": 1}, "$set": {
                "stage": "reviewed" if change == "completion" else "pending_review"}})
    try:
        await operational_owner(f.db, OWNER, concurrent)
    finally:
        f.release.set()
    response = await request
    assert response.status_code == 409, response.text
    assert await f.db.unified_orders.find_one(SELECTOR) == before
    f.observer.assert_not_awaited()
    f.auto_route.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_refresh_clicks_commit_once_and_old_approval_fails(fixture):
    f = fixture
    f.hold = True
    requests = [asyncio.create_task(send(f)) for _ in range(2)]
    async def both_arrived():
        for _ in range(100):
            if f.arrivals == 2:
                return
            await asyncio.sleep(.01)
        raise AssertionError("Both provider requests did not arrive")
    try:
        await both_arrived()
    finally:
        f.release.set()
    results = await asyncio.gather(*requests)
    assert sorted(result.status_code for result in results) == [200, 409], [r.text for r in results]
    assert (await f.db[review.WORKFLOWS].find_one(SELECTOR))["revision"] == 8
    stale = await f.client.post(f"/order-reviews-v1/{NUMBER}/complete", json={
        "expected_revision": 7, "approval_token": "stale-consent"})
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "review_revision_conflict"
    assert await f.db.order_review_completion_operations.count_documents({}) == 0


@pytest.mark.asyncio
async def test_legacy_default_still_invokes_delivery_observer_and_auto_route(fixture):
    result = await refresh.refresh_order_from_salla(fixture.db, OWNER, NUMBER, force=True)
    assert result["ok"], result
    fixture.observer.assert_awaited_once()
    fixture.auto_route.assert_awaited_once()
    assert (await fixture.db[review.WORKFLOWS].find_one(SELECTOR))["revision"] == 7


@pytest.mark.asyncio
async def test_new_delivered_status_refresh_does_not_change_payment_or_invoke_observers(fixture, monkeypatch):
    import bnpl.billing_eligible as billing
    billing_observer = AsyncMock()
    monkeypatch.setattr(billing, "propagate_status_to_billing_eligible", billing_observer)
    fixture.details["status"] = {"slug": "delivered", "name": "تم التوصيل"}
    payments = await fixture.db.payment_transactions.find({}).to_list(None)
    ledger = await fixture.db[GENERAL_LEDGER_COLLECTION].find({}).to_list(None)
    response = await send(fixture)
    assert response.status_code == 200, response.text
    assert await fixture.db.payment_transactions.find({}).to_list(None) == payments
    assert await fixture.db[GENERAL_LEDGER_COLLECTION].find({}).to_list(None) == ledger
    for name in (GROUPS_COLLECTION, AUDIT_COLLECTION, INBOX):
        assert await fixture.db[name].count_documents({}) == 0
    billing_observer.assert_not_awaited()
    fixture.observer.assert_not_awaited()
    fixture.auto_route.assert_not_awaited()
    assert (await fixture.db[review.WORKFLOWS].find_one(SELECTOR))["stage"] == "pending_review"
    loaded = await get_order(MongoOrderRepository(fixture.db), user_id=OWNER, order_number=NUMBER)
    assert not review._is_pending_review_order(loaded)
    assert (await send(fixture, revision=8)).status_code == 409


@pytest.mark.asyncio
async def test_existing_upsert_billing_eligibility_default_remains_enabled(fixture, monkeypatch):
    import bnpl.billing_eligible as billing
    observed = AsyncMock()
    monkeypatch.setattr(billing, "propagate_status_to_billing_eligible", observed)
    fixture.details["status"] = {"slug": "delivered", "name": "تم التوصيل"}
    result = await refresh.refresh_order_from_salla(fixture.db, OWNER, NUMBER, force=True)
    assert result["ok"], result
    observed.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_without_existing_workflow_initializes_only_revision(fixture):
    await fixture.db[review.WORKFLOWS].delete_one(SELECTOR)
    response = await send(fixture, revision=0)
    assert response.status_code == 200, response.text
    row = await fixture.db[review.WORKFLOWS].find_one(SELECTOR)
    assert row["stage"] == "pending_review" and row["revision"] == 1 and row["items"] == []
