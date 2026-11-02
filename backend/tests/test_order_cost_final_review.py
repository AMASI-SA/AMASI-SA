"""Final independent acceptance seams: endpoint parity and historical evidence."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest

from order_cost_authority import (
    CORRECTIONS, LIFECYCLES, SNAPSHOTS, calculate_catalog_line_cost,
    line_fingerprint, load_order_cost_authority, resolve_order_line_cost,
)
from order_cost_reporting_repair import source_authority_evidence_hash
from order_engine.models import OrderDTO, OrderItemDTO, OrderSourceDTO, PaymentDTO
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS


def _item(**changes):
    return {"order_item_id": "line", "source_item_id": "line", "product_id": "shawl",
            "quantity": 4, "options_raw": [{"option_id": "rose", "value_id": "yes"}], **changes}


def _catalog(item, base=15):
    return calculate_catalog_line_cost(item, product={}, profile={"base_cost": base},
                                      product_bindings=[], option_bindings=[], resources={})


async def _resolve(db, item):
    context = await load_order_cost_authority(db, "owner", [{"order_number": "order", "products": [item]}])
    return resolve_order_line_cost(item, order_number="order", catalog_cost=_catalog(item), authority=context)


async def _seed_manual_plus_bundle(db, *, correction=False):
    item = _item()
    manual = {
        "id": "original-manual-proof", "user_id": "owner", "order_number": "order",
        "order_item_id": "line", "salla_product_id": "shawl", "quantity": 4,
        "line_cost": 60, "base_unit_cost": 15, "currency": "SAR", "cost_complete": True,
        "selected_options": [["id:rose", "id:yes"]],
    }
    await db[SNAPSHOTS].insert_one(deepcopy(manual))
    snapshot = {
        "product_id": "shawl", "sale_quantity": 4, "line_fingerprint": line_fingerprint(item),
        "selected_options": [{"option_id": "rose", "value_id": "yes"}],
        "estimated_cost": "40" if correction else "55", "revision": 1,
        "leaves": [{"kind": "product", "product_id": "bouquet", "quantity": "1", "unit_cost": "40"}],
    }
    if not correction:
        snapshot["leaves"].append({"kind": "product", "product_id": "shawl", "quantity": "1",
                                   "unit_cost": "15", "current_product_reference": True})
    await db[LIFECYCLES].insert_one({
        "user_id": "owner", "order_number": "order", "bundle_intake_snapshots": {"line": snapshot},
    })
    if correction:
        context = await load_order_cost_authority(db, "owner", [{"order_number": "order", "products": [item]}])
        record = context["lines"][("order", "line")]
        cost = deepcopy(record["cost"])
        cost.update(base_total=60, line_total=220)
        await db[CORRECTIONS].insert_one({
            "user_id": "owner", "order_number": "order", "order_item_id": "line", "product_id": "shawl",
            "quantity": 4, "line_fingerprint": line_fingerprint(item), "state": "applied", "cost": cost,
            "source_authority_evidence_hash": source_authority_evidence_hash(record),
        })
    return item, manual


@pytest.mark.asyncio
@pytest.mark.parametrize("correction", [False, True], ids=["expected-bundle", "applied-correction"])
async def test_snapshot_post_returns_unified_cost_without_rewriting_old_manual_proof(correction):
    from order_option_cost_snapshot_routes import calculate_order_cost_snapshot
    db = AsyncMongoMockClient().snapshot_post_final_review
    item, original = await _seed_manual_plus_bundle(db, correction=correction)
    assert (await _resolve(db, item))["line_total"] == 220

    result = await calculate_order_cost_snapshot(
        db, user_id="owner", order=SimpleNamespace(order_number="order", order_id="provider-order", items=[item]),
    )

    assert result["total_cost"] == 220
    assert result["items"][0]["line_cost"] == 220
    assert await db[SNAPSHOTS].find_one({"id": original["id"]}, {"_id": 0}) == original
    assert await db[SNAPSHOTS].count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("correction", [False, True], ids=["expected-bundle", "applied-correction"])
async def test_snapshot_get_returns_the_same_unified_cost_as_reports(monkeypatch, correction):
    import order_option_cost_snapshot_routes as routes
    db = AsyncMongoMockClient().snapshot_get_final_review
    item, original = await _seed_manual_plus_bundle(db, correction=correction)

    async def current_user():
        return {"id": "owner"}

    async def fixture_order(*args, **kwargs):
        return SimpleNamespace(order_number="order", order_id="provider-order", items=[item])

    monkeypatch.setattr(routes, "get_order", fixture_order)
    app = FastAPI()
    app.include_router(routes.make_order_option_cost_snapshot_router(db, current_user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/orders-v2/order/cost-snapshot")

    assert response.status_code == 200
    assert response.json()["total_cost"] == 220
    assert response.json()["items"][0]["line_cost"] == 220
    assert await db[SNAPSHOTS].find_one({"id": original["id"]}, {"_id": 0}) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"options_raw": [{"id": "name", "name": "Name", "value": "Layla"}]},
    {"options_raw": [{"id": "date", "name": "Date", "value": "2026-11-03"}]},
    {"custom_fields": [{"id": "date", "name": "Date", "value": "2026-11-03"}]},
], ids=["text-option", "date-option", "custom-date"])
async def test_legacy_empty_selection_cannot_prove_a_new_filled_option(changes):
    db = AsyncMongoMockClient().empty_legacy_selection_review
    item = _item(options_raw=[])
    await db[SNAPSHOTS].insert_one({
        "user_id": "owner", "order_number": "order", "order_item_id": "line", "salla_product_id": "shawl",
        "quantity": 4, "base_unit_cost": 15, "line_cost": 60, "cost_complete": True, "selected_options": [],
    })
    assert (await _resolve(db, item))["line_total"] == 60

    changed = await _resolve(db, {**item, **changes})

    assert changed["calculation_cost_available"] is False
    assert changed["historical_cost_verified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["manual", "lifecycle"])
async def test_two_source_aliases_for_one_frozen_line_never_use_last_row_wins(source):
    db = AsyncMongoMockClient().ambiguous_alias_review
    item = _item(order_item_id="salla:order:111", source_item_id="111", options_raw=[])
    if source == "manual":
        for key, value in [("111", 60), ("salla:order:111", 280)]:
            await db[SNAPSHOTS].insert_one({
                "user_id": "owner", "order_number": "order", "order_item_id": key,
                "salla_product_id": "shawl", "quantity": 4, "line_cost": value, "cost_complete": True,
            })
    else:
        records = {key: {"product_id": "shawl", "quantity": 4, "line_fingerprint": line_fingerprint(item),
                         "cost": _catalog(item, base=cost)}
                   for key, cost in [("111", 15), ("salla:order:111", 70)]}
        await db[LIFECYCLES].insert_one({"user_id": "owner", "order_number": "order", "reporting_cost_snapshots": records})

    result = await _resolve(db, item)

    assert result["calculation_cost_available"] is False
    assert result["historical_cost_verified"] is False


@pytest.mark.asyncio
async def test_first_refresh_of_legacy_order_does_not_certify_todays_cost_as_historical(monkeypatch):
    import fulfillment_v2_routes as fulfillment
    db = AsyncMongoMockClient().legacy_intake_cost_review

    async def fixture_transaction(database, owner, callback, **kwargs):
        return await callback(database)

    monkeypatch.setattr(fulfillment, "operational_owner", fixture_transaction)
    await db.settings.insert_one({"user_id": "owner", "g47_inventory": {
        "component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00",
    }})
    item = OrderItemDTO(order_item_id="line", product_id="shawl", name="Synthetic old shawl", quantity=4)
    old_date = datetime(2025, 1, 1, tzinfo=timezone.utc)
    order = OrderDTO(
        order_id="provider-order", order_number="order", created_at=old_date,
        source=OrderSourceDTO(source_order_id="provider-order"), status="under_review",
        payment=PaymentDTO(method="cod"), items=[item],
    )
    await db.unified_orders.insert_one({
        "user_id": "owner", "order_number": "order", "products": [item.model_dump(mode="python")],
        "raw_by_source": {"salla_direct": {"created_at": old_date.isoformat()}},
    })
    await db[PRODUCTS].insert_one({"user_id": "owner", "salla_product_id": "shawl"})
    await db[COST_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "shawl", "base_cost": 70})

    outcome = await fulfillment.reconcile_component_order_lifecycle(
        db, user_id="owner", order=order, decision={"lines": []},
        source_updated_at="2026-11-02T00:00:00+00:00",
    )

    assert outcome["operational_cohort"] == fulfillment.LEGACY_COMPONENT_COHORT
    result = await _resolve(db, item.model_dump(mode="python"))
    assert result["historical_cost_verified"] is False


@pytest.mark.asyncio
async def test_manual_first_capture_of_old_catalog_estimate_stays_unverified_on_reread():
    from order_option_cost_snapshot_routes import calculate_order_cost_snapshot
    db = AsyncMongoMockClient().unverified_manual_cost_review
    item = _item(options_raw=[], product_id="ordinary")
    await db[PRODUCTS].insert_one({"user_id": "owner", "salla_product_id": "ordinary"})
    await db[COST_PROFILES].insert_one({"user_id": "owner", "salla_product_id": "ordinary", "base_cost": 70})
    order = SimpleNamespace(
        order_number="order", order_id="provider-order", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc), items=[item],
    )

    captured = await calculate_order_cost_snapshot(db, user_id="owner", order=order)

    assert captured["total_cost"] == 280
    assert captured["items"][0]["cost"]["historical_cost_verified"] is False
    persisted = await db[SNAPSHOTS].find_one({"user_id": "owner"})
    assert persisted["cost"]["historical_cost_verified"] is False
    result = await _resolve(db, item)
    assert result["historical_cost_verified"] is False
    assert result["line_total"] == 280


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"products.0.variant_id": "different-variant"},
    {"products.0.custom_fields": [{"id": "date", "name": "Date", "value": "2026-11-03"}]},
], ids=["variant-unproven", "custom-field-unproven"])
async def test_legacy_repair_refuses_unproven_variant_or_custom_field_identity(change):
    from order_cost_reporting_repair import build_reporting_repair_plan
    from tests.test_order_cost_reporting_repair import OWNER, seed
    db = AsyncMongoMockClient().legacy_repair_identity_review
    await seed(db)
    await db[LIFECYCLES].update_one({"user_id": OWNER}, {"$unset": {
        "bundle_intake_snapshots.line-one.line_fingerprint": "",
    }})
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": change})

    plan = await build_reporting_repair_plan(db, OWNER, ["order-one"], include_base_product_ids=["shawl"])

    assert plan["ready"] is False
    assert plan["lines"][0]["after_reporting_correction"] is None
