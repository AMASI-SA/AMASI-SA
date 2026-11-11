"""Exercise history QR fences through the native public refresh endpoint.

Provider transport and Mongo transaction entry are synthetic. The HTTP route,
employee authorization, history proof and final material checks are real.
"""
from copy import deepcopy

import pytest

from salla_shipping import CURRENT_SHIPPING
from test_courier_history_identity_recovery import (
    history_route, KEY, PATH, OWNER, NUMBER, VERSION,
)


EMPLOYEE = "synthetic-history-employee"


@pytest.fixture
async def native_history_route(history_route):
    db, client, state = history_route
    await db.users.insert_one({"id": EMPLOYEE, "created_by": OWNER, "role": "employee"})
    await db.mezan_mobile_app_access_v1.insert_one({
        "owner_user_id": OWNER, "user_id": EMPLOYEE, "enabled": True,
        "permissions": ["app.page.assembly_shipping"],
    })
    state["actor"].clear()
    state["actor"].update({"id": OWNER, "_session_client": "amasi_mobile",
                           "_mobile_actor_id": EMPLOYEE, "_mobile_owner_id": OWNER})
    yield db, client, state


async def assert_no_print(db, response, state, *, status=409):
    assert response.status_code == status, response.text[:500]
    result = response.json()
    assert result["detail"]["print_state"] == "failed"
    assert result.get("ready") is not True and result.get("print_data") is None
    assert all(method == "GET" for method, _ in state["calls"])
    workflow = await db.order_review_workflows.find_one(KEY)
    assert workflow.get("carrier_label_ready") is not True
    assert "assembly_delivery" not in workflow


