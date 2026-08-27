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
    source_line = product["source_lines"][0]
    assert source_line["shipping_company"] == "iMile"
    assert source_line["identity_source"] == "reviewed_ready"
    assert source_line["ready_item_id"].startswith("ready-item:")
    assert source_line["ready_item_identity"] == {
        "order_item_id": "line-0",
        "source_item_id": "",
        "product_id": "p-name",
        "parent_product_id": "",
        "variant_id": "",
        "sku": "NAME-1",
        "barcode": "",
        "quantity": 1,
        "product_name": "سلسال بالاسم",
        "selected_image_url": "https://example.test/selected.jpg",
        "options": {"engraving": "اسم مختلف لكل عميل"},
    }


def test_missing_sku_and_product_id_rejoin_unique_catalog_product_card():
    named_product = "فستان بناتي أنيق بتصميم النخيل واللون الأخضر"
    result = aggregate_reviewed_products(
        [
            (_order("with-id", [_item(
                "line-with-id", "p-13062", quantity=6,
                name=named_product, sku="AMS13062",
            )]), {"items": []}),
            (_order("legacy", [{**_item(
                "line-without-id", "", quantity=1,
                name=named_product, sku="",
            ), "options_normalized": {}}]), {
                "items": [{
                    "order_item_id": "line-without-id",
                    "specifications_snapshot": {"مقاس الطفل": "8 سنوات"},
                }],
            }),
        ],
        [{
            "salla_product_id": "p-13062",
            "name": named_product,
            "sku": "AMS13062",
        }],
    )

    assert result["summary"]["unique_product_count"] == 1
    product = result["products"][0]
    assert product["quantity"] == 7
    assert product["sku"] == "AMS13062"
    assert product["group_key"] == "product:p-13062"
    legacy = next(row for row in product["source_lines"] if row["order_number"] == "legacy")
    assert legacy["options_normalized"] == {"مقاس الطفل": "8 سنوات"}


def test_sparse_live_line_reuses_strong_identity_from_review_snapshot():
    name = "كوب زهور أحمر ووردي 200 مل مخصص بالاسم"
    result = aggregate_reviewed_products(
        [
            (_order("with-sku", [_item(
                "cup-a", "p-13032", name=name, sku="AMS13032",
            )]), {"items": []}),
            (_order("sparse", [{**_item(
                "cup-b", "", name=name, sku="",
            ), "options_normalized": {}}]), {
                "items": [{
                    "order_item_id": "cup-b",
                    "product_id": "p-13032",
                    "product_key": "product:p-13032",
                    "sku": "AMS13032",
                    "product_name": name,
                    "quantity": 1,
                }],
            }),
        ],
        [{
            "salla_product_id": "p-13032",
            "name": name,
            "sku": "AMS13032",
        }],
    )

    assert result["summary"]["unique_product_count"] == 1
    product = result["products"][0]
    assert product["group_key"] == "product:p-13032"
    assert product["sku"] == "AMS13032"
    assert product["quantity"] == 2


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


def test_incident_recovered_lines_are_allocated_before_existing_reviewed_lines():
    result = aggregate_reviewed_products(
        [
            (_order("existing", [_item("existing-line", "p-11", sku="AMS11353")]), {
                "reviewed_at": "2026-08-24T10:00:00+00:00",
                "items": [],
            }),
            (_order("recovered", [_item("recovered-line", "p-11", sku="AMS11353")]), {
                "reviewed_at": "2026-08-25T01:00:00+00:00",
                "incident_recovery_id": "recovery-1",
                "items": [],
            }),
        ],
        [],
    )

    lines = result["products"][0]["source_lines"]
    assert [line["order_number"] for line in lines] == ["recovered", "existing"]
    assert lines[0]["incident_recovery_id"] == "recovery-1"


def test_missing_live_salla_line_is_restored_from_review_snapshot():
    result = aggregate_reviewed_products(
        [(
            _order("279778158", []),
            {
                "reviewed_at": "2026-08-25T00:02:29+03:00",
                "incident_recovery_id": "recovery-11",
                "items": [{
                    "order_item_id": "lost-dress-line",
                    "product_id": "p-dress",
                    "parent_product_id": None,
                    "sku": "AMS11353",
                    "product_name": "فستان بناتي أخضر",
                    "quantity": 2,
                    "selected_image_url": "https://example.test/dress.jpg",
                    "specifications_snapshot": {"size": "8 سنوات"},
                }],
            },
        )],
        [],
    )

    assert result["summary"]["total_quantity"] == 2
    product = result["products"][0]
    assert product["sku"] == "AMS11353"
    assert product["quantity"] == 2
    assert product["source_lines"][0]["options_normalized"] == {"size": "8 سنوات"}
    assert product["source_lines"][0]["review_snapshot_identity"] == {
        "order_item_id": "lost-dress-line",
        "source_item_id": "",
        "product_id": "p-dress",
        "parent_product_id": "",
        "variant_id": "",
        "sku": "AMS11353",
        "barcode": "",
        "quantity": 2,
    }


def test_review_snapshot_does_not_duplicate_a_live_salla_line():
    result = aggregate_reviewed_products(
        [(
            _order("279756840", [_item("same-line", "p-dress", sku="AMS11353")]),
            {
                "incident_recovery_id": "recovery-11",
                "items": [{
                    "order_item_id": "same-line",
                    "product_id": "p-dress",
                    "sku": "AMS11353",
                    "product_name": "فستان بناتي أخضر",
                    "quantity": 1,
                }],
            },
        )],
        [],
    )

    assert result["summary"]["total_quantity"] == 1
    assert result["products"][0]["source_line_count"] == 1


def test_anonymous_review_snapshot_line_is_restored_without_order_item_id():
    result = aggregate_reviewed_products(
        [(
            _order("279803951", []),
            {
                "order_number": "279803951",
                "incident_recovery_id": "recovery-11",
                "items": [{
                    "product_id": "p-dress",
                    "sku": "AMS11353",
                    "product_name": "فستان بناتي أخضر",
                    "quantity": 2,
                    "supplier_export": True,
                    "specifications_snapshot": {"size": "10 سنوات"},
                }],
            },
        )],
        [],
    )

    product = result["products"][0]
    assert product["quantity"] == 2
    assert product["source_lines"][0]["order_item_id"].startswith(
        "review-snapshot:279803951:"
    )
    assert product["source_lines"][0]["options_normalized"] == {
        "size": "10 سنوات"
    }


def test_anonymous_review_snapshot_does_not_duplicate_matching_live_quantity():
    result = aggregate_reviewed_products(
        [(
            _order("279773618", [_item("live-dress", "p-dress", quantity=2, sku="AMS11353")]),
            {
                "order_number": "279773618",
                "incident_recovery_id": "recovery-11",
                "items": [{
                    "product_id": "p-dress",
                    "sku": "AMS11353",
                    "product_name": "فستان بناتي أخضر",
                    "quantity": 2,
                }],
            },
        )],
        [],
    )

    assert result["summary"]["total_quantity"] == 2
    assert result["products"][0]["source_line_count"] == 1
