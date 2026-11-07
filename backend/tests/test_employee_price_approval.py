"""Invoice prices remain local until the original dashboard owner decides."""
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from mongomock_motor import AsyncMongoMockClient

import employee_price_approval as approval


OWNER = {"id": "merchant", "role": "owner", "name": "Owner"}
EMPLOYEE = {"id": "merchant", "role": "owner", "_session_client": "amasi_mobile",
            "_mobile_actor_id": "worker", "_mobile_actor_name": "Worker", "_mobile_owner_id": "merchant"}
NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)


async def seed():
    db = AsyncMongoMockClient().price_approval
    await db[approval.PRODUCTS].insert_one({"user_id": "merchant", "id": "product", "mezan_product_id": "product",
        "salla_product_id": "salla-product", "name": "Shawl", "variants": [{"id": "gold"}]})
    await db[approval.COST_PROFILES].insert_one({"user_id": "merchant", "salla_product_id": "salla-product",
        "base_cost": 15, "variant_costs": {"gold": 25}})
    await db[approval.RESOURCES].insert_one({"user_id": "merchant", "id": "service", "name": "Name embroidery",
        "kind": "service", "unit_cost": 10, "track_inventory": False, "status": "active"})
    return db


async def propose(db, changes=None):
    return await approval.propose_invoice_price_changes(db, user_id="merchant", actor=EMPLOYEE,
        invoice_id="invoice", changes=changes or [{"change_type": "product_price", "product_id": "product",
            "product_name": "Shawl", "before_halalas": 2500, "after_halalas": 3000,
            "before_base_halalas": 1500, "after_base_halalas": 2000, "reference_option_cost_halalas": 1000}],
        changed_at=NOW, mongo_session=None)


@pytest.mark.asyncio
async def test_invoice_proposal_does_not_write_catalog_or_invoice_and_is_idempotent():
    db = await seed()
    original = await db[approval.COST_PROFILES].find_one({}, {"_id": 0})
    invoice = {"id": "invoice", "total_halalas": 3000, "status": "payable_posted"}
    await db.mezan_supplier_invoices_v2.insert_one(deepcopy(invoice))
    first = await propose(db)
    second = await propose(db)
    assert first[0]["approval_request_id"] == second[0]["approval_request_id"]
    assert first[0]["applied"] is False
    assert await db[approval.COST_PROFILES].find_one({}, {"_id": 0}) == original
    assert await db.mezan_supplier_invoices_v2.find_one({}, {"_id": 0}) == invoice
    assert await db[approval.REQUESTS].count_documents({}) == 1
    assert await db.settlement_alerts.count_documents({}) == 1
    request = await db[approval.REQUESTS].find_one({}, {"_id": 0})
    assert (request["before_halalas"], request["after_halalas"]) == (1500, 2000)
    assert request["requested_by"] == "worker"
    assert request["invoice_after_halalas"] == 3000


@pytest.mark.asyncio
async def test_owner_approval_changes_base_only_once_and_leaves_invoice_amount():
    db = await seed()
    changes = await propose(db)
    request_id = changes[0]["approval_request_id"]
    await db.mezan_supplier_invoices_v2.insert_one({"id": "invoice", "total_halalas": 3000})
    decided = await approval.decide_price_change(db, user=OWNER, request_id=request_id, decision="approve", mongo_session=None)
    assert decided["request"]["status"] == "approved"
    profile = await db[approval.COST_PROFILES].find_one({})
    assert profile["base_cost"] == 20 and profile["variant_costs"] == {"gold": 25}
    assert (await db.mezan_supplier_invoices_v2.find_one({}))["total_halalas"] == 3000
    again = await approval.decide_price_change(db, user=OWNER, request_id=request_id, decision="approve", mongo_session=None)
    assert again["idempotent"] is True
    assert await db[approval.AUDIT].count_documents({"event_type": "employee_price_change_approved"}) == 1
    assert (await db.settlement_alerts.find_one({}))["status"] == "resolved"


