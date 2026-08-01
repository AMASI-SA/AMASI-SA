from types import SimpleNamespace

from reviewed_products_catalog import (
    UNCATEGORIZED_ID,
    aggregate_reviewed_products,
    make_reviewed_products_catalog_router,
)


def _order(number, items):
    return {
        "order_number": number,
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
