"""Deterministic status-webhook race: real capture, shipping merge and label flow.

Only provider transport and Mongo transaction entry are doubled; no live APIs.
"""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

import assembly_manual_label_refresh as manual
import preparation_piece_operations as pieces
from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as legacy
from order_engine.mapper import map_salla_order
from salla_shipping import CURRENT_SHIPPING, accept_shipping, extract_shipping


async def scenario(monkeypatch, *, status="in_progress", mutation=None, timing="post"):
    db = AsyncMongoMockClient().manual_courier_race
    key = {"user_id": "owner", "order_number": "3001"}
    raw = {"id": "123", "reference_id": "3001", "status": {"slug": status},
        "date": "2026-11-08T09:00:00Z", "updated_at": "2026-11-08T10:00:00Z",
        "shipping_company_name": "مندوب الرياض", "shipping_company_code": "0",
        "customer": {"name": "Synthetic recipient", "mobile": "0500000001"},
        "shipping": {"company_name": "مندوب الرياض", "company_code": "0",
            "address": {"address_line": "Synthetic street", "city": "Riyadh"}},
        "items": [{"id": "line", "name": "Synthetic product", "quantity": 1}]}
    shipping = accept_shipping({}, extract_shipping(raw, event_name="order.updated"))
    assert shipping
    await db.unified_orders.insert_one({**key, "order_id": "123", "order_status": status,
        "raw_by_source": {"salla_direct": deepcopy(raw)}, CURRENT_SHIPPING: shipping})
    await db.order_review_workflows.insert_one({**key, "stage": "completed", "assembly_status": "completed"})
    await db[pieces.PIECES].insert_one({**key, "piece_id": "piece", "status": "received",
        "assembly_status": "ready", "order_item_id": map_salla_order(raw).items[0].order_item_id,
        "unit_index": 1})
    calls = []
    async def webhook():
        existing = await db.unified_orders.find_one(key)
        updated = deepcopy(raw)
        updated.update(status={"slug": "completed"}, updated_at="2026-11-08T10:01:00Z")
        if mutation == "carrier":
            updated["shipping_company_name"] = updated["shipping"]["company_name"] = "مندوب جدة"
        if mutation == "address":
            updated["shipping"]["address"]["address_line"] = "Changed destination"
        if mutation == "product":
            updated["items"][0]["quantity"] = 2
        accepted = accept_shipping(existing, extract_shipping(updated, event_name="order.updated"))
        assert accepted
        if mutation == "observation_only":
            accepted.update(event_name="order.status.updated", source_kind="order", source_path="shipping_company",
                shipment_updated_at=accepted["provider_updated_at"],
                verified_order_completed_transition={"at": accepted["provider_updated_at"], "shipment_id": None})
        patch = {"order_status": "completed", "raw_by_source.salla_direct": updated, CURRENT_SHIPPING: accepted}
        if mutation in {"component_pending", "requires_authoritative_refresh"}:
            patch["g47_salla_snapshot." + mutation] = True
        await db.unified_orders.update_one(key, {"$set": patch})
    async def provider(_db, owner, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and path == "/orders/123":
            return {"data": deepcopy(raw)}
        if method == "POST" and path == "/orders/123/status":
            assert kwargs == {"json": {"slug": "completed"}, "single_post_attempt": True}
            if timing == "post":
                await webhook()
            return {"success": True}
        if method == "GET" and path in {"/orders/123/shipments", "/shipments"}:
            return {"data": []}
        pytest.fail(f"unexpected provider call {method} {path}")
    async def transaction(database, owner, callback):
        result = await callback(database)
        if timing == "after_document":
            await webhook()
        return result
    monkeypatch.setattr(compat, "call_salla", provider)
    monkeypatch.setattr(legacy, "operational_owner", transaction)
    monkeypatch.setattr(legacy, "_store_identity", AsyncMock(return_value={}))
    return db, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_progress", "completed"])
@pytest.mark.parametrize("timing", ["post", "after_document"])
async def test_same_carrier_status_webhook_does_not_invalidate_manual_document(monkeypatch, status, timing):
    db, calls = await scenario(monkeypatch, status=status, timing=timing)
    result = await manual.complete_and_refresh(db, "owner", "3001", AsyncMock())
    assert result["ready"] and result["order_status_completed"]
    assert result["label_type"] == "store_courier" and result["print_state"] == "ready"
    assert [c[:2] for c in calls].count(("POST", "/orders/123/status")) == 1
    assert [c[:2] for c in calls].count(("GET", "/orders/123")) == 1  # initial resolve only
    saved = await db.order_review_workflows.find_one({"order_number": "3001"})
    assert "manual_label_refresh" not in saved


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["carrier", "address", "product", "component_pending", "requires_authoritative_refresh"])
@pytest.mark.parametrize("timing", ["post", "after_document"])
async def test_material_mutation_during_completed_post_still_blocks_document(monkeypatch, mutation, timing):
    db, calls = await scenario(monkeypatch, mutation=mutation, timing=timing)
    with pytest.raises((legacy.ShippingLabelError, HTTPException)) as error:
        await manual.complete_and_refresh(db, "owner", "3001", AsyncMock())
    code = getattr(error.value, "code", None) or error.value.detail["code"]
    assert code == "shipping_snapshot_changed"
    assert [c[:2] for c in calls].count(("POST", "/orders/123/status")) == 1
    assert [c[:2] for c in calls].count(("GET", "/orders/123")) == 1
    assert "manual_label_refresh" not in await db.order_review_workflows.find_one({"order_number": "3001"})


@pytest.mark.asyncio
async def test_ordinary_completed_print_does_not_post_status(monkeypatch):
    db, calls = await scenario(monkeypatch, status="completed")
    result = await compat.carrier_label(db, "owner", "3001", issue=False, completed_only=True)
    assert result["ready"] and result["label_type"] == "store_courier"
    assert not any(method == "POST" for method, _, _ in calls)


@pytest.mark.asyncio
async def test_repeated_manual_courier_prints_release_lease_and_allow_status_observations(monkeypatch):
    db, calls = await scenario(monkeypatch, mutation="observation_only")
    for _ in range(40):
        result = await manual.complete_and_refresh(db, "owner", "3001", AsyncMock())
        assert result["ready"] and result["print_data"]
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one({"order_number": "3001"})
    assert len(calls) == 80  # one initial GET and one explicit POST per action, no polling


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["shipment_id", "status", "tracking_number", "label_url"])
async def test_courier_snapshot_keeps_material_shipping_fields(monkeypatch, field):
    db, _ = await scenario(monkeypatch)
    before = await manual._capture(db, "owner", "3001")
    after = deepcopy(before)
    after["source"][CURRENT_SHIPPING][field] = "changed"
    after["shipping"][field] = "changed"
    assert not manual._same_material_snapshot(before, after)


@pytest.mark.asyncio
async def test_external_carrier_observation_fence_is_unchanged(monkeypatch):
    db, _ = await scenario(monkeypatch)
    before = await manual._capture(db, "owner", "3001")
    for data in (before["source"][CURRENT_SHIPPING], before["shipping"]):
        data.update(company_name="iMile", company_code="imile")
    after = deepcopy(before)
    for data in (after["source"][CURRENT_SHIPPING], after["shipping"]):
        data["carrier_updated_at"] = "2026-11-08T10:01:00+00:00"
    assert not manual._same_material_snapshot(before, after)
