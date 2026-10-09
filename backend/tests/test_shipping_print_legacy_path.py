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
        "shipping_company": "old carrier", "salla_shipment_id": "old",
        "tracking_number": "OLD-AWB", "shipping_label_url": "https://labels.test/old.pdf",
        CURRENT_SHIPPING: {"company_code": "old", "shipment_id": "old",
            "carrier_updated_at": "2099-01-01T00:00:00Z", "superseded_shipment_ids": ["200"]}})
    await db.order_review_workflows.insert_one({"user_id": OWNER, "order_number": ORDER,
        "stage": "completed", "store_courier_assignee_id": "driver-current",
        "store_courier_assignee_name": "Current driver", "store_delivery_assignment_id": "assignment-current"})
    state = {"order": {"id": "salla-order", "reference_id": ORDER,
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
            return {"data": deepcopy(state["order"])}
        if path == "/shipments":
            assert kwargs["params"]["order_id"] == "salla-order"
            return {"data": deepcopy(state["rows"])}
        if path == "/orders/salla-order/shipments":
            return deepcopy(state.get("order_shipments", {"data": []}))
        if path.endswith("/tracking"):
            return {"data": deepcopy(state.get("tracking", {}))}
        if path.startswith("/shipments/"):
            return {"data": deepcopy(state.get("detail", next((row for row in state["rows"] if str(row.get("id")) == path.split("/")[-1]), {})))}
        if path == "/store/info":
            return {"data": {"name": "Test store"}}
        return {"data": {}}

    def forbidden(*args, **kwargs):
        pytest.fail("print called a stale guard, persistence, sync, or issue path")
    monkeypatch.setattr(shipping, "call_salla", provider)
    for name in ("_stale_label", "_persist_verified_snapshot", "_best_effort_resync",
                 "issue_shipping_label", "_ensure_order_completed", "operational_owner"):
        monkeypatch.setattr(shipping, name, forbidden)
    try:
        yield db, state
    finally:
        await client.drop_database(db.name)
        client.close()


async def dump(db):
    return {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}


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
        state["fail"] = "/shipments"
    before = await dump(db)
    if kind == "failure":
        with pytest.raises(shipping.ShippingLabelError) as caught:
            await shipping.refresh_shipping_label(db, OWNER, ORDER)
        assert caught.value.code == "salla_shipping_unavailable"
    else:
        result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
        assert not result["ready"]
        assert result["message"]
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
async def test_print_does_not_keep_embedded_label_when_fresh_list_is_empty(setup):
    db, state = setup
    state["order"]["shipments"] = [deepcopy(CURRENT)]
    state["rows"] = []
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"]
    assert "/shipments" in state["calls"]


@pytest.mark.asyncio
async def test_print_detail_failure_never_uses_list_label(setup):
    db, state = setup
    state["fail"] = "/shipments/200"
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, ORDER)


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
async def test_imile_ready_pdf_from_same_current_tracking_endpoint(setup):
    db, state = setup
    state["rows"][0].update(courier_name="iMile", label_url=None)
    state["tracking"] = {"shipment": {**CURRENT, "courier_name": "iMile"}}
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["label_url"] == CURRENT["label_url"]
    assert "/shipments/200/tracking" in state["calls"]


@pytest.mark.asyncio
async def test_tracking_response_for_other_order_is_not_opened(setup):
    db, state = setup
    state["rows"][0]["label_url"] = None
    state["tracking"] = {"shipment": {**CURRENT, "order_id": "other-order"}}
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, ORDER)


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [
    {**CURRENT, "label_url": None},
    {**CURRENT, "status": "cancelled"},
])
async def test_detail_removes_ready_list_label_without_fallback(setup, detail):
    db, state = setup
    state["detail"] = detail
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"]
    assert not result["label_url"]


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [
    {**CURRENT, "id": "other-shipment"},
    {**CURRENT, "order_id": "other-order"},
])
async def test_detail_identity_mismatch_cannot_open_another_order(setup, detail):
    db, state = setup
    state["detail"] = detail
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, ORDER)


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
@pytest.mark.parametrize("salla_status", ["in_progress", "shipped", "delivered"])
async def test_completed_assembly_print_survives_later_stages_and_reprint(
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
    assert response.status_code == 200, response.text
    assert response.json()["ready"]
    assert response.json()["label_url"] == CURRENT["label_url"]
    assert response.json()["shipment_id"] == "200"
    assert response.json()["tracking_number"] == "CURRENT-AWB"
    assert "/shipments" in state["calls"]
    assert await dump(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["missing", "pending", "array", "timestamp_only", "print_confirmed_only", "other_order", "other_merchant"])
async def test_salla_delivered_cannot_replace_local_assembly_completion(setup, monkeypatch, proof):
    db, state = setup
    state["order"]["status"] = {"slug": "delivered"}
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
    assert response.json()["detail"]["code"] == "assembly_completion_required"
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
async def test_completed_store_courier_reprint_uses_formatter_after_delivery(setup, monkeypatch, piece_kind):
    db, state = setup
    state["order"]["status"] = {"slug": "delivered"}
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
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] and result["label_type"] == "store_courier"
    assert result["print_data"]["address"]["address_line"] == "Current courier address"
    assert not any(path.startswith("/shipments") for path in state["calls"])
    assert await dump(db) == before

