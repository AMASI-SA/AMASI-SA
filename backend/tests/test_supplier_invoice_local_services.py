"""A service added in a draft belongs only to its selected physical pieces."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import supplier_receiving_routes as receiving
from supplier_invoice_local_services import LOCAL_SERVICES, add_local_service, service_candidates
from test_supplier_refresh_wrapper import installed_live_cost_support, option_cost_draft


CONTEXT = {"merchant_id": "merchant", "actor_id": "worker"}
ACTOR = {"id": "worker", "name": "Worker"}


async def seed_local():
    db, piece = await option_cost_draft({"product_options_snapshot": {"Customization": "Name"}})
    await db[receiving.PRODUCTS].update_one({}, {"$set": {"cost_category_id": "tailoring", "cost_category_name": "Tailoring"}})
    await db[receiving.RESOURCES].insert_many([
        {"user_id": "merchant", "id": "name", "name": "Embroidery", "kind": "service", "unit_cost": 10, "category_ids": ["tailoring"], "status": "active"},
        {"user_id": "merchant", "id": "other", "name": "Unrelated", "kind": "service", "unit_cost": 20, "category_ids": ["other-category"], "status": "active"},
        {"user_id": "other-merchant", "id": "foreign", "name": "Foreign", "kind": "service", "unit_cost": 30, "category_ids": ["tailoring"]},
    ])
    await db[receiving.PIECES].update_one({}, {"$set": {"supplier_receiving_session_id": "draft"}})
    await db[receiving.PIECES].insert_one({**piece, "piece_id": "piece-2", "product_options_snapshot": {"Customization": "Other"}, "supplier_receiving_session_id": "draft"})
    await db[receiving.RECEIVING_EVENTS].insert_one({"user_id": "merchant", "session_id": "draft", "event_type": "supplier_piece_scanned",
        "piece_id": "piece-2", "product_id": "product-1"})
    await db[receiving.SESSIONS].update_one({}, {"$set": {"opened_by": "worker"}})
    return db, await db[receiving.SESSIONS].find_one({})


@pytest.mark.asyncio
async def test_candidates_use_product_category_and_no_category_returns_empty():
    db, session = await seed_local()
    found = await service_candidates(db, context=CONTEXT, session=session, product_id="product-1", piece_ids=["piece-1"])
    assert found["scope"] == "invoice" and found["category_required"] is False
    assert [row["id"] for row in found["services"]] == ["name"]
    assert found["selected_options"] == [{"option_name": "Customization", "value_name": "Name", "piece_ids": ["piece-1"]}]
    await db[receiving.PRODUCTS].update_one({}, {"$unset": {"cost_category_id": ""}})
    missing = await service_candidates(db, context=CONTEXT, session=session, product_id="product-1", piece_ids=["piece-1"])
    assert missing["category_required"] is True and missing["services"] == []
    assert missing["message"] == "لا يوجد تصنيف لإضافة خدمة له"


@pytest.mark.asyncio
async def test_selected_piece_service_survives_real_refresh_wrapper_without_catalog_write(installed_live_cost_support):
    installed_live_cost_support()
    db, session = await seed_local()
    kwargs = dict(context=CONTEXT, actor=ACTOR, session=session, product_id="product-1", service_id="name",
        piece_ids=["piece-1"], option_condition={"option_name": "Customization", "value_name": "Name"}, mongo_session=None)
    first = await add_local_service(db, **kwargs)
    again = await add_local_service(db, **kwargs)
    assert first["scope"] == "invoice" and again["permanent_for_future_orders"] is False
    assert await db[LOCAL_SERVICES].count_documents({}) == 1
    assert await db[receiving.PRODUCT_RESOURCE_BINDINGS].count_documents({}) == 0
    assert await db[receiving.BINDINGS].count_documents({"mode": "resource"}) == 0
    assert (await db[receiving.PIECES].find_one({"piece_id": "piece-1"}))["services"] == []
    for refresh in (False, True, False):
        rows = await receiving._recent_session_events(db, user_id="merchant", session_id="draft", refresh_product_services=refresh)
        selected = next(row for row in rows if row["piece_id"] == "piece-1")
        other = next(row for row in rows if row["piece_id"] == "piece-2")
        assert len(selected["invoice_services"]) == 1
        assert selected["invoice_services"][0]["invoice_scope"] is True
        assert selected["invoice_services"][0]["eligibility_condition"] == kwargs["option_condition"]
        assert other["invoice_services"] == []
    # A new draft does not inherit any local addition from this invoice.
    await db[receiving.SESSIONS].insert_one({**{k:v for k,v in session.items() if k != "_id"}, "id": "future"})
    await db[receiving.RECEIVING_EVENTS].insert_one({"user_id": "merchant", "session_id": "future", "event_type": "supplier_piece_scanned",
        "piece_id": "piece-2", "product_id": "product-1"})
    assert (await receiving._recent_session_events(db, user_id="merchant", session_id="future"))[0]["invoice_services"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("service_id,pieces,condition", [
    ("other", ["piece-1"], None),
    ("foreign", ["piece-1"], None),
    ("name", ["piece-2"], {"option_name": "Customization", "value_name": "Name"}),
    ("name", ["not-scanned"], None),
])
async def test_invalid_category_option_or_piece_scope_is_rejected(service_id, pieces, condition):
    db, session = await seed_local()
    with pytest.raises(HTTPException):
        await add_local_service(db, context=CONTEXT, actor=ACTOR, session=session, product_id="product-1", service_id=service_id,
            piece_ids=pieces, option_condition=condition, mongo_session=None)
    assert await db[LOCAL_SERVICES].count_documents({}) == 0
    assert await db[receiving.PRODUCT_RESOURCE_BINDINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_mixed_customer_choices_return_explicit_matching_piece_subsets():
    db, session = await seed_local()
    result = await service_candidates(db, context=CONTEXT, session=session, product_id="product-1", piece_ids=["piece-1", "piece-2"])
    assert {choice["value_name"]: choice["piece_ids"] for choice in result["selected_options"]} == {
        "Name": ["piece-1"], "Other": ["piece-2"],
    }
    with pytest.raises(HTTPException) as error:
        await add_local_service(db, context=CONTEXT, actor=ACTOR, session=session, product_id="product-1", service_id="name",
            piece_ids=["piece-1", "piece-2"], option_condition={"option_name": "Customization", "value_name": "Name"})
    assert error.value.detail["code"] == "supplier_invoice_option_not_selected"
    assert await db[LOCAL_SERVICES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_http_local_endpoint_requires_piece_scope_and_old_permanent_route_rejects(monkeypatch):
    db, _session = await seed_local()
    context = {**CONTEXT, "is_owner": False,
        "permissions": {receiving.RECEIVE_PERMISSION, receiving.ADD_PRODUCT_SERVICE_PERMISSION}}
    monkeypatch.setattr(receiving, "_actor_context", AsyncMock(return_value=context))
    # MongoMock has no transactions; replace only the transaction boundary.
    class MemorySession:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def with_transaction(self, work): return await work(None)
    monkeypatch.setattr(db.client, "start_session", AsyncMock(return_value=MemorySession()))
    app = FastAPI(); app.include_router(receiving.make_supplier_receiving_router(db, lambda: ACTOR))
    path = "/supplier-receiving-v1/sessions/draft/products/product-1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        old = await client.post(path + "/services/name", json={"piece_ids": ["piece-1"]})
        assert old.status_code == 409
        assert old.json()["detail"]["code"] == "supplier_invoice_local_service_endpoint_required"
        missing = await client.post(path + "/invoice-services/name", json={})
        assert missing.status_code == 422
        options = await client.get(path + "/invoice-service-candidates?piece_ids=piece-1,piece-2")
        assert options.status_code == 200 and options.json()["scope"] == "invoice"
        saved = await client.post(path + "/invoice-services/name", json={"piece_ids": ["piece-1"],
            "option_condition": {"option_name": "Customization", "value_name": "Name"}})
        assert saved.status_code == 201, saved.text
        assert saved.json()["affected_piece_ids"] == ["piece-1"]
        assert saved.json()["permanent_for_future_orders"] is False
        context["actor_id"] = "another-worker"
        denied = await client.get(path + "/invoice-service-candidates?piece_ids=piece-1")
        assert denied.status_code == 403
    assert await db[receiving.PRODUCT_RESOURCE_BINDINGS].count_documents({}) == 0
    assert await db[LOCAL_SERVICES].count_documents({}) == 1


@pytest.mark.asyncio
async def test_invoice_and_piece_history_keep_local_choice_when_identical_services_group(installed_live_cost_support):
    installed_live_cost_support()
    db, session = await seed_local()
    # The same product/service prices can group despite different customer options.
    await db[receiving.BINDINGS].delete_many({})
    for piece_id, value in (("piece-1", "Name"), ("piece-2", "Other")):
        await add_local_service(db, context=CONTEXT, actor=ACTOR, session=session, product_id="product-1", service_id="name",
            piece_ids=[piece_id], option_condition={"option_name": "Customization", "value_name": value})
    scans = await receiving._recent_session_events(db, user_id="merchant", session_id="draft")
    now = datetime.now(timezone.utc)
    lines = [receiving.SupplierReceivingInvoiceLineRequest(piece_ids=[row["piece_id"]], product_unit_price_halalas=4000,
        services=[receiving.SupplierReceivingInvoiceServiceRequest(service_id="name", unit_price_halalas=1200)]) for row in scans]
    invoice = receiving.build_supplier_receiving_invoice(session=session, scans=scans, requested_lines=lines, saved_at=now,
        permissions={receiving.EDIT_SERVICE_PRICE_PERMISSION})
    assert invoice["total_halalas"] == 10400
    assert invoice["line_count"] == 1
    line = invoice["lines"][0]
    assert sorted(line["services"][0]["invoice_scope_piece_ids"]) == ["piece-1", "piece-2"]
    for piece_id, value in (("piece-1", "Name"), ("piece-2", "Other")):
        piece = await db[receiving.PIECES].find_one({"piece_id": piece_id})
        update = receiving.supplier_service_completion_update(piece=piece, invoice_line=line, session=session,
            actor=ACTOR, invoice_id="invoice", completed_at=now)
        recorded = update["$set"]["services"][0]
        assert recorded["invoice_scope"] is True and recorded["condition"]["value_name"] == value
        assert recorded["supplier_unit_price_halalas"] == 1200
    assert await db[receiving.PRODUCT_RESOURCE_BINDINGS].count_documents({}) == 0
    assert (await db[receiving.RESOURCES].find_one({"id": "name"}))["unit_cost"] == 10
