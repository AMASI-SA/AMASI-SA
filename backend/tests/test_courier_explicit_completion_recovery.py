"""Native fetch recovers an unconfirmed courier through the real fenced flow.

Reuse current courier/source/piece fixtures. Only provider transport and the
Mongo transaction entry are doubled; routes, permissions and captures are real.
"""
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI

import preparation_piece_operations as pieces
from fulfillment_v2_routes import make_fulfillment_v2_router
from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as shipping
from salla_integration.service import SallaError
from test_manual_courier_label_refresh_race import scenario


KEY = {"user_id": "owner", "order_number": "3001"}
PATH = "/fulfillment-v2/completed/3001/carrier-label/v1"


@asynccontextmanager
async def route_case(monkeypatch, *, status="in_progress", journal=None, timing="post"):
    db, calls = await scenario(monkeypatch, status=status, timing=timing)
    monkeypatch.setattr("operational_atomic.operational_owner", shipping.operational_owner)
    monkeypatch.setattr("fulfillment_v2_routes.operational_owner", shipping.operational_owner)
    monkeypatch.setattr("order_engine.salla_refresh.call_salla", compat.call_salla)
    await db.unified_orders.update_one(KEY, {"$set": {"order_date": "2026-11-08T09:00:00Z"}})
    assert await compat.mezan_courier_name(db, "owner", "3001") == "مندوب الرياض"
    await db.users.insert_one({"id": "employee", "created_by": "owner", "role": "employee"})
    await db.mezan_mobile_app_access_v1.insert_one({
        "owner_user_id": "owner", "user_id": "employee", "enabled": True,
        "permissions": ["app.page.assembly_shipping"],
    })
    if journal is not None:
        await db.order_review_workflows.update_one(KEY, {"$set": {"assembly_delivery": deepcopy(journal)}})

    async def actor():
        return {"id": "owner", "_session_client": "amasi_mobile",
                "_mobile_actor_id": "employee", "_mobile_owner_id": "owner"}

    app = FastAPI()
    app.include_router(make_fulfillment_v2_router(db, actor))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield db, calls, client


@pytest.mark.asyncio
@pytest.mark.parametrize("journal", [None, {"version": 1, "order_confirmed": False},
                                     {"version": 2, "order_confirmed": False, "state": "pending"}])
async def test_explicit_fetch_completes_unconfirmed_courier_once_and_repeated_fetch_stays_local(monkeypatch, journal):
    async with route_case(monkeypatch, journal=journal) as (db, calls, client):
        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 200, response.text[:500]
        result = response.json()
        assert result["ready"] is True and result["print_state"] == "ready"
        assert result["order_status_completed"] is True
        assert result["assembly_completion_confirmed"] is True
        assert result["label_type"] == "store_courier"
        assert result["courier_name"] == "مندوب الرياض"
        assert result["print_data"]["order_number"] == "3001"
        assert result["print_data"]["qr_code"]
        assert result["shipment_id"] is None and result["label_url"] is None
        assert [call[:2] for call in calls] == [("GET", "/orders/123"), ("POST", "/orders/123/status")]
        assert calls[1][2] == {"json": {"slug": "completed"}, "single_post_attempt": True}
        saved = await db.order_review_workflows.find_one(KEY)
        assert "manual_label_refresh" not in saved
        assert saved.get("assembly_delivery") == journal

        for _ in range(3):
            again = await client.post(PATH + "/complete-and-refresh")
            assert again.status_code == 200, again.text[:500]
            assert again.json()["courier_name"] == result["courier_name"]
            assert again.json()["print_data"]["qr_code"] == result["print_data"]["qr_code"]
            assert again.json()["print_state"] == "ready"
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_confirmed_journal_with_lagging_local_status_keeps_local_document(monkeypatch):
    journal = {"version": 2, "order_confirmed": True, "state": "confirmed", "status_attempted": True}
    async with route_case(monkeypatch, journal=journal) as (db, calls, client):
        await db.order_review_workflows.update_one(KEY, {"$set": {
            "salla_order_status": "completed", "salla_order_status_verified_at": "2026-11-11T10:00:00Z"}})

        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 200, response.text[:500]
        assert response.json()["ready"] is True
        assert response.json()["courier_name"] == "مندوب الرياض"
        assert calls == []
        assert (await db.order_review_workflows.find_one(KEY))["assembly_delivery"] == journal


