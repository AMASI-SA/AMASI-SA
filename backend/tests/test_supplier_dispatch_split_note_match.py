from supplier_dispatch_pdf import _line_match


def test_supplier_pdf_matches_exact_split_unit_before_order_item_fallback():
    rows = [
        {
            "order_item_id": "item-1",
            "unit_index": 1,
            "unit_indices": [1],
            "note": "هذا المنتج مفصول من كمية 2 — القطعة 1 من 2",
        },
        {
            "order_item_id": "item-1",
            "unit_index": 2,
            "unit_indices": [2],
            "note": "هذا المنتج مفصول من كمية 2 — القطعة 2 من 2",
        },
    ]

    matched = _line_match(
        {"order_item_id": "item-1", "unit_index": 2},
        rows,
    )

    assert matched is rows[1]
    assert matched["note"].endswith("القطعة 2 من 2")


def test_legacy_unsplit_row_still_matches_without_unit_identity():
    row = {"order_item_id": "item-legacy", "note": "ملاحظة قديمة"}
    assert _line_match({"order_item_id": "item-legacy", "unit_index": 1}, [row]) is row
