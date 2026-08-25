from types import SimpleNamespace

from reviewed_products_catalog import (
    UNCATEGORIZED_ID,
    aggregate_reviewed_products,
    apply_preparation_allocations,
    make_reviewed_products_catalog_router,
)


def _order(number, items):
    return {
        "order_number": number,
        "created_at": "2026-08-02T02:00:00+00:00",
        "shipping": {"company": "iMile"},
        "items": items,
    }


def _item(line_id, product_id, *, quantity=1, name="سلسال بالاسم", sku="NAME-1"):
    return {
        "order_item_id": line_id,
        "product_id": product_id,
        "parent_product_id": None,
        "variant_id": None,
        "sku": sku,
        "name": name,
        "quantity": quantity,
        "image_url": "https://example.test/source.jpg",
        "options_normalized": {"engraving": "اسم مختلف لكل عميل"},
    }


def test_same_salla_product_across_fifty_orders_becomes_one_card():
    pairs = []
    for index in range(50):
        line_id = f"line-{index}"
        pairs.append((
            _order(str(1000 + index), [_item(line_id, "p-name")]),
            {
                "reviewed_at": f"2026-08-02T02:{index:02d}:00+00:00",
                "items": [{
                    "order_item_id": line_id,
                    "selected_image_url": "https://example.test/selected.jpg",
                    "preparation_note": f"ملاحظة {index}",
                }],
            },
        ))

    result = aggregate_reviewed_products(
        pairs,
        [{
            "salla_product_id": "p-name",
            "name": "سلسال بالاسم",
            "sku": "NAME-1",
            "main_image": "https://example.test/catalog.jpg",
            "categories": [{"id": "jewelry", "name": "المجوهرات"}],
        }],
    )

    assert result["summary"]["reviewed_order_count"] == 50
    assert result["summary"]["unique_product_count"] == 1
    assert result["summary"]["total_quantity"] == 50
    product = result["products"][0]
    assert product["name"] == "سلسال بالاسم"
    assert product["quantity"] == 50
    assert product["source_order_count"] == 50
    assert product["source_line_count"] == 50
    assert len(product["source_lines"]) == 50
    assert product["image_url"] == "https://example.test/selected.jpg"
    assert product["source_lines"][0]["shipping_company"] == "iMile"


def test_thirty_allocated_units_leave_twenty_available():
    base = aggregate_reviewed_products(
        [(
            _order("100", [_item("line-50", "p-name", quantity=50)]),
            {"reviewed_at": "2026-08-02T02:00:00+00:00", "items": []},
        )],
        [],
    )
    allocations = [
        {
            "status": "committed",
            "order_number": "100",
            "order_item_id": "line-50",
            "unit_index": index,
        }
        for index in range(1, 31)
    ]

    result = apply_preparation_allocations(base, allocations)

    assert result["summary"]["original_quantity"] == 50
    assert result["summary"]["allocated_quantity"] == 30
    assert result["summary"]["remaining_quantity"] == 20
    assert result["products"][0]["quantity"] == 20
    source = result["products"][0]["source_lines"][0]
    assert source["remaining_quantity"] == 20
    assert source["available_unit_indices"] == list(range(31, 51))


def test_fully_allocated_product_disappears_from_reviewed_catalog():
    base = aggregate_reviewed_products(
        [(_order("100", [_item("line-2", "p-name", quantity=2)]), {"items": []})],
        [],
    )
    result = apply_preparation_allocations(base, [
        {"status": "committed", "order_number": "100", "order_item_id": "line-2", "unit_index": 1},
        {"status": "committed", "order_number": "100", "order_item_id": "line-2", "unit_index": 2},
    ])

    assert result["products"] == []
    assert result["categories"] == []
    assert result["summary"]["remaining_quantity"] == 0


def test_supplier_hidden_or_internal_line_is_not_offered_for_file():
    result = aggregate_reviewed_products(
        [(
            _order("100", [
                _item("supplier", "p-1"),
                _item("internal", "p-2"),
            ]),
            {
                "items": [{
                    "order_item_id": "internal",
                    "supplier_export": False,
                    "preparation_route": "internal_preparation",
                }],
            },
        )],
        [],
    )

    assert result["summary"]["unique_product_count"] == 1
    assert result["products"][0]["product_id"] == "p-1"


def test_different_product_ids_never_merge_only_because_names_match():
    result = aggregate_reviewed_products(
        [(
            _order("1", [
                _item("a", "p-1", name="سلسال"),
                _item("b", "p-2", name="سلسال"),
            ]),
            {"items": []},
        )],
        [],
    )

    assert result["summary"]["unique_product_count"] == 2
    assert {row["product_id"] for row in result["products"]} == {"p-1", "p-2"}


def test_parent_category_is_added_for_subcategory_filtering():
    result = aggregate_reviewed_products(
        [(
            _order("1", [_item("a", "p-1", quantity=3)]),
            {"items": []},
        )],
        [{
            "salla_product_id": "p-1",
            "name": "سلسال بالاسم",
            "raw_salla": {
                "categories": [{
                    "id": "root",
                    "name": "الإكسسوارات",
                    "children": [{
                        "id": "necklaces",
                        "name": "السلاسل",
                        "parent_id": "root",
                    }],
                }],
            },
            "categories": [{"id": "necklaces", "name": "السلاسل", "parent_id": "root"}],
        }],
    )

    product = result["products"][0]
    assert set(product["category_ids"]) == {"root", "necklaces"}
    categories = {row["id"]: row for row in result["categories"]}
    assert categories["root"]["product_count"] == 1
    assert categories["necklaces"]["depth"] == 1


def test_product_without_salla_category_is_visible_under_uncategorized():
    result = aggregate_reviewed_products(
        [(_order("1", [_item("a", "p-1")]), {"items": []})],
        [],
    )
    assert result["products"][0]["category_ids"] == [UNCATEGORIZED_ID]
    assert result["categories"][0]["id"] == UNCATEGORIZED_ID


def test_router_registers_catalog_endpoint():
    router = make_reviewed_products_catalog_router(
        SimpleNamespace(unified_orders=object()),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }
    assert ("/reviewed-products-v1/catalog", "GET") in routes


def test_historical_catalog_does_not_subtract_later_allocations():
    base = aggregate_reviewed_products(
        [(_order("25", [_item("line-11", "p-11", quantity=11)]), {
            "reviewed_at": "2026-08-25T10:00:00+03:00",
            "items": [],
        })],
        [],
    )

    assert base["summary"]["total_quantity"] == 11
    assert base["products"][0]["quantity"] == 11