@pytest.mark.asyncio
async def test_invoice_readback_status_updates_without_rewriting_approved_invoice():
    db = await seed()
    changes = await propose(db)
    original = {"id": "invoice", "total_halalas": 3000, "price_changes": changes,
        "price_updates_applied": False, "price_approval_pending": True}
    await db.mezan_supplier_invoices_v2.insert_one(deepcopy(original))
    first = await approval.invoice_approval_view(db, owner="merchant", invoice=original)
    assert first["price_approval_pending"] is True
    await approval.decide_price_change(db, user=OWNER, request_id=changes[0]["approval_request_id"], decision="approve", mongo_session=None)
    final = await approval.invoice_approval_view(db, owner="merchant", invoice=original)
    assert final["price_approval_pending"] is False and final["price_changes"][0]["approval_status"] == "approved"
    assert final["total_halalas"] == 3000 and final["price_updates_applied"] is False
    assert await db.mezan_supplier_invoices_v2.find_one({}, {"_id": 0}) == original


@pytest.mark.asyncio
async def test_variant_and_service_approval_are_target_specific_and_stale_safe():
    db = await seed()
    changes = await propose(db, [
        {"change_type": "product_price", "product_id": "product", "variant_id": "gold", "before_halalas": 2500, "after_halalas": 3500},
        {"change_type": "service_price", "service_id": "service", "before_halalas": 1000, "after_halalas": 1200},
    ])
    await db[approval.COST_PROFILES].update_one({}, {"$set": {"base_cost": 99}})
    await approval.decide_price_change(db, user=OWNER, request_id=changes[0]["approval_request_id"], decision="approve", mongo_session=None)
    profile = await db[approval.COST_PROFILES].find_one({})
    assert profile["base_cost"] == 99 and profile["variant_costs"]["gold"] == 35
    await db[approval.RESOURCES].update_one({}, {"$set": {"unit_cost": 11}})
    with pytest.raises(HTTPException) as error:
        await approval.decide_price_change(db, user=OWNER, request_id=changes[1]["approval_request_id"], decision="approve", mongo_session=None)
    assert error.value.status_code == 409 and error.value.detail["code"] == "price_change_stale"
    assert (await db[approval.RESOURCES].find_one({}))["unit_cost"] == 11


@pytest.mark.asyncio
async def test_rejection_preserves_catalog_and_foreign_or_native_owner_cannot_decide():
    db = await seed()
    request_id = (await propose(db))[0]["approval_request_id"]
    for user in (EMPLOYEE, {"id": "worker", "role": "employee"}, {"id": "other", "role": "owner"}):
        with pytest.raises(HTTPException) as error:
            await approval.decide_price_change(db, user=user, request_id=request_id, decision="approve", mongo_session=None)
        assert error.value.status_code in (403, 404)
    result = await approval.decide_price_change(db, user=OWNER, request_id=request_id, decision="reject", mongo_session=None)
    assert result["request"]["status"] == "rejected"
    assert (await db[approval.COST_PROFILES].find_one({}))["base_cost"] == 15


@pytest.mark.asyncio
async def test_http_read_contract_is_owner_scoped():
    db = await seed()
    request_id = (await propose(db))[0]["approval_request_id"]
    for user, expected in ((OWNER, 200), (EMPLOYEE, 403)):
        app = FastAPI()
        app.include_router(approval.make_price_change_router(db, lambda: user))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/price-change-requests-v1/{request_id}")
            assert response.status_code == expected
            if expected == 200:
                assert response.json()["request"]["status"] == "pending"
                listed = await client.get("/price-change-requests-v1?status=pending")
                assert listed.json()["pending_count"] == 1


