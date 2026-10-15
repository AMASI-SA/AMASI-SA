from preparation_pdf_unit_card_expansion import (
    _planned_source_quantities,
    expand_preparation_unit_cards,
    split_quantity_card_note,
)


def test_quantity_two_cards_show_original_quantity_and_piece_position():
    cards = expand_preparation_unit_cards([
        {
            "order_number": "276628330",
            "order_item_id": "item-1",
            "quantity": 2,
            "source_line_quantity": 2,
            "unit_indices": [1, 2],
            "note": None,
        }
    ])

    assert len(cards) == 2
    assert [card["quantity"] for card in cards] == [1, 1]
    assert cards[0]["note"] == "هذا المنتج مفصول من كمية 2 — القطعة 1 من 2"
    assert cards[1]["note"] == "هذا المنتج مفصول من كمية 2 — القطعة 2 من 2"


def test_existing_customer_note_is_preserved_before_split_note():
    cards = expand_preparation_unit_cards([
        {
            "quantity": 2,
            "source_line_quantity": 2,
            "unit_indices": [1],
            "note": "تغليف هدية",
        }
    ])

    assert cards[0]["note"] == (
        "تغليف هدية | هذا المنتج مفصول من كمية 2 — القطعة 1 من 2"
    )


def test_single_quantity_product_does_not_get_split_note():
    cards = expand_preparation_unit_cards([
        {"quantity": 1, "source_line_quantity": 1, "unit_indices": [1], "note": None}
    ])

    assert cards[0]["note"] is None
    assert split_quantity_card_note(1, 1) == ""


def test_original_order_line_quantity_wins_over_partial_batch_selection():
    quantities = _planned_source_quantities([
        {
            "order_number": "3001",
            "order_item_id": "item-1",
            "quantity": 1,
            "unit_indices": [2],
            "line": {"quantity": 2},
        }
    ])

    assert quantities[("3001", "item-1")] == 2
    cards = expand_preparation_unit_cards([
        {
            "order_number": "3001",
            "order_item_id": "item-1",
            "quantity": 1,
            "source_line_quantity": quantities[("3001", "item-1")],
            "unit_indices": [2],
        }
    ])
    assert cards[0]["note"] == "هذا المنتج مفصول من كمية 2 — القطعة 2 من 2"
