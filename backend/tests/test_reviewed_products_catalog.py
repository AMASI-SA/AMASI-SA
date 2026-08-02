from types import SimpleNamespace

from reviewed_product_sorting import (
    apply_reviewed_product_sorting,
    make_reviewed_product_sorting_router,
    order_selections_by_product_rank,
    reviewed_product_sort_candidates,
)
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


def _item(
    line_id,
    product_id,
    *,
    quantity=1,
    name="سلسال بالاسم",
    sku="NAME-1",
    options=None,
):
    return {
        "order_item_id": line_id,
        "product_id": product_id,
        "parent_product_id": None,
        "variant_id": None,
        "sku": sku,
        "name": name,
        "quantity": quantity,
        "image_url": "https://example.test/source.jpg",
        "options_normalized": options or {"engraving": "اسم مختلف لكل عميل"},
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


def test_reviewed_product_cards_are_ranked_by_remaining_piece_quantity():
    base = aggregate_reviewed_products(
        [(
            _order("1", [
                _item("small", "p-small", quantity=2, name="منتج صغير", sku="S"),
                _item("large", "p-large", quantity=9, name="منتج كبير", sku="L"),
            ]),
            {"items": []},
        )],
        [],
    )

    result = apply_reviewed_product_sorting(base, [])

    assert [row["product_id"] for row in result["products"]] == [
        "p-large",
        "p-small",
    ]


def test_selected_age_spec_groups_cards_by_highest_piece_demand_without_expansion():
    product = {
        "group_key": "product:dress",
        "name": "دقلة أطفال",
        "quantity": 14,
        "remaining_quantity": 14,
        "source_lines": [
            {
                "order_number": "1001",
                "order_item_id": "a",
                "quantity": 3,
                "remaining_quantity": 3,
                "options_normalized": {"العمر": "5 سنوات", "الاسم اللي تبيه": "سارة"},
            },
            {
                "order_number": "1002",
                "order_item_id": "b",
                "quantity": 5,
                "remaining_quantity": 5,
                "options_normalized": {"العمر": "6 سنوات", "الاسم اللي تبيه": "نورة"},
            },
            {
                "order_number": "1003",
                "order_item_id": "c",
                "quantity": 4,
                "remaining_quantity": 4,
                "options_normalized": {"العمر": "5 سنوات", "الاسم اللي تبيه": "ريم"},
            },
            {
                "order_number": "1004",
                "order_item_id": "d",
                "quantity": 2,
                "remaining_quantity": 2,
                "options_normalized": {"العمر": "4 سنوات", "الاسم اللي تبيه": "ليان"},
            },
        ],
    }

    candidates = reviewed_product_sort_candidates(product)
    assert [row["label"] for row in candidates] == ["العمر"]
    assert candidates[0]["values"][0] == {
        "value": "5 سنوات",
        "quantity": 7,
        "card_count": 2,
    }

    result = apply_reviewed_product_sorting(
        {"products": [product], "categories": [], "summary": {}},
        [{"group_key": "product:dress", "spec_key": "العمر"}],
    )
    lines = result["products"][0]["source_lines"]

    assert [row["options_normalized"]["العمر"] for row in lines] == [
        "5 سنوات",
        "5 سنوات",
        "6 سنوات",
        "4 سنوات",
    ]
    # One Salla line remains one card and retains the original line quantity.
    assert len(lines) == 4
    assert [row["quantity"] for row in lines] == [4, 3, 5, 2]


def test_batch_product_blocks_follow_reviewed_quantity_rank():
    products = [
        {"group_key": "product:large", "quantity": 20},
        {"group_key": "product:small", "quantity": 3},
    ]
    selections = [
        {"group_key": "product:small", "quantity": 3},
        {"group_key": "product:large", "quantity": 10},
    ]

    assert [row["group_key"] for row in order_selections_by_product_rank(products, selections)] == [
        "product:large",
        "product:small",
    ]


def test_router_registers_catalog_and_sort_preference_endpoints():
    current_user = lambda: {"id": "owner-1", "role": "owner"}
    catalog_router = make_reviewed_products_catalog_router(
        SimpleNamespace(unified_orders=object()),
        current_user,
    )
    sorting_router = make_reviewed_product_sorting_router(
        SimpleNamespace(unified_orders=object()),
        current_user,
    )
    routes = {
        (route.path, method)
        for router in (catalog_router, sorting_router)
        for route in router.routes
        for method in route.methods
    }
    assert ("/reviewed-products-v1/catalog", "GET") in routes
    assert ("/reviewed-product-sorting-v1/preference", "PUT") in routes
