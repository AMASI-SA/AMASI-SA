from types import SimpleNamespace

from preparation_pdf_unit_card_expansion import expand_preparation_unit_cards
from reviewed_product_sorting import (
    apply_reviewed_product_sorting,
    make_reviewed_product_sorting_router,
    order_selections_by_product_rank,
    reviewed_product_sort_candidates,
)


def test_selected_piece_quantities_expand_to_one_pdf_card_per_piece():
    rows = [
        {"order_number": "1", "quantity": 4, "unit_indices": [1, 2, 3, 4]},
        {"order_number": "2", "quantity": 1, "unit_indices": [1]},
        {"order_number": "3", "quantity": 5, "unit_indices": [1, 2, 3, 4, 5]},
        {"order_number": "4", "quantity": 1, "unit_indices": [1]},
    ]
    cards = expand_preparation_unit_cards(rows)
    assert len(cards) == 11
    assert [card["line_number"] for card in cards] == list(range(1, 12))
    assert {card["quantity"] for card in cards} == {1}
    assert [card["order_number"] for card in cards].count("1") == 4
    assert [card["order_number"] for card in cards].count("3") == 5
    assert all(len(card["unit_indices"]) == 1 for card in cards)


def test_reviewed_products_rank_by_remaining_quantity_and_saved_spec():
    large = {
        "group_key": "product:large",
        "name": "منتج كبير",
        "quantity": 9,
        "remaining_quantity": 9,
        "source_lines": [
            {
                "order_number": "1",
                "quantity": 4,
                "remaining_quantity": 4,
                "options_normalized": {"العمر": "5 سنوات"},
            },
            {
                "order_number": "2",
                "quantity": 5,
                "remaining_quantity": 5,
                "options_normalized": {"العمر": "6 سنوات"},
            },
        ],
    }
    small = {
        "group_key": "product:small",
        "name": "منتج صغير",
        "quantity": 2,
        "remaining_quantity": 2,
        "source_lines": [],
    }

    candidates = reviewed_product_sort_candidates(large)
    assert [row["label"] for row in candidates] == ["العمر"]
    result = apply_reviewed_product_sorting(
        {"products": [small, large], "categories": [], "summary": {}},
        [{"group_key": "product:large", "spec_key": "العمر"}],
    )
    assert [row["group_key"] for row in result["products"]] == [
        "product:large",
        "product:small",
    ]
    assert result["products"][0]["preparation_sort_spec"] == "العمر"
    assert [
        row["options_normalized"]["العمر"]
        for row in result["products"][0]["source_lines"]
    ] == ["6 سنوات", "5 سنوات"]


def test_batch_selections_follow_reviewed_product_rank():
    products = [
        {"group_key": "product:large", "quantity": 20},
        {"group_key": "product:small", "quantity": 3},
    ]
    selections = [
        {"group_key": "product:small", "quantity": 3},
        {"group_key": "product:large", "quantity": 10},
    ]
    assert [
        row["group_key"]
        for row in order_selections_by_product_rank(products, selections)
    ] == ["product:large", "product:small"]


def test_sort_preference_router_is_registered():
    router = make_reviewed_product_sorting_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }
    assert ("/reviewed-product-sorting-v1/preference", "PUT") in routes
