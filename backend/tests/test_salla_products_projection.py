"""Current-cost reports and Order Engine must use the same accepted Salla lines."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

import orders_db
from salla_integration.sync import _salla_order_to_doc


def source(items):
    return {
        "id": "synthetic-native-order", "reference_id": "synthetic-order",
        "date": {"date": "2026-11-13 12:00:00", "timezone": "Asia/Riyadh"},
        "updated_at": "2026-11-13T09:05:00Z",
        "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
        "items": items,
    }


def item(identity, quantity=1, *, choice="yes"):
    return {
        "id": identity, "product_id": "synthetic-product", "sku": "SYNTHETIC-SKU",
        "name": "Synthetic product", "quantity": quantity,
        "amounts": {"price_without_tax": {"amount": 149, "currency": "SAR"},
                    "total": {"amount": 160.92 * quantity, "currency": "SAR"}},
        "options": [{"product_option_id": "embroidery", "name": "Embroidery",
                     "value": {"id": choice, "name": choice}}],
    }


async def persist(db, raw):
    return await orders_db._upsert_order(
        db, "synthetic-owner", "synthetic-order", _salla_order_to_doc(raw),
        "salla_direct", raw, None, allow_billing_eligibility=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_make", [False, True])
async def test_accepted_three_to_one_snapshot_removes_ghost_report_lines(monkeypatch, legacy_make):
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(orders_db, "_ensure_order_products_catalogued", AsyncMock())
    initial = source([item("line-a"), item("line-b"), item("line-c")])
    await persist(db, initial)
    if legacy_make:
        await db.unified_orders.update_one({}, {"$set": {
            "last_make_update_at": "2026-11-12T00:00:00Z",
            "field_sources.products": "make", "customer_city": "Preserve legacy scalar",
        }})
    newer = source([item("line-b")])
    newer["updated_at"] = "2026-11-13T09:06:00Z"
    await persist(db, newer)
    stored = await db.unified_orders.find_one({"user_id": "synthetic-owner"})
    assert [row["order_item_id"] for row in stored["products"]] == ["line-b"]
    assert [row["id"] for row in stored["raw_by_source"]["salla_direct"]["items"]] == ["line-b"]
    assert sum(row["quantity"] for row in stored["products"]) == 1
    assert stored["products"][0]["options"] == _salla_order_to_doc(newer)["products"][0]["options"]
    assert stored["raw_by_source"]["salla_direct"]["items"][0]["options"] == newer["items"][0]["options"]
    if legacy_make:
        assert stored["customer_city"] == "Preserve legacy scalar"


@pytest.mark.asyncio
async def test_quantity_reduction_preserves_only_same_native_lines_choices(monkeypatch):
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(orders_db, "_ensure_order_products_catalogued", AsyncMock())
    initial = source([item("line-a", 3), item("line-b", choice="no")])
    await persist(db, initial)
    newer = source([item("line-a", 1), item("line-b", choice="no")])
    newer["items"][0]["options"] = []
    await persist(db, newer)
    stored = await db.unified_orders.find_one({})
    assert [row["quantity"] for row in stored["products"]] == [1, 1]
    assert stored["products"][0]["options"] == _salla_order_to_doc(initial)["products"][0]["options"]
    assert stored["products"][1]["options"] == _salla_order_to_doc(initial)["products"][1]["options"]


@pytest.mark.asyncio
async def test_sparse_status_snapshot_cannot_erase_current_lines(monkeypatch):
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(orders_db, "_ensure_order_products_catalogued", AsyncMock())
    initial = source([item("line-a"), item("line-b", choice="no")])
    await persist(db, initial)
    before = await db.unified_orders.find_one({})
    sparse = deepcopy(initial)
    sparse.pop("items")
    await persist(db, sparse)
    after = await db.unified_orders.find_one({})
    assert after["products"] == before["products"]
    assert after["raw_by_source"]["salla_direct"]["items"] == before["raw_by_source"]["salla_direct"]["items"]


@pytest.mark.asyncio
async def test_same_sku_with_distinct_line_ids_and_choices_remains_distinct(monkeypatch):
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(orders_db, "_ensure_order_products_catalogued", AsyncMock())
    raw = source([item("line-a"), item("line-b", choice="no")])
    await persist(db, raw)
    await persist(db, raw)
    stored = await db.unified_orders.find_one({})
    assert [row["order_item_id"] for row in stored["products"]] == ["line-a", "line-b"]
    assert sum(row["quantity"] for row in stored["products"]) == 2
    assert [row["options"][0]["value"] for row in stored["products"]] == ["yes", "no"]
    assert [row["options"][0]["value"]["id"] for row in stored["raw_by_source"]["salla_direct"]["items"]] == ["yes", "no"]


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_only_a_proven_complete_empty_list_can_remove_the_last_line(monkeypatch, complete):
    from salla_integration.order_items_snapshot import CompleteOrderItems
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(orders_db, "_ensure_order_products_catalogued", AsyncMock())
    initial = source([item("line-a")])
    await persist(db, initial)
    empty = source(CompleteOrderItems([], order_id=initial["id"]) if complete else [])
    await persist(db, empty)
    stored = await db.unified_orders.find_one({})
    assert len(stored["products"]) == (0 if complete else 1)
    assert len(stored["raw_by_source"]["salla_direct"]["items"]) == (0 if complete else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["foreign_order", "mutated_items", "foreign_reference"])
async def test_invalid_complete_proof_cannot_modify_a_saved_order(monkeypatch, invalid):
    from salla_integration.order_items_snapshot import CompleteOrderItems
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(orders_db, "_ensure_order_products_catalogued", AsyncMock())
    initial = source([item("line-a")])
    await persist(db, initial)
    before = await db.unified_orders.find_one({})
    new = source(CompleteOrderItems([item("line-a")], order_id=initial["id"]))
    if invalid == "foreign_order":
        new["id"] = "another-order"
    elif invalid == "mutated_items":
        new["items"][0]["quantity"] = 2
    else:
        new["reference_id"] = "another-reference"
    with pytest.raises(ValueError):
        await persist(db, new)
    assert await db.unified_orders.find_one({}) == before


@pytest.mark.asyncio
async def test_report_removes_two_ghost_pieces_and_resolves_embroidery_cost(monkeypatch):
    from sold_products_report_v2 import aggregate_sold_products
    from report_option_recovery import attach_report_option_evidence
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(orders_db, "_ensure_order_products_catalogued", AsyncMock())
    initial = source([item("removed-a"), item("kept"), item("removed-b")])
    await persist(db, initial)
    # Reproduce the legacy projection: two removed lines have no selection,
    # while ten other sold pieces and the retained line have a proven choice.
    ghost_products = _salla_order_to_doc(initial)["products"]
    for row in ghost_products:
        row["options"] = []
    await db.unified_orders.update_one({}, {"$set": {
        "products": ghost_products, "raw_by_source.salla_direct.items": [item("kept")],
    }})
    other = orders_db._current_salla_products(source([item("other", 10)]), "synthetic-order")
    product = {"salla_product_id": "synthetic-product", "sku": "SYNTHETIC-SKU", "options": [
        {"id": "embroidery", "name": "Embroidery", "type": "radio",
         "values": [{"id": "yes", "name": "yes"}, {"id": "no", "name": "no"}]}]}
    profile = {"salla_product_id": "synthetic-product", "base_cost": 60}
    context = {
        "product_binding_map": {"synthetic-product": [{"id": "bag-link", "resource_id": "bag"}]},
        "option_map": {"synthetic-product": [{"id": "embroidery-link", "option_id": "embroidery",
            "value_id": "yes", "mode": "direct", "direct_amount": 10, "option_name": "Embroidery"}]},
        "resources": {"bag": {"name": "Bag", "unit_cost": 5.5}},
    }

    async def report():
        order = await db.unified_orders.find_one({})
        attach_report_option_evidence([order], [order])
        return aggregate_sold_products([order, {"products": other}], [product], [profile],
                                       catalog_context=context)[0]

    before = await report()
    assert before["units_sold"] == 13
    assert before["cost_status"] == "incomplete"
    assert before["known_cost_total"] == 961.5
    await persist(db, source([item("kept")]))
    after = await report()
    assert after["units_sold"] == 11
    assert after["total_cost"] == 830.5
    assert after["known_cost_total"] == 830.5
    assert after["unit_cost"] == 75.5


@pytest.mark.asyncio
async def test_empty_light_event_keeps_legacy_root_without_previous_raw_items(monkeypatch):
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(orders_db, "_ensure_order_products_catalogued", AsyncMock())
    legacy = _salla_order_to_doc(source([item("legacy")]))["products"]
    await db.unified_orders.insert_one({"user_id": "synthetic-owner", "order_number": "synthetic-order",
        "products": legacy, "last_make_update_at": "2026-11-12T00:00:00Z", "field_sources": {"products": "make"}})
    await persist(db, source([]))
    assert (await db.unified_orders.find_one({}))["products"] == legacy


def test_projection_keeps_historical_cost_fingerprint_for_unchanged_line():
    from order_cost_authority import calculate_catalog_line_cost, line_fingerprint, resolve_order_line_cost
    raw = source([item("kept")])
    previous = _salla_order_to_doc(raw)["products"][0]
    projected = orders_db._current_salla_products(raw, "synthetic-order")[0]
    cost = calculate_catalog_line_cost(previous, product={}, profile={"base_cost": 75.5},
        product_bindings=[], option_bindings=[], resources={})
    cost["historical_cost_verified"] = True
    authority = {"lines": {("synthetic-order", "kept"): {
        "quantity": 1, "line_fingerprint": line_fingerprint(previous), "cost": cost}}}
    assert line_fingerprint(projected) == line_fingerprint(previous)
    result = resolve_order_line_cost(projected, order_number="synthetic-order", catalog_cost={}, authority=authority)
    assert result["line_total"] == 75.5
    assert result["historical_cost_verified"] is True
