"""Synthetic Salla boundary; Mongo variant uses an isolated real replica set."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import order_engine.shipping_label_service as shipping
import preparation_piece_operations as operations
from test_shipping_print_boundary import database, seed, OWNER, ORDER


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "unconfirmed", "transport"])
async def test_completion_precedes_document_and_concurrent_attempt_cannot_repeat_post(database, monkeypatch, failure):
    await seed(database, company="مندوب المتجر", code="0")
    await database.order_review_workflows.insert_one({"user_id": OWNER, "order_number": ORDER,
        "stage": "completed", "assembly_status": "completed"})
    order = {"id": "synthetic", "reference_id": ORDER, "status": "in_progress",
             "shipping": {"company_name": "مندوب المتجر", "company_code": "0"}}
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def provider(db, owner, method, path, **kwargs):
        calls.append((method, path))
        if path == "/orders/statuses":
            return {"data": []}
        if method == "POST":
            assert path == "/orders/synthetic/status"
            assert kwargs["json"] == {"slug": "completed"}
            entered.set()
            await release.wait()
            if failure == "transport":
                raise shipping.SallaError("uncertain response", status_code=502)
            if failure is None:
                order["status"] = "completed"
            return {"success": True}
        assert method == "GET" and path == "/orders/synthetic"
        return {"data": deepcopy(order)}

    async def resolve(*args):
        return "synthetic", deepcopy(order)

    monkeypatch.setattr(shipping, "_resolve_order", resolve)
    monkeypatch.setattr(shipping, "_best_effort_resync", AsyncMock())
    monkeypatch.setattr(shipping, "call_salla", provider)
    monkeypatch.setattr(shipping, "_store_identity", AsyncMock(return_value={"name": "Synthetic"}))
    original_document = shipping._internal_delivery_document

    async def document(*args):
        assert order["status"] == "completed"
        return await original_document(*args)

    monkeypatch.setattr(shipping, "_internal_delivery_document", document)
    task = asyncio.create_task(shipping.issue_shipping_label(database, OWNER, ORDER))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(shipping.ShippingLabelError) as duplicate:
            await shipping.issue_shipping_label(database, OWNER, ORDER)
        assert duplicate.value.code == "store_courier_completion_unconfirmed"
        # Printing while POST is pending must neither open nor update Salla.
        with pytest.raises(shipping.ShippingLabelError) as pending:
            await shipping.refresh_shipping_label(database, OWNER, ORDER)
        assert pending.value.code == "store_courier_completion_required"
    finally:
        release.set()
    if failure:
        with pytest.raises(shipping.ShippingLabelError):
            await task
        with pytest.raises(shipping.ShippingLabelError) as replay:
            await shipping.issue_shipping_label(database, OWNER, ORDER)
        assert replay.value.code == "store_courier_completion_unconfirmed"
    else:
        result = await task
        assert result["ready"] and result["order_status_changed"]
        result = await shipping.issue_shipping_label(database, OWNER, ORDER)
        assert result["ready"] and not result["order_status_changed"]
        assert (await shipping.refresh_shipping_label(database, OWNER, ORDER))["ready"]
    assert sum(method == "POST" for method, _ in calls) == 1
    assert not any("shipments" in path for _, path in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "delivered", "shipped", "pending_review", "unknown", ""])
@pytest.mark.parametrize("kind", ["physical", "virtual"])
async def test_non_progress_piece_rejected_before_business_side_effects(database, monkeypatch, status, kind):
    piece_id = "a" * 32
    await database.order_review_workflows.insert_one({"user_id": OWNER, "order_number": ORDER,
        "stage": "ready_to_ship", "operational_items": [{"operational_item_id": piece_id}]})
    if kind == "physical":
        await database[operations.PIECES].insert_one({"user_id": OWNER, "order_number": ORDER,
            "piece_id": piece_id, "status": operations.PIECE_STATUS_READY_FOR_ASSEMBLY,
            "preparation_receipt_status": "received"})
    monkeypatch.setattr(operations, "_current_assembly_order", AsyncMock(return_value=SimpleNamespace(status=status)))
    def forbidden(*args, **kwargs):
        pytest.fail("rejected readiness reached a business side effect")
    for name in ("_consume_piece_components", "_assembly_progress", "enforce_stage_instructions"):
        monkeypatch.setattr(operations, name, forbidden)
    async def dump():
        return {name: await database[name].find({}).to_list(None)
                for name in await database.list_collection_names()}
    before = await dump()
    with pytest.raises(HTTPException) as error:
        await operations._mark_assembly_piece_ready_in_transaction(database, user_id=OWNER,
            piece_id=piece_id, client_request_id="synthetic", actor_id="worker", actor_name="Synthetic")
    assert error.value.status_code == 409
    assert await dump() == before


@pytest.mark.asyncio
async def test_unfinished_assembly_cannot_send_completion(database, monkeypatch):
    await database.order_review_workflows.insert_one({"user_id": OWNER, "order_number": ORDER,
        "stage": "in_progress", "assembly_status": "pending"})
    provider = AsyncMock()
    monkeypatch.setattr(shipping, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping._ensure_internal_order_completed(database, OWNER, ORDER, "synthetic", {
            "id": "synthetic", "reference_id": ORDER, "status": "in_progress",
            "shipping": {"company_name": "مندوب المتجر", "company_code": "0"}})
    assert error.value.code == "assembly_completion_required"
    provider.assert_not_awaited()