@pytest.mark.asyncio
async def test_completed_courier_ignores_old_manual_and_delivery_leases_for_local_print(monkeypatch):
    lease = {"token": "old-request", "until": "2099-01-01T00:00:00Z"}
    journal = {"version": 1, "lease_until": "2099-01-01T00:00:00Z", "state": "pending"}
    async with route_case(monkeypatch, status="completed", journal=journal) as (db, calls, client):
        await db.order_review_workflows.update_one(KEY, {"$set": {"manual_label_refresh": lease}})

        for suffix in ("/complete-and-refresh", "/refresh"):
            response = await client.post(PATH + suffix)
            assert response.status_code == 200, response.text[:500]
            assert response.json()["ready"] is True
        assert calls == []
        saved = await db.order_review_workflows.find_one(KEY)
        assert saved["manual_label_refresh"] == lease
        assert saved["assembly_delivery"] == journal


@pytest.mark.asyncio
async def test_ordinary_refresh_does_not_complete_unconfirmed_courier(monkeypatch):
    async with route_case(monkeypatch) as (db, calls, client):
        response = await client.post(PATH + "/refresh")

        assert response.status_code == 200, response.text[:500]
        assert response.json()["order_status_completed"] is False
        assert response.json()["ready"] is False
        assert calls == []
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one(KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["cancelled", "pending_piece", "revoked_access", "handoff", "authoritative_refresh", "component_pending"])
async def test_initial_completion_blockers_never_dispatch_status(monkeypatch, blocker):
    async with route_case(monkeypatch) as (db, calls, client):
        if blocker == "cancelled":
            await db.unified_orders.update_one(KEY, {"$set": {
                "order_status": "cancelled", "raw_by_source.salla_direct.status.slug": "cancelled"}})
        elif blocker == "pending_piece":
            await db[pieces.PIECES].update_one(KEY, {"$set": {"assembly_status": "pending"}})
        elif blocker == "revoked_access":
            await db.users.update_one({"id": "employee"}, {"$set": {"disabled": True}})
        elif blocker == "handoff":
            await db.order_review_workflows.update_one(KEY, {"$set": {"stage": "delivering"}})
        elif blocker == "authoritative_refresh":
            await db.unified_orders.update_one(KEY, {"$set": {"g47_salla_snapshot.requires_authoritative_refresh": True}})
        else:
            await db.unified_orders.update_one(KEY, {"$set": {"g47_salla_snapshot.component_pending": True}})

            async def unavailable_refresh(database, owner, method, path, **kwargs):
                calls.append((method, path, kwargs))
                assert method == "GET"
                raise SallaError("Synthetic component refresh unavailable", status_code=503)

            monkeypatch.setattr("order_engine.salla_refresh.call_salla", unavailable_refresh)

        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code in {403, 409}, response.text[:500]
        assert not [call for call in calls if call[0] == "POST"]
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one(KEY)
        if blocker == "component_pending":
            assert (await db.unified_orders.find_one(KEY))["g47_salla_snapshot"]["component_pending"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_kind", ["manual", "delivery"])
async def test_active_completion_lease_does_not_repeat_status(monkeypatch, lease_kind):
    async with route_case(monkeypatch) as (db, calls, client):
        field, value = ("manual_label_refresh", {"token": "another-request", "until": "2099-01-01T00:00:00Z"}) if lease_kind == "manual" else (
            "assembly_delivery", {"version": 2, "lease_until": "2099-01-01T00:00:00Z", "state": "pending"})
        await db.order_review_workflows.update_one(KEY, {"$set": {field: value}})

        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 409, response.text[:500]
        assert response.json()["detail"]["code"] == "operation_in_progress"
        assert not [call for call in calls if call[0] == "POST"]
        assert (await db.order_review_workflows.find_one(KEY))[field] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "negative_ack"])
async def test_uncertain_or_rejected_status_response_is_not_retried(monkeypatch, failure):
    async with route_case(monkeypatch) as (db, calls, client):
        original = compat.call_salla

        async def provider(database, owner, method, path, **kwargs):
            if method == "POST":
                calls.append((method, path, kwargs))
                if failure == "timeout":
                    raise TimeoutError("synthetic provider timeout")
                return {"success": False}
            return await original(database, owner, method, path, **kwargs)

        monkeypatch.setattr(compat, "call_salla", provider)
        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 502, response.text[:500]
        assert response.json()["detail"]["code"] == "completion_status_unconfirmed"
        assert [call[:2] for call in calls] == [("GET", "/orders/123"), ("POST", "/orders/123/status")]
        saved = await db.order_review_workflows.find_one(KEY)
        assert "manual_label_refresh" not in saved
        assert not saved.get("carrier_label_ready")
        assert (await db.unified_orders.find_one(KEY))["order_status"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["post", "after_document"])
