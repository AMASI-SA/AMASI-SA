"""Refresh latency contract with synthetic provider IO and real local fences."""
import asyncio
from copy import deepcopy
from time import perf_counter

import pytest
import order_engine.shipping_label_service as shipping
from test_shipping_print_legacy_path import setup, OWNER, ORDER, dump


@pytest.mark.asyncio
async def test_courier_refresh_reuses_resolved_id_but_reads_final_details(setup, monkeypatch):
    db, state = setup
    state["order"]["shipping"] = {"company_name": "مندوب الرياض"}
    provider = shipping.call_salla

    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.08)
        return await provider(*args, **kwargs)

    monkeypatch.setattr(shipping, "call_salla", delayed)
    before = await dump(db)
    started = perf_counter()
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    elapsed = perf_counter() - started
    print(f"courier_refresh_synthetic seconds={elapsed:.3f} calls={state['calls']}")
    assert result["ready"] and result["label_type"] == "store_courier"
    assert await dump(db) == before
    assert state["calls"] == ["/orders", "/orders/salla-order", "/store/info", "/orders/salla-order"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"status": "delivered"}, {"status": "cancelled"}, {"status": "shipped"},
    {"id": "other-order"}, {"reference_id": "other-reference"},
    {"shipping": {"company_name": "different carrier"}},
])
async def test_final_direct_read_keeps_provider_status_and_identity_guards(setup, monkeypatch, changed):
    db, state = setup
    state["order"]["shipping"] = {"company_name": "مندوب الرياض"}
    provider = shipping.call_salla
    calls = 0

    async def changed_final(*args, **kwargs):
        nonlocal calls
        response = await provider(*args, **kwargs)
        if args[3] == "/orders/salla-order":
            calls += 1
            if calls == 2:
                response = deepcopy(response)
                response["data"].update(changed)
        return response

    monkeypatch.setattr(shipping, "call_salla", changed_final)
    monkeypatch.setattr(shipping, "_stale_label", lambda: shipping.ShippingLabelError("shipping_snapshot_changed", "changed"))
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert caught.value.code in {"order_status_not_completed", "shipping_snapshot_changed", "salla_order_reference_mismatch"}
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_field", ["raw_by_source.salla_direct.id"])
async def test_saved_and_regular_courier_share_print_data_without_keyword_search(setup, monkeypatch, identity_field):
    from salla_shipping import CURRENT_SHIPPING
    db, state = setup
    state["order"]["shipping"] = {"company_name": "مندوب الرياض", "company_code": "0"}
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        "order_date": "2026-10-01T10:00:00Z", "raw_by_source.salla_direct.date.date": "2026-10-01T10:00:00Z", "raw_by_source.salla_direct.reference_id": ORDER,
        identity_field: "salla-order", "shipping_company": "مندوب الرياض",
        "shipping_company_code": "0",
        CURRENT_SHIPPING: {"company_name": "مندوب الرياض", "company_code": "0"},
    }})
    provider = shipping.call_salla
    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.08)
        return await provider(*args, **kwargs)
    monkeypatch.setattr(shipping, "call_salla", delayed)
    started = perf_counter()
    saved = await shipping.refresh_saved_shipping_label(db, OWNER, ORDER)
    regular = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    print(f"combined_courier_synthetic seconds={perf_counter()-started:.3f} calls={state['calls']}")
    metadata = {"assigned_courier_id", "assigned_courier_name", "assignment_id"}
    assert saved["print_data"] == {k: v for k, v in regular["print_data"].items() if k not in metadata}
    assert saved["ready"] and regular["ready"]
    assert state["calls"] == ["/orders/salla-order", "/store/info", "/orders/salla-order", "/store/info", "/orders/salla-order"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"id": "wrong"}, {"reference_id": "wrong"}, {"reference_id": None}])
async def test_ingested_id_is_only_hint_and_mismatch_never_falls_back(setup, changed):
    db, state = setup
    state["order"].update(changed)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"raw_by_source.salla_direct.id": "salla-order"}})
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping._resolve_order(db, OWNER, ORDER)
    assert caught.value.code == "salla_order_reference_mismatch"
    assert state["calls"] == ["/orders/salla-order"]


@pytest.mark.asyncio
async def test_other_owners_ingested_identity_never_used(setup):
    db, state = setup
    await db.unified_orders.insert_one({"user_id": "other-owner", "order_number": ORDER, "order_id": "private-id"})
    internal_id, _ = await shipping._resolve_order(db, OWNER, ORDER)
    assert internal_id == "salla-order"
    assert state["calls"] == ["/orders", "/orders/salla-order"]


@pytest.mark.asyncio
async def test_local_assembly_order_id_is_not_a_provider_routing_hint(setup):
    db, state = setup
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"order_id": "local-assembly"}})
    internal_id, _ = await shipping._resolve_order(db, OWNER, ORDER)
    assert internal_id == "salla-order"
    assert state["calls"] == ["/orders", "/orders/salla-order"]
