from preparation_piece_execution_guard import incomplete_assignment_orders


def test_reviewed_order_with_remaining_units_blocks_file_start():
    workflows = [{
        "order_number": "3001",
        "stage": "reviewed",
        "preparation_assignment_status": "partially_assigned",
        "preparation_progress": {
            "required_quantity": 5,
            "allocated_quantity": 3,
            "remaining_quantity": 2,
        },
    }]

    assert incomplete_assignment_orders(workflows) == ["3001"]


def test_reviewed_order_with_full_assignment_can_start():
    workflows = [{
        "order_number": "3001",
        "stage": "reviewed",
        "preparation_assignment_status": "assigned",
        "preparation_progress": {
            "required_quantity": 5,
            "allocated_quantity": 5,
            "remaining_quantity": 0,
        },
    }]

    assert incomplete_assignment_orders(workflows) == []


def test_already_in_progress_order_does_not_block_another_assigned_file():
    workflows = [{
        "order_number": "3001",
        "stage": "in_progress",
        "preparation_assignment_status": "started",
        "preparation_progress": {
            "required_quantity": 5,
            "allocated_quantity": 5,
            "remaining_quantity": 0,
        },
    }]

    assert incomplete_assignment_orders(workflows) == []
