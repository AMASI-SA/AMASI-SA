"""Print-only contract: synthetic provider data, no network or production writes."""
from copy import deepcopy
import os
import uuid
from urllib.parse import urlparse
from motor.motor_asyncio import AsyncIOMotorClient

import pytest
from mongomock_motor import AsyncMongoMockClient

import order_engine.shipping_label_service as shipping
from salla_integration.service import SallaError
from salla_shipping import CURRENT_SHIPPING


ORDER = "290989235"
OWNER = "print-test-owner"
CURRENT = {"id": "200", "order_id": "salla-order", "courier_name": "SMSA",
           "created_at": "2026-10-08T00:00:00Z", "status": "created",
           "tracking_number": "CURRENT-AWB", "label_url": "https://labels.test/current.pdf"}


@pytest.fixture(params=["memory", "mongo"])
async def setup(monkeypatch, request):
    if request.param == "mongo":
        uri = os.environ.get("BUILD37_TEST_MONGO_URI")
        if not uri:
            pytest.skip("isolated loopback replica set not configured")
        assert urlparse(uri).hostname in {"localhost", "127.0.0.1"}
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        hello = await client.admin.command("hello")
        assert hello.get("setName") and hello.get("isWritablePrimary")
    else:
        client = AsyncMongoMockClient()
    db = client["current_print_test_" + uuid.uuid4().hex]
    await db.unified_orders.insert_one({"user_id": OWNER, "order_number": ORDER,
        "order_status": "completed", "order_status_slug": "completed",
        "raw_by_source": {"salla_direct": {"status": {"slug": "completed"}}},
        "shipping_company": "old carrier", "salla_shipment_id": "old",
        "tracking_number": "OLD-AWB", "shipping_label_url": "https://labels.test/old.pdf",
        CURRENT_SHIPPING: {"company_code": "old", "shipment_id": "old",
            "carrier_updated_at": "2099-01-01T00:00:00Z", "superseded_shipment_ids": ["200"]}})
    await db.order_review_workflows.insert_one({"user_id": OWNER, "order_number": ORDER,
        "stage": "completed", "assembly_status": "completed", "store_courier_assignee_id": "driver-current",
        "store_courier_assignee_name": "Current driver", "store_delivery_assignment_id": "assignment-current"})
    state = {"order": {"id": "salla-order", "reference_id": ORDER,
        "status": {"slug": "completed"},
        "shipping": {"company_name": "SMSA"}, "customer": {"full_name": "Test"}},
        "rows": [deepcopy(CURRENT)], "calls": [], "fail": None}

    async def provider(_db, owner, method, path, **kwargs):
        assert owner == OWNER
        assert method == "GET", "printing must never mutate Salla"
        state["calls"].append(path)
        if path in state.get("denied", {}):
            raise SallaError("synthetic endpoint denial", status_code=state["denied"][path])
        if state["fail"] == path:
            raise SallaError("synthetic failure", status_code=502)
        if path == "/orders":
            return {"data": [{"id": "salla-order", "reference_id": ORDER}]}
        if path == "/orders/salla-order":
            return {"data": deepcopy({"shipments": state["rows"], **state["order"]})}
        if "/shipments" in path:
            pytest.fail(f"printing must use order details only, not {path}")
        if path == "/store/info":
            return {"data": {"name": "Test store"}}
        return {"data": {}}

    def forbidden(*args, **kwargs):
        pytest.fail("print called a stale guard, persistence, sync, or issue path")
    monkeypatch.setattr(shipping, "call_salla", provider)
    for name in ("_stale_label", "_persist_verified_snapshot", "_best_effort_resync",
                 "issue_shipping_label", "_ensure_order_completed"):
        monkeypatch.setattr(shipping, name, forbidden)
    if request.param == "memory":
        # Test-only transaction adapter: mongomock has no sessions. The Mongo
        # parametrization retains the production owner transaction and guard.
        async def local_owner(scoped, owner, callback, **kwargs):
            assert owner == OWNER
            return await callback(scoped)
        monkeypatch.setattr(shipping, "operational_owner", local_owner)
        import assembly_completion_delivery
        monkeypatch.setattr(assembly_completion_delivery, "operational_owner", local_owner)
    try:
        yield db, state
    finally:
        await client.drop_database(db.name)
        client.close()


