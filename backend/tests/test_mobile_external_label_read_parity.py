"""Android fetch reads a completed external label before considering completion.

Exercise actual routes, permissions, provider-label selection and completion
fences. Only provider transport and the Mongo transaction entry are doubled.
"""
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

import preparation_piece_operations as pieces
from fulfillment_v2_routes import make_fulfillment_v2_router
from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as shipping
from salla_integration.service import SallaError
from salla_shipping import CURRENT_SHIPPING
from test_manual_courier_label_refresh_race import scenario


KEY = {"user_id": "owner", "order_number": "3001"}
PATH = "/fulfillment-v2/completed/3001/carrier-label/v1"


@asynccontextmanager
async def external_case(monkeypatch, *, status="completed"):
    db, calls = await scenario(monkeypatch, status=status, external=True)
    monkeypatch.setattr("operational_atomic.operational_owner", shipping.operational_owner)
    monkeypatch.setattr("fulfillment_v2_routes.operational_owner", shipping.operational_owner)
    await db.users.insert_one({"id": "employee", "created_by": "owner", "role": "employee"})
    await db.mezan_mobile_app_access_v1.insert_one({
        "owner_user_id": "owner", "user_id": "employee", "enabled": True,
        "permissions": ["app.page.assembly_shipping"],
    })

    async def actor():
        return {"id": "owner", "_session_client": "amasi_mobile",
                "_mobile_actor_id": "employee", "_mobile_owner_id": "owner"}

    app = FastAPI()
    app.include_router(make_fulfillment_v2_router(db, actor))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield db, calls, client


@pytest.mark.asyncio
@pytest.mark.parametrize("incomplete_cache", [False, True])
async def test_completed_external_fetch_matches_print_without_status_post(monkeypatch, incomplete_cache):
    async with external_case(monkeypatch) as (db, calls, client):
        if incomplete_cache:
            # The durable completion and ready physical unit remain valid. The
            # fresh provider still supplies the full order and its current PDF.
            await db.unified_orders.update_one(KEY, {"$unset": {"raw_by_source.salla_direct.items": ""}})
        assert (await db.order_review_workflows.find_one(KEY))["assembly_status"] == "completed"
        assert (await db[pieces.PIECES].find_one(KEY))["assembly_status"] == "ready"
        printed = await client.post(PATH + "/refresh")
        assert printed.status_code == 200, printed.text
        assert printed.json()["ready"] is True
        calls.clear()

        for _ in range(3):
            response = await client.post(PATH + "/complete-and-refresh")
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["ready"] is True and result["print_state"] == "ready"
            assert result["assembly_completion_confirmed"] is True
            assert result["order_status_completed"] is True
            assert result["shipment_id"] == "99"
            assert result["label_url"] == printed.json()["label_url"] == "https://files.example/aramex-synthetic.pdf"
            assert result["tracking_number"] == "SYNTHETIC-AWB"
            assert not any(method != "GET" for method, _, _ in calls)
        assert [call[:2] for call in calls] == [("GET", "/orders/123")] * 3
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one(KEY)


