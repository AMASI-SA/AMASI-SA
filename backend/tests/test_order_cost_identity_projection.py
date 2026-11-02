"""Real Salla canonical/projection seams must preserve frozen cost identity."""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

from bundle_catalog_v2 import BUNDLES
from bundle_fulfillment_v2 import capture_intake
from order_cost_authority import (
    LIFECYCLES, calculate_catalog_line_cost, capture_reporting_costs,
    line_fingerprint, load_order_cost_authority, resolve_order_line_cost,
)
from order_engine.mapper import _map_item
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS
from salla_orders_v3.compatibility import _legacy_product
from salla_orders_v3.normalizer import normalize_order_item


def _representations(*, options=None):
    raw = {
        "id": "111", "name": "Shawl", "quantity": 4,
        "product": {"id": "shawl", "name": "Shawl", "sku": "AMS13281"},
        "options": deepcopy(options or []),
    }
    canonical = _map_item(order_number="123", item=raw, index=0).model_dump(mode="python")
    projected = _legacy_product(None, normalize_order_item(raw, order_number="123", index=0))
    return canonical, projected


def _catalog(item, base=70):
    return calculate_catalog_line_cost(
        item, product={}, profile={"base_cost": base}, product_bindings=[],
        option_bindings=[], resources={},
    )


async def _product(db):
    await db[PRODUCTS].insert_one({
        "user_id": "owner", "salla_product_id": "shawl",
        "mezan_product_id": "mpv2_shawl", "name": "Shawl", "variants": [],
        "options": [{"id": "rose", "name": "Rose", "values": [
            {"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}]}],
    })
    await db[COST_PROFILES].insert_one({
        "user_id": "owner", "salla_product_id": "shawl", "base_cost": 15,
    })


@pytest.mark.asyncio
async def test_real_projected_line_reads_its_canonical_frozen_cost():
    db = AsyncMongoMockClient().cost_projection
    await _product(db)
    canonical, projected = _representations()
    assert canonical["order_item_id"] == "salla:123:111"
    assert projected["order_item_id"] == "111"
    assert projected["canonical_order_item_id"] == canonical["order_item_id"]
    captured = await capture_reporting_costs(db, "owner", [canonical], {})
    await db[LIFECYCLES].insert_one({
        "user_id": "owner", "order_number": "123", "reporting_cost_snapshots": captured,
    })
    authority = await load_order_cost_authority(
        db, "owner", [{"order_number": "123", "products": [projected]}],
    )
    result = resolve_order_line_cost(
        projected, order_number="123", product_id="shawl",
        catalog_cost=_catalog(projected, base=70), authority=authority,
    )

    assert result["line_total"] == 60
    assert result["historical_cost_verified"] is True


def test_projected_option_raw_keeps_the_same_economic_fingerprint():
    canonical, projected = _representations(options=[{
        "option_id": "rose", "value_id": "yes", "name": "Rose", "value": "Yes",
    }])
    # Align the line ID explicitly so this assertion isolates option projection.
    projected["order_item_id"] = canonical["order_item_id"]
    assert projected["options"][0]["raw"]["value_id"] == "yes"
    assert line_fingerprint(projected) == line_fingerprint(canonical)


def test_equivalent_quantity_serializations_have_the_same_fingerprint():
    canonical, _ = _representations()
    assert line_fingerprint({**canonical, "quantity": 4}) == line_fingerprint({**canonical, "quantity": 4.0})


@pytest.mark.asyncio
async def test_real_projected_bundle_matches_the_frozen_intake_and_options():
    db = AsyncMongoMockClient().bundle_cost_projection
    await _product(db)
    await db[PRODUCTS].insert_one({
        "user_id": "owner", "salla_product_id": "bouquet",
        "mezan_product_id": "mpv2_bouquet", "name": "Bouquet", "variants": [],
    })
    await db[COST_PROFILES].insert_one({
        "user_id": "owner", "salla_product_id": "bouquet", "base_cost": 40,
    })
    await db[BUNDLES].insert_one({
        "user_id": "owner", "product_id": "shawl", "product_type": "composite",
        "schema_version": 2, "revision": 1, "items": [
            {"kind": "current_product", "quantity": 1, "rule_id": "base"},
            {"kind": "product", "product_id": "bouquet", "quantity": 1, "rule_id": "rose",
             "condition": {"option_id": "rose", "value_id": "yes"}},
        ],
    })
    canonical, projected = _representations(options=[{
        "option_id": "rose", "value_id": "yes", "name": "Rose", "value": "Yes",
    }])
    captured = await capture_intake(
        db, "owner", [{**canonical, "order_line_id": canonical["order_item_id"]}], {},
    )
    assert float(captured[canonical["order_item_id"]]["estimated_cost"]) == 55
    await db[LIFECYCLES].insert_one({
        "user_id": "owner", "order_number": "123", "bundle_intake_snapshots": captured,
    })
    authority = await load_order_cost_authority(
        db, "owner", [{"order_number": "123", "products": [projected]}],
    )
    result = resolve_order_line_cost(
        projected, order_number="123", product_id="shawl",
        catalog_cost=_catalog(projected, base=70), authority=authority,
    )

    assert result["line_total"] == 220
    assert result["calculation_cost_available"] is True