async def dump(db):
    return {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()
            if name != "mz2_atomic_owners"}


async def assert_only_rejection_cache_revoked(db, before):
    """A failed fresh check may revoke success; all order/stock data is fixed."""
    after = await dump(db)
    expected = deepcopy(before)
    for workflow in expected.get("order_review_workflows", []):
        if workflow.get("user_id") == OWNER and workflow.get("order_number") == ORDER:
            workflow.update(carrier_label_ready=False, salla_order_status="unknown",
                            salla_order_status_verified_at=None, carrier_label_url=None,
                            carrier_label_print_data=None,
                            shipping_status_epoch=workflow.get("shipping_status_epoch", 0) + 1)
    assert after == expected


@pytest.mark.asyncio
async def test_print_uses_only_fresh_order_details_without_shipping_permissions(setup):
    db, state = setup
    state["order"]["shipments"] = [deepcopy(CURRENT)]
    state["denied"] = {"/shipments": 403, "/orders/salla-order/shipments": 403,
                       "/shipments/200": 403, "/shipments/200/tracking": 403}
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["label_url"] == CURRENT["label_url"]
    assert state["calls"] == ["/orders", "/orders/salla-order"]


@pytest.mark.asyncio
async def test_smsa_uses_current_provider_label_despite_every_local_identity_difference(setup):
    db, state = setup
    before = await dump(db)
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["label_url"] == CURRENT["label_url"]
    assert result["shipment_id"] == "200" and result["tracking_number"] == "CURRENT-AWB"
    assert await dump(db) == before


@pytest.mark.asyncio
async def test_store_courier_uses_legacy_formatter_without_shipment_or_clock_guards(setup):
    db, state = setup
    state["order"]["shipping"] = {"company_name": "مندوب المتجر", "company_code": "0",
        "address": {"address_line": "Current address"}}
    state["order"]["shipments"] = [{**CURRENT, "ship_to": {"address_line": "OLD address"}}]
    before = await dump(db)
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["label_type"] == "store_courier"
    assert result["print_data"]["address"]["address_line"] == "Current address"
    assert not any(path.startswith("/shipments") for path in state["calls"])
    assert await dump(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "pending", "cancelled", "failure"])
async def test_no_old_label_fallback_or_writes(setup, kind):
    db, state = setup
    state["rows"] = [{**CURRENT, "label_url": None}]
    if kind == "missing":
        state["rows"] = []
    elif kind in {"pending", "cancelled"}:
        state["rows"][0]["status"] = kind
    elif kind == "failure":
        state["fail"] = "/orders/salla-order"
    before = await dump(db)
    if kind == "failure":
        with pytest.raises(shipping.ShippingLabelError) as caught:
            await shipping.refresh_shipping_label(db, OWNER, ORDER)
        assert caught.value.code == "salla_shipping_unavailable"
    else:
        result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
        assert not result["ready"]
        assert result["message"]
    if kind == "failure":
        await assert_only_rejection_cache_revoked(db, before)
    else:
        assert await dump(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["another-order"])
async def test_requested_order_identity_required(setup, reference):
    db, state = setup
    state["order"]["reference_id"] = reference
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert "/shipments" not in state["calls"]


@pytest.mark.asyncio
async def test_current_pending_never_falls_back_to_another_ready_shipment(setup):
    db, state = setup
    state["rows"] = [
        {**CURRENT, "id": "900", "status": "pending", "label_url": None},
        {**CURRENT, "id": "800", "status": "cancelled"},
        {**CURRENT, "id": "700", "type": "return"},
        {**CURRENT, "id": "100", "tracking_number": "READY-AWB", "label_url": "https://labels.test/ready.pdf"},
    ]
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"]
    assert not result["label_url"]


