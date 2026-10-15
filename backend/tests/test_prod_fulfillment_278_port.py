from types import SimpleNamespace

import fitz
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

from preparation_pdf import ProductLine
from preparation_pdf_compact_operational_layout import (
    COLUMN_GAP,
    MEDIA_GAP,
    MEDIA_SIZE,
    ROW_GAP,
    compact_card_dimensions,
    compact_media_positions,
    compact_reference_card_rows,
    generate_compact_operational_preparation_pdf,
)
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


def test_compact_pdf_omits_product_title_and_delivery_label():
    line = ProductLine(
        order_number="275808511",
        order_date="2026-08-02",
        product_name="PRODUCT-TITLE-MUST-NOT-APPEAR",
        customer_name="Customer",
        note=None,
        quantity=1,
        total_products_in_order=4,
        shipping_company="iMile",
        size="50 inch",
        color="blue",
    )
    specifications, order_rows = compact_reference_card_rows(line)
    assert specifications
    assert order_rows[-1] == ("", "4 - iMile")

    pdf_bytes = generate_compact_operational_preparation_pdf([line])
    assert pdf_bytes.startswith(b"%PDF")
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        extracted = "\n".join(page.get_text() for page in document)
    finally:
        document.close()
    assert "PRODUCT-TITLE-MUST-NOT-APPEAR" not in extracted
    assert "iMile" in extracted


def test_compact_pdf_reserves_card_gaps_and_tightens_media_pair():
    card_width, card_height = compact_card_dimensions()
    legacy_width = (A4[0] - 14 * mm) / 3
    legacy_height = (A4[1] - 12 * mm) / 5
    assert COLUMN_GAP == 4 * mm
    assert ROW_GAP == 4.5 * mm
    assert card_width < legacy_width
    assert card_height < legacy_height

    image_x, qr_x = compact_media_positions(0, card_width)
    assert abs((qr_x - image_x - MEDIA_SIZE) - MEDIA_GAP) < 0.001
    assert MEDIA_GAP == 1.5 * mm
