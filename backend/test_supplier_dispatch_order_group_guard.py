from supplier_dispatch_order_group_guard import (
    _ORIGINAL_PLAN,
    expand_same_order_product_closure,
)


def _piece(piece_id: str, order_number: str, *, group_key: str = "product-1", product_id: str = "p1", unit_index: int = 1):
    return {
        "piece_id": piece_id,
        "order_number": order_number,
        "order_item_id": f"item-{order_number}",
        "unit_index": unit_index,
        "group_key": group_key,
        "product_id": product_id,
        "product_name": "منتج الاختبار",
        "sku": "SKU-1",
        "services": [],
    }


def test_boundary_expands_15_to_16_to_keep_customer_order_whole():
    pieces = [
        _piece(f"single-{index:02d}", f"{index:03d}")
        for index in range(1, 15)
    ]
    pieces.extend([
        _piece("multi-a", "015", unit_index=1),
        _piece("multi-b", "015", unit_index=2),
    ])
    group_key = __import__("preparation_supplier_dispatch")._piece_dispatch_group_key(pieces[0])

    planned = _ORIGINAL_PLAN(pieces, [{"group_key": group_key, "quantity": 15}])
    expanded = expand_same_order_product_closure(pieces, planned)

    assert len(planned) == 15
    assert len(expanded) == 16
    assert [row["piece_id"] for row in expanded[-2:]] == ["multi-a", "multi-b"]


def test_exact_order_boundary_does_not_expand():
    pieces = [
        _piece("a1", "001", unit_index=1),
        _piece("a2", "001", unit_index=2),
        _piece("b1", "002", unit_index=1),
    ]
    group_key = __import__("preparation_supplier_dispatch")._piece_dispatch_group_key(pieces[0])
    planned = _ORIGINAL_PLAN(pieces, [{"group_key": group_key, "quantity": 2}])
    expanded = expand_same_order_product_closure(pieces, planned)
    assert len(expanded) == 2
    assert [row["piece_id"] for row in expanded] == ["a1", "a2"]


def test_same_order_same_product_is_closed_across_service_aware_groups():
    plain = _piece("plain", "777", group_key="product-1", unit_index=1)
    serviced = _piece("serviced", "777", group_key="product-1", unit_index=2)
    serviced["services"] = [{"service_id": "engrave", "service_name": "نقش", "status": "pending"}]

    expanded = expand_same_order_product_closure([plain, serviced], [plain])

    assert [row["piece_id"] for row in expanded] == ["plain", "serviced"]


def test_missing_order_identity_never_groups_unrelated_pieces():
    first = _piece("p1", "")
    second = _piece("p2", "")
    expanded = expand_same_order_product_closure([first, second], [first])
    assert [row["piece_id"] for row in expanded] == ["p1"]