@pytest.mark.asyncio
async def test_legacy_store_courier_embedded_in_salla_shipment(setup):
    db, state = setup
    state["order"]["shipping"] = {}
    state["rows"] = [{"id": "200", "courier_name": "مندوب المتجر", "meta": {"app_id": 0},
                      "ship_to": {"address_line": "Courier address"}}]
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["label_type"] == "store_courier" and result["ready"]
    assert result["print_data"]["address"]["address_line"] == "Courier address"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["orders", "completed"])
@pytest.mark.parametrize("allowed", [True, False])
async def test_http_print_is_read_only_and_permission_scoped(setup, monkeypatch, surface, allowed):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    # Exercise the actual Order Engine HTTP factory, without unrelated product
    # AI routers appended by the package-wide application installer.
    from order_engine import _original_make_order_engine_router as make_order_engine_router
    import fulfillment_v2_routes as fulfillment

    db, state = setup
    await db.order_review_workflows.update_one({"user_id": OWNER}, {"$set": {"assembly_status": "completed"}})
    for collection in ("general_ledger", "mezan_fulfillment_events_v2", "review_completions"):
        await db[collection].insert_one({"sentinel": "must remain unchanged"})

    async def user():
        return {"id": OWNER if allowed else "employee", "role": "owner" if allowed else "employee", "created_by": OWNER}

    def forbidden(*args, **kwargs):
        pytest.fail("print must not enter workflow synchronization")
    monkeypatch.setattr(fulfillment, "sync_completed_carrier_label", forbidden)
    app = FastAPI()
    if surface == "orders":
        app.include_router(make_order_engine_router(db, user))
        path = f"/orders-v2/{ORDER}/shipping-label/refresh"
    else:
        app.include_router(fulfillment.make_fulfillment_v2_router(db, user))
        path = f"/fulfillment-v2/completed/{ORDER}/carrier-label/refresh"
    before = await dump(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path)
    assert response.status_code == (200 if allowed else 403)
    if allowed:
        assert response.json()["label_url"] == CURRENT["label_url"]
    else:
        assert state["calls"] == []
    assert await dump(db) == before


@pytest.mark.asyncio
async def test_print_rejects_shipment_for_another_order(setup):
    db, state = setup
    state["rows"][0]["order_id"] = "other-order"
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, ORDER)


@pytest.mark.asyncio
async def test_empty_fresh_order_shipments_never_uses_local_label(setup):
    db, state = setup
    state["order"]["shipments"] = []
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"]
    assert state["calls"] == ["/orders", "/orders/salla-order"]


@pytest.mark.asyncio
async def test_print_does_not_call_forbidden_shipment_details(setup):
    db, state = setup
    state["fail"] = "/shipments/200"
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"]
    assert state["calls"] == ["/orders", "/orders/salla-order"]


@pytest.mark.asyncio
@pytest.mark.parametrize("company", ["SMSA", "iMile"])
async def test_external_current_label_ignores_cancelled_and_return_rows(setup, company):
    db, state = setup
    state["order"]["shipping"] = {"company_name": company}
    state["rows"][0]["courier_name"] = company
    state["rows"] += [{**CURRENT, "id": "999", "status": "cancelled"}, {**CURRENT, "id": "998", "type": "return"}]
    before = await dump(db)
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["shipment_id"] == "200"
    assert result["label_url"] == CURRENT["label_url"]
    assert await dump(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [None, ""])
async def test_print_missing_order_identity_fails_closed(setup, reference):
    db, state = setup
    state["order"]["reference_id"] = reference
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, ORDER)


@pytest.mark.asyncio
async def test_multiple_ready_shipments_are_not_guessed_by_numeric_id(setup):
    db, state = setup
    state["rows"].append({**CURRENT, "id": "999", "label_url": "https://labels.test/old.pdf"})
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"] and not result["label_url"]


@pytest.mark.asyncio
async def test_missing_embedded_pdf_never_tries_tracking(setup):
    db, state = setup
    state["rows"][0].update(courier_name="iMile", label_url=None)
    state["tracking"] = {"shipment": {**CURRENT, "courier_name": "iMile"}}
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"] and not result["label_url"]
    assert state["calls"] == ["/orders", "/orders/salla-order"]




