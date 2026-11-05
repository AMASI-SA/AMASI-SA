from assembly_board_groups import assembly_queue_group


def item(key, route="supplier_file", quantity=1):
    return {"order_item_id": key, "preparation_route": route, "quantity": quantity}


def received(key, identity):
    return {"order_item_id": key, "piece_id": identity,
            "supplier_dispatch_status": "received", "received_at": "2026-11-05"}


def test_direct_and_mixed_are_exclusive_and_do_not_require_employee_receipt():
    direct = item("a", "direct_assembly", 2)
    assert assembly_queue_group({"items": [direct]}, []) == "direct_preparation"
    mixed = {"items": [direct, item("b", quantity=2)]}
    assert assembly_queue_group(mixed, [received("b", "b1")]) is None
    assert assembly_queue_group(mixed, [received("b", "b1"), received("b", "b2")]) == "supplier_received"
    assert assembly_queue_group({"items": [item("b")]}, [received("b", "b1")]) == "supplier_received"


def test_missing_partial_duplicate_and_contradictory_receipts_do_not_complete():
    workflow = {"items": [item("b", quantity=2)]}
    for pieces in [[], [received("b", "b1")], [received("b", "b1")]*2,
                   [received("b", "b1"), {**received("b", "b2"), "supplier_dispatch_status": "partial_received"}],
                   [received("b", "b1"), {**received("b", "b2"), "received_at": None}],
                   [received("b", "b1"), received("other", "b2")]]:
        assert assembly_queue_group(workflow, pieces) is None
    assert assembly_queue_group({"items": [item("b", "direct_assembly")]}, [received("b", "b1")]) is None
    assert assembly_queue_group({"items": []}, []) is None
    assert assembly_queue_group({"items": [item("b", "internal_preparation")]}, []) is None
    assert assembly_queue_group({"items": [item("b"), item("b")]}, []) is None
