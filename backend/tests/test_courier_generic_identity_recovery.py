"""Recover a current courier document through the public print-only route."""
from copy import deepcopy
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest

import operational_atomic
from fulfillment_v2_routes import make_fulfillment_v2_router
from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING


OWNER = "synthetic-courier-owner"
NUMBER = "910000001"
INTERNAL_ID = "42"
KEY = {"user_id": OWNER, "order_number": NUMBER}
PATH = f"/fulfillment-v2/completed/{NUMBER}/carrier-label/v1/refresh"


@pytest.fixture
async def courier_route(monkeypatch):
    mongo = AsyncMongoMockClient()
    db = mongo.courier_generic_identity_test
    order = {
        "id": 42,
        "reference_id": NUMBER,
        "status": {"slug": "completed"},
        "delivery_method": "shipping",
        "receiver": {"name": "Synthetic recipient", "mobile": "0500000000"},
        "shipping": {"address": {"city": "الرياض", "street": "Synthetic destination"}},
    }
    child = {
        "id": 51, "order_id": 42, "type": "shipment", "status": "created",
        "courier_name": "مندوب الرياض", "courier_id": 77,
        "meta": {"app_id": 0}, "created_at": None,
    }
    await db.unified_orders.insert_one({
        **KEY, "order_id": INTERNAL_ID, "order_status": "completed",
        "order_status_slug": "completed", "raw_by_source": {"salla_direct": deepcopy(order)},
        "shipping_company": "shipping",
        CURRENT_SHIPPING: {"company_name": "shipping", "source_path": "delivery_method"},
    })
    await db.order_review_workflows.insert_one({
        **KEY, "stage": "completed", "assembly_status": "completed",
    })
    state = {"order": order, "rows": [child],
             "pagination": {"totalPages": 1, "currentPage": 1, "links": []}, "calls": []}

    async def provider(database, owner, method, path, **kwargs):
        assert database is db and owner == OWNER
        assert method == "GET", "A label refresh must not change Salla"
        state["calls"].append((method, path))
        if path == f"/orders/{INTERNAL_ID}":
            return {"data": deepcopy(state["order"])}
        if path == f"/orders/{INTERNAL_ID}/shipments":
            return {"data": deepcopy(state["rows"])}
        if path == "/shipments":
            assert kwargs["params"] == {"order_id": INTERNAL_ID, "per_page": 50}
            return {"data": deepcopy(state["rows"]), "pagination": deepcopy(state["pagination"])}
        for row in state["rows"]:
            if path in {f"/shipments/{row['id']}", f"/shipments/{row['id']}/tracking"}:
                return {"data": deepcopy(row)}
        if path == "/store/info":
            return {"data": {"name": "Synthetic store"}}
        raise AssertionError(f"Unexpected provider path: {path}")

    async def local_owner(database, owner, callback, **kwargs):
        # Mongo mock cannot run sessions. This does not prove transaction races.
        assert database is db and owner == OWNER
        return await callback(database)

    monkeypatch.setattr(compat, "call_salla", provider)
    monkeypatch.setattr(shipping, "call_salla", provider)
    monkeypatch.setattr(operational_atomic, "operational_owner", local_owner)
    monkeypatch.setattr(shipping, "operational_owner", local_owner)
    async def actor():
        return {"id": OWNER, "role": "owner"}
    app = FastAPI()
    app.include_router(make_fulfillment_v2_router(db, actor))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield db, client, state
    mongo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["named_order", "current_child"])
async def test_current_courier_evidence_recovers_qr_from_generic_saved_identity(courier_route, evidence):
    db, client, state = courier_route
    if evidence == "named_order":
        state["order"]["shipping_company_name"] = "مندوب الرياض"
        state["rows"] = []
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(PATH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] is True and result["print_state"] == "ready"
    assert result["order_status_completed"] is True
    assert result["label_type"] == "store_courier"
    assert result["courier_name"] == "مندوب الرياض"
    assert result["print_data"]["order_number"] == NUMBER
    assert result["print_data"]["qr_code"].startswith("data:image/svg+xml;base64,")
    assert result["label_url"] is None and result["tracking_number"] is None
    assert (("GET", "/shipments") in state["calls"]) is (evidence == "current_child")
    assert await db.unified_orders.find_one(KEY) == before
    workflow = await db.order_review_workflows.find_one(KEY)
    assert workflow["stage"] == workflow["assembly_status"] == "completed"
    assert workflow["carrier_label_type"] == "store_courier"
    assert "assembly_delivery" not in workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["none", "foreign", "cancelled", "return", "superseded"])