@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [{**CURRENT, "label_url": None}, {**CURRENT, "status": "cancelled"}])
async def test_fresh_order_cleared_label_is_not_replaced_by_other_endpoints(setup, detail):
    db, state = setup
    state["order"]["shipments"] = [detail]
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"] and not result["label_url"]
    assert state["calls"] == ["/orders", "/orders/salla-order"]


@pytest.mark.asyncio
async def test_order_embedded_shipment_cannot_belong_to_another_order(setup):
    db, state = setup
    state["order"]["shipments"] = [{**CURRENT, "order_id": "another-order"}]
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert state["calls"] == ["/orders", "/orders/salla-order"]


async def completed_print_request(db, monkeypatch, *, order_number=ORDER):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    import fulfillment_v2_routes as fulfillment

    async def owner():
        return {"id": OWNER, "role": "owner"}

    def forbidden(*args, **kwargs):
        pytest.fail("read/print entered the workflow synchronization path")

    monkeypatch.setattr(fulfillment, "sync_completed_carrier_label", forbidden)
    app = FastAPI()
    app.include_router(fulfillment.make_fulfillment_v2_router(db, owner))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/fulfillment-v2/completed/{order_number}/carrier-label/refresh")


@pytest.mark.asyncio
@pytest.mark.parametrize("piece_kind", ["physical", "virtual"])
@pytest.mark.parametrize("stage", ["completed", "delivering", "delivered"])
@pytest.mark.parametrize("salla_status", ["completed", "in_progress", "shipped", "delivered"])
async def test_completed_assembly_print_requires_live_completed_status(
    setup, monkeypatch, piece_kind, stage, salla_status,
):
    db, state = setup
    state["order"]["status"] = {"slug": salla_status}
    workflow_patch = {
        "stage": stage, "assembly_status": "completed",
        "assembly_completed_at": "2026-10-01T00:00:00Z",
        "carrier_label_print_confirmed": True,
    }
    piece = {"user_id": OWNER, "order_number": ORDER,
             "assembly_status": "ready", "piece_id": "completed-piece"}
    if piece_kind == "physical":
        await db.mezan_preparation_pieces_v1.insert_one(piece)
    else:
        workflow_patch["operational_items"] = [{
            "operational_item_id": "completed-piece", "assembly_status": "ready",
            "preparation_status": "ready",
        }]
    await db.order_review_workflows.update_one({"user_id": OWNER}, {"$set": workflow_patch})
    for collection in ("general_ledger", "mezan_fulfillment_events_v2", "review_completions", "inventory"):
        await db[collection].insert_one({"sentinel": "unchanged by reprint"})
    before = await dump(db)
    response = await completed_print_request(db, monkeypatch)
    if salla_status != "completed":
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "shipping_snapshot_changed"
        assert response.json()["detail"]["reason_code"] == "order_status_not_completed"
        assert "/shipments" not in state["calls"]
        await assert_only_rejection_cache_revoked(db, before)
        return
    assert response.status_code == 200, response.text
    assert response.json()["ready"]
    assert response.json()["label_url"] == CURRENT["label_url"]
    assert response.json()["shipment_id"] == "200"
    assert response.json()["tracking_number"] == "CURRENT-AWB"
    assert state["calls"] == ["/orders", "/orders/salla-order"]
    assert await dump(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["missing", "pending", "array", "timestamp_only", "print_confirmed_only", "other_order", "other_merchant"])