@pytest.mark.asyncio
async def test_success_keeps_full_source_and_tombstones_unchanged(native_history_route):
    db, client, state = native_history_route
    before = await db.unified_orders.find_one(KEY)
    workflow_before = await db.order_review_workflows.find_one(KEY)

    response = await client.post(PATH)

    assert response.status_code == 200, response.text[:500]
    result = response.json()
    assert result["ready"] is True and result["print_state"] == "ready"
    assert result["print_data"]["order_number"] == NUMBER
    assert result["print_data"]["qr_code"]
    assert all(result.get(key) is None for key in ("shipment_id", "tracking_number", "shipping_number", "label_url"))
    assert await db.unified_orders.find_one(KEY) == before
    assert before[CURRENT_SHIPPING]["superseded_shipment_ids"] == ["51"]
    assert before[CURRENT_SHIPPING]["shipment_id"] is None and before["salla_shipment_id"] is None
    assert await db.salla_shipping_label_inbox.count_documents({"user_id": OWNER}) == 0
    workflow = await db.order_review_workflows.find_one(KEY)
    for key in ("stage", "assembly_status", "store_courier_assignee_id", "store_delivery_assignment_id"):
        assert workflow.get(key) == workflow_before.get(key)
    assert "assembly_delivery" not in workflow
    assert state["order_reads"] == 2
    assert all(method == "GET" for method, _ in state["calls"])


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["version", "customer", "address", "cancelled"])
async def test_final_provider_order_change_blocks_history_qr(native_history_route, change):
    db, client, state = native_history_route
    before = await db.unified_orders.find_one(KEY)
    changed = False

    async def on_read(path):
        nonlocal changed
        if path == "/orders/42" and state["order_reads"] == 2:
            changed = True
            if change == "version":
                state["order"]["updated_at"] = "2026-01-02T12:00:01+00:00"
            elif change == "customer":
                state["order"]["customer"]["full_name"] = "Different synthetic customer"
            elif change == "address":
                state["order"]["shipping_address"] = {"city": "الرياض", "street": "Changed provider destination"}
            else:
                state["order"]["status"] = {"slug": "cancelled"}

    state["on_read"] = on_read
    response = await client.post(PATH)

    assert changed and state["order_reads"] == 2
    await assert_no_print(db, response, state)
    assert response.json()["detail"]["code"] == "shipping_snapshot_changed"
    assert await db.unified_orders.find_one(KEY) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "cancelled", "in_progress", "component_pending", "authoritative_refresh", "revision",
    "verified_address", "raw_address", "assignment", "handoff",
])
async def test_local_change_during_provider_reads_blocks_history_qr(native_history_route, change):
    db, client, state = native_history_route
    changed = False

    async def on_read(path):
        nonlocal changed
        if path != "/orders/42/histories" or changed:
            return
        changed = True
        if change in {"cancelled", "in_progress"}:
            await db.unified_orders.update_one(KEY, {"$set": {
                "order_status": change, "order_status_slug": change,
                "raw_by_source.salla_direct.status.slug": change,
            }})
        elif change in {"component_pending", "authoritative_refresh", "revision"}:
            field, value = {
                "component_pending": ("component_pending", True),
                "authoritative_refresh": ("requires_authoritative_refresh", True),
                "revision": ("revision", "new-synthetic-revision"),
            }[change]
            await db.unified_orders.update_one(KEY, {"$set": {"g47_salla_snapshot." + field: value}})
        elif change == "verified_address":
            await db.unified_orders.update_one(KEY, {"$set": {"shipping_address_raw.street": "Changed local destination"}})
        elif change == "raw_address":
            await db.unified_orders.update_one(KEY, {"$set": {
                "raw_by_source.salla_direct.shipping_address": {"city": "الرياض", "street": "Changed raw destination"},
            }})
        elif change == "assignment":
            await db.order_review_workflows.update_one(KEY, {"$set": {"store_courier_assignee_id": "another-synthetic-driver"}})
        else:
            await db.order_review_workflows.update_one(KEY, {"$set": {"stage": "delivering"}})

    state["on_read"] = on_read
    response = await client.post(PATH)

    assert changed
    await assert_no_print(db, response, state)
    source = await db.unified_orders.find_one(KEY)
    assert source[CURRENT_SHIPPING]["superseded_shipment_ids"] == ["51"]
    assert source[CURRENT_SHIPPING]["shipment_id"] is None and source["salla_shipment_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["disabled_employee", "removed_page_grant"])
async def test_authorization_revoked_before_return_blocks_history_qr(native_history_route, revocation):
    db, client, state = native_history_route
    before = await db.unified_orders.find_one(KEY)
    changed = False

    async def on_read(path):
        nonlocal changed
        if path == "/orders/42" and state["order_reads"] == 2:
            changed = True
            if revocation == "disabled_employee":
                await db.users.update_one({"id": EMPLOYEE}, {"$set": {"disabled": True}})
            else:
                await db.mezan_mobile_app_access_v1.update_one(
                    {"owner_user_id": OWNER, "user_id": EMPLOYEE}, {"$set": {"permissions": []}})

    state["on_read"] = on_read
    response = await client.post(PATH)

    assert changed
    await assert_no_print(db, response, state, status=403)
    assert await db.unified_orders.find_one(KEY) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["already_recorded", "during_final_read"])
async def test_verified_cancellation_inbox_blocks_created_provider_child(native_history_route, timing):
    db, client, state = native_history_route
    before = await db.unified_orders.find_one(KEY)
    # This collection accepts verified webhook observations. Its retained done
    # cancellation must veto a stale positive provider row as well as a pending one.
    cancellation = {
        "_id": "synthetic-verified-cancellation", **KEY, "merchant_id": "synthetic-merchant",
        "order_id": "42", "state": "done",
        "observation": {"shipment_id": "51", "status": "cancelled", "source_kind": "shipment",
                        "event_name": "shipment.cancelled", "provider_updated_at": VERSION,
                        "shipment_updated_at": VERSION},
    }
    if timing == "already_recorded":
        await db.salla_shipping_label_inbox.insert_one(deepcopy(cancellation))
    else:
        async def on_read(path):
            if path == "/orders/42" and state["order_reads"] == 2:
                await db.salla_shipping_label_inbox.insert_one(deepcopy(cancellation))
        state["on_read"] = on_read

    response = await client.post(PATH)

    await assert_no_print(db, response, state)
    assert response.json()["detail"]["code"] == "shipping_snapshot_changed"
    assert state["rows"][0]["status"] == "created"
    assert await db.unified_orders.find_one(KEY) == before
    assert await db.salla_shipping_label_inbox.find_one({"_id": cancellation["_id"]}) == cancellation
