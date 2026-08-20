from preparation_pdf_physical_piece_overlay import expand_batch_lines_to_physical_pieces
from preparation_piece_barcode import preparation_piece_barcode


def test_expands_selected_units_to_one_row_each():
    batch = {
        "lines": [
            {
                "order_number": "1001",
                "order_item_id": "salla-item-77",
                "quantity": 3,
                "unit_indices": [1, 2, 3],
                "product_name": "منتج تجريبي",
            }
        ]
    }
    rows = expand_batch_lines_to_physical_pieces(batch)
    assert len(rows) == 3
    assert [row["unit_index"] for row in rows] == [1, 2, 3]
    assert [row["quantity"] for row in rows] == [1, 1, 1]
    assert [row["unit_indices"] for row in rows] == [[1], [2], [3]]


def test_expansion_does_not_mutate_stored_batch_snapshot():
    source = {
        "order_number": "1001",
        "order_item_id": "salla-item-77",
        "quantity": 2,
        "unit_indices": [2, 1],
    }
    batch = {"lines": [source]}
    rows = expand_batch_lines_to_physical_pieces(batch)
    assert source["quantity"] == 2
    assert source["unit_indices"] == [2, 1]
    assert [row["unit_index"] for row in rows] == [1, 2]


def test_each_physical_unit_has_a_different_barcode_but_batch_does_not_change_it():
    common = {
        "user_id": "merchant-1",
        "order_number": "1001",
        "order_item_id": "salla-item-77",
    }
    unit1_a = preparation_piece_barcode(**common, unit_index=1, batch_id="batch-a")
    unit1_b = preparation_piece_barcode(**common, unit_index=1, batch_id="batch-b")
    unit2 = preparation_piece_barcode(**common, unit_index=2, batch_id="batch-a")
    assert unit1_a == unit1_b
    assert unit1_a != unit2