async def test_salla_delivered_cannot_replace_local_assembly_completion(setup, monkeypatch, proof):
    db, state = setup
    state["order"]["status"] = {"slug": "delivered"}
    # Explicitly remove the valid completion proof seeded by the common fixture.
    await db.order_review_workflows.update_one({"user_id": OWNER}, {"$unset": {"assembly_status": ""}})
    patch = {"stage": "delivered"}
    if proof == "pending":
        patch["assembly_status"] = "pending"
    elif proof == "array":
        patch["assembly_status"] = ["completed"]
    elif proof == "timestamp_only":
        patch["assembly_completed_at"] = "2026-10-01T00:00:00Z"
    elif proof == "print_confirmed_only":
        patch["carrier_label_print_confirmed"] = True
    elif proof in {"other_order", "other_merchant"}:
        patch["assembly_status"] = "completed"
        patch["order_number" if proof == "other_order" else "user_id"] = "someone-else"
    await db.order_review_workflows.update_one({"user_id": OWNER}, {"$set": patch})
    before = await dump(db)
    response = await completed_print_request(db, monkeypatch)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "shipping_snapshot_changed"
    assert response.json()["detail"]["reason_code"] == "assembly_completion_required"
    assert state["calls"] == []
    assert await dump(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["delivering", "delivered"])
async def test_issue_guard_still_requires_current_completed_stage(setup, stage):
    from fulfillment_carrier_label import _require_completed_workflow

    db, state = setup
    await db.order_review_workflows.update_one({"user_id": OWNER}, {"$set": {
        "stage": stage, "assembly_status": "completed",
    }})
    before = await dump(db)
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await _require_completed_workflow(db, user_id=OWNER, order_number=ORDER)
    assert caught.value.code == "assembly_completion_required"
    assert state["calls"] == []
    assert await dump(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("piece_kind", ["physical", "virtual"])
@pytest.mark.parametrize("salla_status", ["completed", "shipped", "delivered"])
async def test_completed_store_courier_reprint_uses_formatter_after_delivery(setup, monkeypatch, piece_kind, salla_status):
    db, state = setup
    # Prior printing/later local stages do not bypass the owner-approved live completed gate.
    state["order"]["status"] = {"slug": salla_status}
    state["order"]["shipping"] = {
        "company_name": "مندوب المتجر", "company_code": "0",
        "address": {"address_line": "Current courier address"},
    }
    workflow_patch = {
        "stage": "delivered", "assembly_status": "completed",
        "carrier_label_print_confirmed": True,
    }
    if piece_kind == "physical":
        await db.mezan_preparation_pieces_v1.insert_one({
            "user_id": OWNER, "order_number": ORDER, "piece_id": "courier-piece",
            "assembly_status": "ready",
        })
    else:
        workflow_patch["operational_items"] = [{
            "operational_item_id": "courier-piece", "assembly_status": "ready",
            "preparation_status": "ready",
        }]
    await db.order_review_workflows.update_one({"user_id": OWNER}, {"$set": workflow_patch})
    before = await dump(db)
    response = await completed_print_request(db, monkeypatch)
    if salla_status != "completed":
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "shipping_snapshot_changed"
        assert response.json()["detail"]["reason_code"] == "order_status_not_completed"
        assert not any(path.startswith("/shipments") for path in state["calls"])
        await assert_only_rejection_cache_revoked(db, before)
        return
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] and result["label_type"] == "store_courier"
    assert result["print_data"]["address"]["address_line"] == "Current courier address"
    assert not any(path.startswith("/shipments") for path in state["calls"])
    assert await dump(db) == before