@pytest.mark.asyncio
async def test_mobile_product_editor_defers_prices_but_dashboard_keeps_direct_save(monkeypatch):
    from product_v2_details_routes import make_product_v2_details_router
    db = await seed()
    async def memory_transaction(_db, work):
        return await work(None)
    monkeypatch.setattr(approval, "atomic_price_change", memory_transaction)
    for user in (EMPLOYEE, OWNER):
        app = FastAPI(); app.include_router(make_product_v2_details_router(db, lambda: user))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put("/products-v2/product/costs", json={"base_cost": 20, "variant_costs": {"gold": 25}})
            assert response.status_code == 200, response.text
            if user is EMPLOYEE:
                assert response.json()["approval_required"] is True
                assert response.json()["catalog_updated"] is False
                assert (await db[approval.COST_PROFILES].find_one({}))["base_cost"] == 15
            else:
                assert (await db[approval.COST_PROFILES].find_one({}))["base_cost"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/price-review"])
async def test_mobile_service_edit_keeps_metadata_and_defers_shared_cost(monkeypatch, suffix):
    from component_category_required_routes import make_component_category_required_router
    from component_category_policy import COMPONENT_CATEGORIES
    db = await seed()
    await db[COMPONENT_CATEGORIES].insert_one({"user_id": "merchant", "id": "category", "name": "Tailoring", "status": "active"})
    await db[approval.RESOURCES].update_one({}, {"$set": {"category_ids": ["category"], "code": "SVC"}})
    async def memory_transaction(_db, work):
        return await work(None)
    monkeypatch.setattr(approval, "atomic_price_change", memory_transaction)
    app = FastAPI(); app.include_router(make_component_category_required_router(db, lambda: EMPLOYEE))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/components-v2/service" + suffix, json={"unit_cost": 12, "name": "New label", "category_ids": ["category"]})
        assert response.status_code == 200, response.text
        assert response.json()["approval_required"] is True
    service = await db[approval.RESOURCES].find_one({})
    assert service["name"] == "New label" and service["unit_cost"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/review"])
async def test_mobile_direct_option_amount_cannot_bypass_owner_approval(monkeypatch, suffix):
    from product_option_cost_routes import make_product_option_cost_router, BINDINGS
    db = await seed()
    await db[approval.PRODUCTS].update_one({}, {"$set": {"options": [{"id": "option", "name": "Add name", "values": [{"id": "yes", "name": "Yes"}]}]}})
    await db[BINDINGS].insert_one({"user_id": "merchant", "salla_product_id": "salla-product", "option_id": "option",
        "value_id": "yes", "mode": "direct", "direct_amount": 10, "quantity": 1})
    async def memory_transaction(_db, work):
        return await work(None)
    monkeypatch.setattr(approval, "atomic_price_change", memory_transaction)
    app = FastAPI(); app.include_router(make_product_option_cost_router(db, lambda: EMPLOYEE))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/products-v2/product/option-costs/option/yes" + suffix, json={"mode": "direct", "direct_amount": 12, "quantity": 1})
        assert response.status_code == 200, response.text
        assert response.json()["approval_required"] is True
        request_id = response.json()["pending_request_id"]
    assert (await db[BINDINGS].find_one({}))["direct_amount"] == 10
    await approval.decide_price_change(db, user=OWNER, request_id=request_id, decision="approve", mongo_session=None)
    assert (await db[BINDINGS].find_one({}))["direct_amount"] == 12
    assert (await db[approval.COST_PROFILES].find_one({}))["base_cost"] == 15


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,expected", [(18.004, 18), (0.005, 0.01)])
async def test_owner_approves_exact_displayed_halalas_not_hidden_fraction(amount, expected, monkeypatch):
    from product_v2_details_routes import make_product_v2_details_router
    db = await seed()
    async def memory_transaction(_db, work):
        return await work(None)
    monkeypatch.setattr(approval, "atomic_price_change", memory_transaction)
    app = FastAPI(); app.include_router(make_product_v2_details_router(db, lambda: EMPLOYEE))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/products-v2/product/costs/review", json={"base_cost": amount, "variant_costs": {"gold": 25}, "client_request_id": "precision-request"})
        assert response.status_code == 200, response.text
        request = response.json()["requests"][0]
    assert request["after_halalas"] / 100 == expected
    await approval.decide_price_change(db, user=OWNER, request_id=request["id"], decision="approve", mongo_session=None)
    assert (await db[approval.COST_PROFILES].find_one({}))["base_cost"] == expected


@pytest.mark.asyncio
async def test_rejected_proposal_can_be_resubmitted_with_a_new_key_and_retry_is_deduplicated(monkeypatch):
    db = await seed()
    async def memory_transaction(_db, work):
        return await work(None)
    monkeypatch.setattr(approval, "atomic_price_change", memory_transaction)
    product = await db[approval.PRODUCTS].find_one({})
    kwargs = dict(user=EMPLOYEE, product=product, base_cost=20, variant_costs={"gold": 25})
    first = await approval.propose_mobile_product_costs(db, **kwargs, client_request_id="first-save")
    replay = await approval.propose_mobile_product_costs(db, **kwargs, client_request_id="first-save")
    assert first["pending_request_id"] == replay["pending_request_id"]
    await approval.decide_price_change(db, user=OWNER, request_id=first["pending_request_id"], decision="reject", mongo_session=None)
    second = await approval.propose_mobile_product_costs(db, **kwargs, client_request_id="second-save")
    assert second["pending_request_id"] != first["pending_request_id"]
    assert second["status"] == "pending"
    assert await db[approval.REQUESTS].count_documents({}) == 2
    with pytest.raises(HTTPException) as error:
        await approval.propose_mobile_product_costs(db, **{**kwargs, "base_cost": 21}, client_request_id="second-save")
    assert error.value.detail["code"] == "price_change_request_conflict"


@pytest.mark.asyncio
async def test_alerts_http_exposes_standard_type_and_review_link(monkeypatch):
    import alerts_routes
    from unittest.mock import AsyncMock
    db = await seed()
    request_id = (await propose(db))[0]["approval_request_id"]
    monkeypatch.setattr(alerts_routes, "_gen_integration_health", AsyncMock(return_value=0))
    app = FastAPI(); alerts_routes.attach_alerts_routes(app, db, lambda: OWNER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/alerts")
        assert response.status_code == 200, response.text
        alert = response.json()["alerts"][0]
    assert alert["alert_type"] == "employee_price_change"
    assert alert["related_entity_url"] == f"/price-change-requests/{request_id}"


@pytest.mark.asyncio
async def test_review_alias_never_directly_saves_a_price_without_native_identity():
    from product_v2_details_routes import make_product_v2_details_router
    from component_category_required_routes import make_component_category_required_router
    from product_option_cost_routes import make_product_option_cost_router
    db = await seed()
    app = FastAPI(); app.include_router(make_product_v2_details_router(db, lambda: OWNER))
    app.include_router(make_component_category_required_router(db, lambda: OWNER))
    app.include_router(make_product_option_cost_router(db, lambda: OWNER))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path, payload in (("/products-v2/product/costs/review", {"base_cost": 20}),
                              ("/components-v2/service/price-review", {"unit_cost": 20}),
                              ("/products-v2/product/option-costs/option/yes/review", {"mode": "direct", "direct_amount": 20})):
            response = await client.put(path, json=payload)
            assert response.status_code == 403
        removed = await client.request("DELETE", "/products-v2/product/option-costs/option/yes/review", json={"client_request_id": "remove-price"})
        assert removed.status_code == 403
    assert (await db[approval.COST_PROFILES].find_one({}))["base_cost"] == 15
    assert (await db[approval.RESOURCES].find_one({}))["unit_cost"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_legacy_service_cost_shortcut_also_defers_price(monkeypatch, fallback):
    from component_edit_routes import make_component_edit_router
    from product_option_cost_routes import make_product_option_cost_router
    db = await seed()
    async def memory_transaction(_db, work):
        return await work(None)
    monkeypatch.setattr(approval, "atomic_price_change", memory_transaction)
    router = make_product_option_cost_router if fallback else make_component_edit_router
    app = FastAPI(); app.include_router(router(db, lambda: EMPLOYEE))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/components-v2/service/cost", json={"amount": 20})
    assert response.status_code == 200 and response.json()["approval_required"] is True
    assert (await db[approval.RESOURCES].find_one({}))["unit_cost"] == 10


@pytest.mark.asyncio
async def test_option_cost_picker_exposes_current_product_category():
    from product_option_cost_routes import make_product_option_cost_router
    db = await seed()
    await db[approval.PRODUCTS].update_one({}, {"$set": {"cost_category_id": "tailoring", "cost_category_name": "Tailoring"}})
    app = FastAPI(); app.include_router(make_product_option_cost_router(db, lambda: OWNER))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/products-v2/product/option-costs")
    assert response.status_code == 200
    assert response.json()["cost_category_id"] == "tailoring" and response.json()["cost_category_name"] == "Tailoring"


@pytest.mark.asyncio
async def test_direct_option_deletion_and_resource_replacement_cannot_erase_cost_without_approval(monkeypatch):
    from product_option_cost_routes import make_product_option_cost_router, BINDINGS
    db = await seed()
    await db[approval.PRODUCTS].update_one({}, {"$set": {"options": [{"id": "option", "name": "Add name", "values": [{"id": "yes", "name": "Yes"}]}]}})
    await db[BINDINGS].insert_one({"user_id": "merchant", "salla_product_id": "salla-product", "option_id": "option", "option_name": "Add name",
        "value_id": "yes", "value_name": "Yes", "mode": "direct", "direct_amount": 10, "quantity": 1})
    async def memory_transaction(_db, work):
        return await work(None)
    monkeypatch.setattr(approval, "atomic_price_change", memory_transaction)
    app = FastAPI(); app.include_router(make_product_option_cost_router(db, lambda: EMPLOYEE))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.put("/products-v2/product/option-costs/option/yes", json={"mode": "resource", "resource_id": "service"})
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "direct_cost_removal_requires_approval"
        response = await client.request("DELETE", "/products-v2/product/option-costs/option/yes/review", json={"client_request_id": "remove-price"})
        assert response.status_code == 200, response.text
        assert response.json()["approval_required"] is True
        request_id = response.json()["pending_request_id"]
    assert (await db[BINDINGS].find_one({}))["direct_amount"] == 10
    request = await db[approval.REQUESTS].find_one({"id": request_id})
    assert request["removes_override"] is True and request["option_name"] == "Add name"
    assert request["before_quantity"] == 1 and request["after_quantity"] is None
    await approval.decide_price_change(db, user=OWNER, request_id=request_id, decision="approve", mongo_session=None)
    assert await db[BINDINGS].count_documents({}) == 0
    assert (await db[approval.RESOURCES].find_one({}))["unit_cost"] == 10


@pytest.mark.asyncio
async def test_option_quantity_only_change_discloses_effect_and_waits_for_owner(monkeypatch):
    from product_option_cost_routes import make_product_option_cost_router, BINDINGS
    db = await seed()
    await db[approval.PRODUCTS].update_one({}, {"$set": {"options": [{"id": "option", "name": "Add name", "values": [{"id": "yes", "name": "Yes"}]}]}})
    # Legacy bindings without quantity use one unit in existing cost calculation.
    await db[BINDINGS].insert_one({"user_id": "merchant", "salla_product_id": "salla-product", "option_id": "option",
        "value_id": "yes", "mode": "direct", "direct_amount": 10})
    async def memory_transaction(_db, work):
        return await work(None)
    monkeypatch.setattr(approval, "atomic_price_change", memory_transaction)
    app = FastAPI(); app.include_router(make_product_option_cost_router(db, lambda: EMPLOYEE))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/products-v2/product/option-costs/option/yes/review", json={
            "mode": "direct", "direct_amount": 10, "quantity": 2, "client_request_id": "quantity-change"})
    assert response.status_code == 200 and response.json()["approval_required"] is True
    request = response.json()["requests"][0]
    assert request["before_halalas"] == request["after_halalas"] == 1000
    assert request["before_quantity"] == 1 and request["after_quantity"] == 2
    assert "quantity" not in await db[BINDINGS].find_one({})
    await approval.decide_price_change(db, user=OWNER, request_id=request["id"], decision="approve", mongo_session=None)
    approved = await db[BINDINGS].find_one({})
    assert approved["quantity"] == 2 and approved["direct_amount"] == 10


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 90_071_992_547_410, -1])
def test_unreviewable_money_values_are_rejected(value):
    with pytest.raises(HTTPException):
        approval._money(value)