@pytest.mark.asyncio
async def test_explicit_unconfirmed_external_fetch_keeps_fenced_completion(monkeypatch):
    async with external_case(monkeypatch, status="in_progress") as (db, calls, client):
        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 200, response.text
        assert response.json()["ready"] is True
        assert response.json()["shipment_id"] == "99"
        status_writes = [call for call in calls if call[0] == "POST"]
        assert len(status_writes) == 1
        assert status_writes[0] == ("POST", "/orders/123/status", {
            "json": {"slug": "completed"}, "single_post_attempt": True})
        assert len(calls) <= 3  # Current read, guarded re-read, one status POST.
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one(KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["pending_piece", "held_piece", "incomplete_cache", "pending_source"])
async def test_unconfirmed_external_order_still_requires_current_completion_evidence(monkeypatch, blocker):
    async with external_case(monkeypatch, status="in_progress") as (db, calls, client):
        if blocker == "pending_piece":
            await db[pieces.PIECES].update_one(KEY, {"$set": {"assembly_status": "pending"}})
        elif blocker == "held_piece":
            await db[pieces.PIECES].update_one(KEY, {"$set": {"active_hold_id": "current-hold"}})
        elif blocker == "incomplete_cache":
            await db.unified_orders.update_one(KEY, {"$unset": {"raw_by_source.salla_direct.items": ""}})
        else:
            await db.unified_orders.update_one(KEY, {"$set": {"g47_salla_snapshot.component_pending": True}})

        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "assembly_completion_required"
        assert not any(method == "POST" for method, _, _ in calls)
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one(KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "refunded", "delivered", "shipped", "delivering", "unknown"])
async def test_fresh_noncompleted_external_status_does_not_trigger_completion(monkeypatch, status):
    async with external_case(monkeypatch) as (db, calls, client):
        original = compat.call_salla

        async def provider(*args, **kwargs):
            response = await original(*args, **kwargs)
            response["data"]["status"] = {} if status == "unknown" else {"slug": status}
            return response

        monkeypatch.setattr(compat, "call_salla", provider)
        before = await db.order_review_workflows.find_one(KEY)
        canonical = await db.unified_orders.find_one(KEY)
        response = await client.post(PATH + "/complete-and-refresh")

        assert not any(method == "POST" for method, _, _ in calls)
        assert await db.unified_orders.find_one(KEY) == canonical
        current = await db.order_review_workflows.find_one(KEY)
        if status in {"shipped", "delivering", "delivered"}:
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["ready"] is True and result["shipment_id"] == "99"
            assert result["observed_order_status"] == status
            assert result["order_status_changed"] is False
            assert current["salla_order_status"] == status
            assert current["assembly_status"] == before["assembly_status"]
            assert current["stage"] == before["stage"]
        else:
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] in {"order_cancelled", "order_status_not_completed"}
            assert current == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["salla_error", "timeout"])
async def test_external_provider_failure_does_not_fall_back_to_completion(monkeypatch, failure):
    async with external_case(monkeypatch) as (db, calls, client):
        async def unavailable(database, owner, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if failure == "timeout":
                raise TimeoutError("Synthetic provider timeout")
            raise SallaError("Synthetic provider unavailable", status_code=503)

        monkeypatch.setattr(compat, "call_salla", unavailable)
        monkeypatch.setattr(shipping, "call_salla", unavailable)
        before = await db.order_review_workflows.find_one(KEY)
        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == 502, response.text
        expected = "completion_status_unconfirmed" if failure == "timeout" else "salla_order_unavailable"
        assert response.json()["detail"]["code"] == expected
        assert not any(method == "POST" for method, _, _ in calls)
        assert await db.order_review_workflows.find_one(KEY) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["assembly_pending", "permission_missing"])
async def test_external_fetch_requires_completion_and_employee_grant(monkeypatch, blocker):
    async with external_case(monkeypatch) as (db, calls, client):
        if blocker == "assembly_pending":
            await db.order_review_workflows.update_one(KEY, {"$set": {"assembly_status": "pending"}})
        else:
            await db.mezan_mobile_app_access_v1.update_one({"user_id": "employee"}, {"$set": {"permissions": []}})
        before = await db.order_review_workflows.find_one(KEY)

        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == (409 if blocker == "assembly_pending" else 403), response.text
        assert calls == []
        assert await db.order_review_workflows.find_one(KEY) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["carrier", "shipment", "revoked_permission"])
async def test_changed_shipping_or_authority_during_read_cannot_expose_old_label(monkeypatch, mutation):
    async with external_case(monkeypatch) as (db, calls, client):
        original = compat.call_salla

        async def provider(*args, **kwargs):
            response = await original(*args, **kwargs)
            if mutation == "revoked_permission":
                await db.users.update_one({"id": "employee"}, {"$set": {"disabled": True}})
            else:
                field = "company_code" if mutation == "carrier" else "shipment_id"
                value = "imile" if mutation == "carrier" else "replacement-shipment"
                await db.unified_orders.update_one(KEY, {"$set": {f"{CURRENT_SHIPPING}.{field}": value}})
            return response

        monkeypatch.setattr(compat, "call_salla", provider)
        before = await db.order_review_workflows.find_one(KEY)
        response = await client.post(PATH + "/complete-and-refresh")

        assert response.status_code == (403 if mutation == "revoked_permission" else 409), response.text
        assert not any(method == "POST" for method, _, _ in calls)
        assert await db.order_review_workflows.find_one(KEY) == before