async def test_no_eligible_child_never_invents_a_courier_label(courier_route, case):
    db, client, state = courier_route
    if case == "none":
        state["rows"] = []
    elif case == "foreign":
        state["rows"][0]["order_id"] = 43
    elif case == "cancelled":
        state["rows"][0]["status"] = "cancelled"
    elif case == "return":
        state["rows"][0]["type"] = "return"
    else:
        await db.unified_orders.update_one(KEY, {"$set": {
            f"{CURRENT_SHIPPING}.superseded_shipment_ids": ["51"],
        }})
    response = await client.post(PATH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] is False and result["print_state"] == "pending"
    assert result["order_status_completed"] is True
    assert result.get("print_data") is None and result.get("label_url") is None
    workflow = await db.order_review_workflows.find_one(KEY)
    assert workflow.get("carrier_label_ready") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "multiple", "next_page", "multiple_pages", "external_identity",
    "cancelled_local", "stale_child", "known_current",
])
async def test_ambiguous_child_or_real_external_identity_blocks_recovery(courier_route, case):
    db, client, state = courier_route
    if case == "multiple":
        state["rows"].append({**state["rows"][0], "id": 52})
    elif case == "next_page":
        state["pagination"] = {"links": {"next": "synthetic-next-page"}}
    elif case == "multiple_pages":
        state["pagination"] = {"totalPages": 2}
    elif case == "external_identity":
        await db.unified_orders.update_one(KEY, {"$set": {
            "shipping_company": "iMile", "shipping_company_code": "imile",
            CURRENT_SHIPPING: {"company_name": "iMile", "company_code": "imile"},
        }})
    elif case == "cancelled_local":
        await db.unified_orders.update_one(KEY, {"$set": {f"{CURRENT_SHIPPING}.status": "cancelled"}})
    elif case == "stale_child":
        state["rows"][0]["updated_at"] = "2026-11-11T00:00:00Z"
        await db.unified_orders.update_one(KEY, {"$set": {
            f"{CURRENT_SHIPPING}.shipment_updated_at": "2026-11-12T00:00:00Z",
        }})
    else:
        await db.unified_orders.update_one(KEY, {"$set": {
            f"{CURRENT_SHIPPING}.shipment_id": "51", "salla_shipment_id": "51",
        }})
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(PATH)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "shipping_snapshot_changed"
    assert response.json()["detail"]["print_state"] == "failed"
    assert await db.unified_orders.find_one(KEY) == before
    assert not (await db.order_review_workflows.find_one(KEY)).get("carrier_label_ready")


@pytest.mark.asyncio
async def test_external_label_keeps_its_current_snapshot_guard(courier_route, monkeypatch):
    db, client, state = courier_route
    state["rows"] = [{
        "id": 51, "order_id": 42, "type": "shipment", "status": "created",
        "courier_name": "SMSA", "courier_code": "smsa",
        "label_url": "https://labels.example/current.pdf",
    }]
    await db.unified_orders.update_one(KEY, {"$set": {
        "shipping_company": "SMSA", "shipping_company_code": "smsa", "salla_shipment_id": "51",
        CURRENT_SHIPPING: {"company_name": "SMSA", "company_code": "smsa", "shipment_id": "51"},
    }})
    guard = AsyncMock(wraps=shipping._persist_verified_snapshot)
    monkeypatch.setattr(shipping, "_persist_verified_snapshot", guard)
    response = await client.post(PATH)
    assert response.status_code == 200, response.text
    assert response.json()["ready"] is True
    assert response.json()["label_url"] == "https://labels.example/current.pdf"
    assert response.json().get("print_data") is None
    guard.assert_awaited_once()
    assert guard.await_args.kwargs["persist"] is False
    assert guard.await_args.kwargs["allow_current_internal_child"] is False


@pytest.mark.asyncio
async def test_internal_child_read_opt_in_cannot_persist_a_new_shipment(courier_route):
    db, _, _ = courier_route
    before = await db.unified_orders.find_one(KEY)
    baseline = await shipping._label_baseline(db, OWNER, NUMBER)
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping._persist_verified_snapshot(
            db, OWNER, NUMBER,
            {"ready": False, "shipment_id": "51", "status": "created",
             "courier_name": "مندوب الرياض", "courier_code": "0"},
            baseline=baseline, persist=True, allow_current_internal_child=True,
        )
    assert caught.value.code == "shipping_snapshot_changed"
    assert await db.unified_orders.find_one(KEY) == before
