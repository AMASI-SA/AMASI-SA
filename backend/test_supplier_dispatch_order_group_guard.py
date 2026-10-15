"""Supplier dispatch must honor the employee's physical-piece quantity."""

from preparation_supplier_dispatch import SupplierDispatchFileSelection
from supplier_dispatch_order_group_guard import _preview_result, _requested_plan


def _piece(piece_id: str, unit_index: int):
    return {
        "piece_id": piece_id,
        "file_number": "PF-1",
        "order_number": "ORDER-1",
        "order_item_id": "ITEM-1",
        "unit_index": unit_index,
        "group_key": "same-product",
        "product_id": "same-product",
        "product_name": "منتج واحد بثلاث قطع",
        "sku": "SKU-1",
        "services": [],
    }


def test_one_of_three_similar_pieces_stays_one_in_supplier_preview():
    candidates = [_piece("first", 1), _piece("second", 2), _piece("third", 3)]
    files = [SupplierDispatchFileSelection(
        file_number="PF-1",
        selections=[{"group_key": "piece:second", "quantity": 1}],
    )]

    planned = _requested_plan(candidates, files)
    preview = _preview_result(planned)

    assert [piece["piece_id"] for piece in planned] == ["second"]
    assert preview["requested_piece_count"] == 1
    assert preview["suggested_piece_count"] == 1
    assert preview["expansion_required"] is False
    assert preview["files"] == [{
        "file_number": "PF-1",
        "selections": [{"group_key": "piece:second", "quantity": 1}],
    }]


def test_group_quantity_two_never_expands_to_third_piece():
    candidates = [_piece("first", 1), _piece("second", 2), _piece("third", 3)]
    files = [SupplierDispatchFileSelection(
        file_number="PF-1",
        selections=[{"group_key": "same-product::service:none", "quantity": 2}],
    )]

    planned = _requested_plan(candidates, files)

    assert [piece["piece_id"] for piece in planned] == ["first", "second"]