# Scope compatibility exercises the real print service; only provider I/O is
# substituted. These are synthetic fixtures, never production order snapshots.
@pytest.fixture
async def scope_fallback(setup, monkeypatch):
    db, state = setup
    for name in ("inventory", "mezan_preparation_pieces_v1",
                 "mezan_fulfillment_events_v2", "shipments"):
        await db[name].insert_one({"sentinel": "print must not write"})
    before = await dump(db)

    class ReadOnlyCollection:
        def __init__(self, collection):
            self.collection = collection

        def __getattr__(self, name):
            if name not in {"find", "find_one", "count_documents", "distinct"}:
                pytest.fail(f"print attempted a collection operation: {name}")
            return getattr(self.collection, name)

    class ReadOnlyDB:
        def __getitem__(self, name):
            return ReadOnlyCollection(db[name])

        def __getattr__(self, name):
            return self[name]

    def forbidden(*args, **kwargs):
        pytest.fail("print attempted issuance, resync, or Salla status mutation")

    for name in ("_internal_delivery_document", "_ensure_internal_order_completed",
                 "resync_single_order"):
        monkeypatch.setattr(shipping, name, forbidden)
    state["order_shipments"] = {"data": [deepcopy(CURRENT)]}
    yield ReadOnlyDB(), state
    assert await dump(db) == before


def deny_print_read(state, phase, status=403):
    path = {"list": "/shipments", "details": "/shipments/200",
            "tracking": "/shipments/200/tracking"}[phase]
    state["denied"] = {path: status}
    if phase == "tracking":
        # A successful fresh details read invalidates the earlier list PDF.
        # Only a NEW compatible provider read may supply it again.
        state["detail"] = {**CURRENT, "label_url": None}
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("company", ["iMile", "SMSA"])
@pytest.mark.parametrize("phase", ["list", "details", "tracking"])
@pytest.mark.parametrize("status", [401, 403])
async def test_scope_compat_external_current_label(scope_fallback, company, phase, status):
    db, state = scope_fallback
    state["order"]["shipping"] = {"company_name": company}
    state["rows"][0]["courier_name"] = company
    # Distinct URL proves the denied endpoint did not revive the prior list URL.
    fresh = {**CURRENT, "courier_name": company,
             "label_url": "https://labels.test/fresh-compatible.pdf"}
    state["order_shipments"] = {"data": {"shipments": [fresh]}}
    denied = deny_print_read(state, phase, status)
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["shipment_id"] == "200"
    assert result["label_url"] == fresh["label_url"]
    assert result["tracking_number"] == "CURRENT-AWB"
    assert denied in state["calls"]
    assert "/orders/salla-order/shipments" in state["calls"]


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [
    {"courier_name": "Local delivery", "meta": {"app_id": 0}},
    {"courier_name": "Local delivery", "meta": {"app_id": "0"}},
    {"courier_name": "مندوب الرياض"},
])
@pytest.mark.parametrize("status", [None, 401, 403])
async def test_scope_compat_courier_detected_before_external_reads(scope_fallback, identity, status):
    db, state = scope_fallback
    state["order"].pop("shipping")
    row = {"id": "200", "order_id": "salla-order", "type": "shipment",
           "status": "creating", "label": None, "tracking_number": None,
           "ship_to": {"address_line": "Current courier address"}, **identity}
    state["rows"] = [row]
    state["order_shipments"] = {"data": [row]}
    state["denied"] = {"/shipments/200": 403, "/shipments/200/tracking": 403}
    if status:
        state["denied"]["/shipments"] = status
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["label_type"] == "store_courier"
    assert result["shipment_id"] == "200"
    assert result["print_data"]["address"]["address_line"] == "Current courier address"
    assert result["print_data"]["qr_code"]
    assert "/shipments/200" not in state["calls"]
    assert "/shipments/200/tracking" not in state["calls"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["list", "details", "tracking"])
