from preparation_pdf_unit_card_expansion import expand_preparation_unit_cards
from reviewed_products_catalog import aggregate_reviewed_products


def test_quantity_two_expands_to_two_preparation_cards_only():
    cards = expand_preparation_unit_cards([
        {
            "order_number": "276628330",
            "order_item_id": "item-1",
            "name": "دقلة ولدي",
            "quantity": 2,
        }
    ])

    assert len(cards) == 2
    assert [row["unit_index"] for row in cards] == [1, 2]
    assert [row["quantity"] for row in cards] == [1, 1]
    assert all(row["source_line_quantity"] == 2 for row in cards)
    assert all(row["order_item_id"] == "item-1" for row in cards)


def test_reviewed_catalog_keeps_original_order_quantity_aggregated():
    order = {
        "order_number": "276628330",
        "created_at": "2026-08-20T00:00:00+00:00",
        "shipping": {"company": "iMile"},
        "items": [
            {
                "order_item_id": "item-1",
                "product_id": "product-1",
                "sku": "AMS10836",
                "name": "دقلة ولدي بشكل جديد",
                "quantity": 2,
                "options_normalized": {"المقاس": "48"},
            }
        ],
    }
    workflow = {
        "reviewed_at": "2026-08-20T00:05:00+00:00",
        "items": [{"order_item_id": "item-1", "review_status": "reviewed"}],
    }

    catalog = aggregate_reviewed_products([(order, workflow)], [])

    assert catalog["summary"]["total_quantity"] == 2
    assert len(catalog["products"]) == 1
    product = catalog["products"][0]
    assert product["quantity"] == 2
    assert product["source_lines"][0]["quantity"] == 2
    assert product["source_lines"][0]["order_item_id"] == "item-1"