# Order-details-only printing exercises the real print service; only provider I/O is
# substituted. These are synthetic fixtures, never production order snapshots.
@pytest.fixture
async def order_details_only(setup, monkeypatch):
    db, state = setup
    for name in ("inventory", "mezan_preparation_pieces_v1",
                 "mezan_fulfillment_events_v2", "shipments"):
        await db[name].insert_one({"sentinel": "print must not write"})
    before = await dump(db)

    class ReadOnlyCollection:
        def __init__(self, collection, name):
            self.collection = collection
            self.name = name

        def __getattr__(self, name):
            if name == "update_one" and self.name == "order_review_workflows":
                async def revoke(selector, update, **kwargs):
                    assert selector["user_id"] == OWNER and selector["order_number"] == ORDER
                    assert set(update) <= {"$set", "$inc"}
                    assert set(update.get("$set", {})) <= {
                        "carrier_label_ready", "salla_order_status", "salla_order_status_verified_at",
                        "carrier_label_url", "carrier_label_print_data", "assembly_delivery.order_confirmed",
                        "assembly_delivery.state", "assembly_delivery.claim", "assembly_delivery.lease_until",
                        "assembly_delivery.error_code",
                    }
                    assert update.get("$inc", {}) in ({}, {"shipping_status_epoch": 1})
                    return await self.collection.update_one(selector, update, **kwargs)
                return revoke
            if name not in {"find", "find_one", "count_documents", "distinct"}:
                pytest.fail(f"print attempted a collection operation: {name}")
            return getattr(self.collection, name)

    class ReadOnlyDB:
        def __getitem__(self, name):
            # Serialization metadata is the sole permitted owner-guard write.
            return db[name] if name == "mz2_atomic_owners" else ReadOnlyCollection(db[name], name)

        def __getattr__(self, name):
            if name in {"client", "command"}:
                return getattr(db, name)
            return self[name]

    def forbidden(*args, **kwargs):
        pytest.fail("print attempted issuance, resync, or Salla status mutation")

    for name in ("_internal_delivery_document", "_ensure_internal_order_completed",
                 "resync_single_order"):
        monkeypatch.setattr(shipping, name, forbidden)
    yield ReadOnlyDB(), state
    after = await dump(db)
    if after != before:
        await assert_only_rejection_cache_revoked(db, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("company", ["iMile", "SMSA"])
@pytest.mark.parametrize("shape", ["list", "object"])
async def test_order_details_is_the_only_shipping_source(order_details_only, company, shape):
    db, state = order_details_only
    row = {**CURRENT, "courier_name": company, "label_url": "https://labels.test/order-only.pdf"}
    state["order"]["shipments"] = [row] if shape == "list" else row
    state["order"]["shipping"] = {"company_name": company}
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["label_url"] == row["label_url"]
    assert state["calls"] == ["/orders", "/orders/salla-order"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "empty", "cancelled", "return", "multiple", "wrong_order", "missing_id", "incomplete", "malformed"])
async def test_invalid_order_embedded_source_has_no_fallback(order_details_only, kind):
    db, state = order_details_only
    row = deepcopy(CURRENT)
    embedded = [row]
    if kind == "missing": embedded = None
    elif kind == "empty": embedded = []
    elif kind == "cancelled": row["status"] = "cancelled"
    elif kind == "return": row["type"] = "return"
    elif kind == "multiple": embedded.append({**CURRENT, "id": "999"})
    elif kind == "wrong_order": row["order_id"] = "other-order"
    elif kind == "missing_id": row.pop("id")
    elif kind == "incomplete": row["label_url"] = None
    elif kind == "malformed": embedded = "invalid"
    state["order"]["shipments"] = embedded
    if kind in {"wrong_order", "malformed"}:
        with pytest.raises(shipping.ShippingLabelError):
            await shipping.refresh_shipping_label(db, OWNER, ORDER)
    else:
        result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
        assert not result["ready"]
    assert state["calls"] == ["/orders", "/orders/salla-order"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 502])
async def test_order_read_failure_never_probes_shipping_routes(order_details_only, status):
    db, state = order_details_only
    state["denied"] = {"/orders/salla-order": status}
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping.refresh_shipping_label(db, OWNER, ORDER)
    if status == 403:
        assert caught.value.code == "order_scope_required"
        assert "قراءة الطلبات" in str(caught.value)
    else:
        assert caught.value.code == "salla_shipping_unavailable"
    assert state["calls"] == ["/orders", "/orders/salla-order"]


@pytest.mark.asyncio
async def test_each_print_uses_new_order_details(order_details_only):
    db, state = order_details_only
    first = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    state["order"]["shipments"] = [{**CURRENT, "id": "new", "label_url": "https://labels.test/new.pdf"}]
    second = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert first["label_url"] == CURRENT["label_url"]
    assert second["label_url"] == "https://labels.test/new.pdf"
    assert state["calls"] == ["/orders", "/orders/salla-order"] * 2