@pytest.mark.parametrize("kind", [
    "empty", "cancelled", "return", "multiple", "paginated", "page_count",
    "wrong_order", "missing_id", "incomplete", "malformed",
])
async def test_scope_compat_rejects_invalid_current_source(scope_fallback, phase, kind):
    db, state = scope_fallback
    deny_print_read(state, phase)
    row = deepcopy(CURRENT)
    response = {"data": [row]}
    if kind == "empty":
        response["data"] = []
    elif kind == "cancelled":
        row["status"] = "cancelled"
    elif kind == "return":
        row["type"] = "return"
    elif kind == "multiple":
        response["data"].append({**CURRENT, "id": "999"})
    elif kind == "paginated":
        response["pagination"] = {"links": {"next": "https://salla.test/next"}}
    elif kind == "page_count":
        response["pagination"] = {"currentPage": 1, "totalPages": 2}
    elif kind == "wrong_order":
        row["order_id"] = "another-order"
    elif kind == "missing_id":
        row.pop("id")
    elif kind == "incomplete":
        row["label_url"] = None
    elif kind == "malformed":
        response["data"] = "invalid"
    state["order_shipments"] = response
    # Neither the embedded nor the database's stale PDF may rescue the read.
    state["order"]["shipments"] = [deepcopy(CURRENT)]
    if kind in {"wrong_order", "malformed"}:
        with pytest.raises(shipping.ShippingLabelError) as caught:
            await shipping.refresh_shipping_label(db, OWNER, ORDER)
        assert caught.value.code != "shipping_scope_required"
    else:
        result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
        assert not result["ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["details", "tracking"])
async def test_scope_compat_never_switches_current_shipment(scope_fallback, phase):
    db, state = scope_fallback
    deny_print_read(state, phase)
    state["order_shipments"]["data"][0]["id"] = "199"
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert caught.value.code == "salla_order_reference_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["list", "details", "tracking"])
async def test_scope_compat_non_auth_failures_do_not_fallback(scope_fallback, phase):
    db, state = scope_fallback
    deny_print_read(state, phase, 502)
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert caught.value.code == "salla_shipping_unavailable"
    assert "/orders/salla-order/shipments" not in state["calls"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_scope_compat_denied_fallback_does_not_use_old_label(scope_fallback, status):
    db, state = scope_fallback
    deny_print_read(state, "list", status)
    state["denied"]["/orders/salla-order/shipments"] = status
    state["order"]["shipments"] = [deepcopy(CURRENT)]
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, ORDER)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "return"])
async def test_scope_compat_terminal_details_do_not_revive_fallback(scope_fallback, status):
    db, state = scope_fallback
    state["detail"] = {**CURRENT, "label_url": None,
                       **({"status": status} if status == "cancelled" else {"type": status})}
    state["denied"] = {"/shipments/200/tracking": 403}
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"]
    assert "/orders/salla-order/shipments" not in state["calls"]


@pytest.mark.asyncio
@pytest.mark.parametrize("clock_source", ["details", "list"])
async def test_scope_compat_cannot_revive_older_same_shipment(scope_fallback, clock_source):
    db, state = scope_fallback
    deny_print_read(state, "tracking")
    observed = state["detail"] if clock_source == "details" else state["rows"][0]
    observed["updated_at"] = "2026-10-08T02:00:00Z"
    state["order_shipments"]["data"][0]["updated_at"] = "2026-10-08T01:00:00Z"
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert not result["ready"]