async def test_sparse_order_and_matched_current_child_are_ready_on_first_explicit_fetch(monkeypatch, timing):
    async with route_case(monkeypatch, timing=timing) as (db, calls, client):
        original = compat.call_salla
        child = {"id": 51, "order_id": 123, "type": "shipment", "status": "created",
                 "courier_name": "مندوب الرياض", "courier_id": 77,
                 "meta": {"app_id": 0}, "created_at": None}

        async def provider(database, owner, method, path, **kwargs):
            if method == "GET" and path == "/orders/123":
                response = await original(database, owner, method, path, **kwargs)
                raw = response["data"]
                raw.pop("shipping_company_name")
                raw.pop("shipping_company_code")
                raw["shipping"].pop("company_name")
                raw["shipping"].pop("company_code")
                raw["delivery_method"] = "shipping"
                return response
            if method == "GET" and path in {"/orders/123/shipments", "/shipments", "/shipments/51", "/shipments/51/tracking"}:
                calls.append((method, path, kwargs))
                if path in {"/shipments/51", "/shipments/51/tracking"}:
                    return {"data": deepcopy(child)}
                return {"data": [deepcopy(child)], "pagination": {"totalPages": 1, "currentPage": 1}}
            return await original(database, owner, method, path, **kwargs)

        monkeypatch.setattr(compat, "call_salla", provider)
        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 200, response.text[:500]
        result = response.json()
        assert result["ready"] is True and result["print_state"] == "ready"
        assert result["order_status_completed"] is True
        assert result["label_type"] == "store_courier"
        assert result["courier_name"] == "مندوب الرياض"
        assert result["print_data"]["qr_code"]
        assert result["tracking_number"] is None and result["label_url"] is None
        assert [call[:2] for call in calls].count(("POST", "/orders/123/status")) == 1
        assert [call[:2] for call in calls].count(("GET", "/orders/123")) == 1
        assert ("GET", "/shipments") in [call[:2] for call in calls]
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one(KEY)

        call_count = len(calls)
        for _ in range(3):
            repeated = await client.post(PATH + "/complete-and-refresh")
            assert repeated.status_code == 200, repeated.text[:500]
            assert repeated.json()["courier_name"] == "مندوب الرياض"
            assert repeated.json()["print_state"] == "ready"
        assert len(calls) == call_count


@pytest.mark.asyncio
async def test_sparse_order_without_current_child_fails_closed_after_one_acknowledged_post(monkeypatch):
    async with route_case(monkeypatch) as (db, calls, client):
        original = compat.call_salla

        async def provider(database, owner, method, path, **kwargs):
            response = await original(database, owner, method, path, **kwargs)
            if method == "GET" and path == "/orders/123":
                raw = response["data"]
                raw.pop("shipping_company_name")
                raw.pop("shipping_company_code")
                raw["shipping"].pop("company_name")
                raw["shipping"].pop("company_code")
                raw["delivery_method"] = "shipping"
            return response

        monkeypatch.setattr(compat, "call_salla", provider)
        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 409, response.text[:500]
        assert response.json()["detail"]["code"] == "shipping_snapshot_changed"
        assert [call[:2] for call in calls].count(("POST", "/orders/123/status")) == 1
        assert [call[:2] for call in calls].count(("GET", "/orders/123")) == 1
        saved = await db.order_review_workflows.find_one(KEY)
        assert "manual_label_refresh" not in saved
        assert not saved.get("carrier_label_ready")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoked_access", "handoff"])
async def test_change_during_provider_read_prevents_completion_post(monkeypatch, change):
    async with route_case(monkeypatch) as (db, calls, client):
        original = compat.call_salla

        async def provider(database, owner, method, path, **kwargs):
            response = await original(database, owner, method, path, **kwargs)
            if method == "GET" and path == "/orders/123":
                if change == "revoked_access":
                    await db.users.update_one({"id": "employee"}, {"$set": {"disabled": True}})
                else:
                    await db.order_review_workflows.update_one(KEY, {"$set": {"stage": "delivering"}})
            return response

        monkeypatch.setattr(compat, "call_salla", provider)
        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code in {403, 409}, response.text[:500]
        assert not [call for call in calls if call[0] == "POST"]
        saved = await db.order_review_workflows.find_one(KEY)
        assert "manual_label_refresh" not in saved
        assert not saved.get("carrier_label_ready")
