from types import SimpleNamespace

import pytest

from reviewed_preparation_batches import (
    _batch_response,
    _card_field_projection,
    make_reviewed_preparation_batches_router,
    plan_preparation_allocations,
    render_preparation_batch_pdf,
)


def _product(group_key, quantity, source_lines):
    return {
        "group_key": group_key,
        "name": f"منتج {group_key}",
        "quantity": quantity,
        "remaining_quantity": quantity,
        "source_lines": source_lines,
    }


def _line(order_number, order_item_id, quantity, start=1):
    return {
        "order_number": order_number,
        "order_item_id": order_item_id,
        "quantity": quantity,
        "available_unit_indices": list(range(start, start + quantity)),
    }


def test_select_thirty_of_fifty_across_deterministic_order_lines():
    products = [
        _product("product:p-1", 50, [
            _line("100", "line-a", 20),
            _line("101", "line-b", 30),
        ]),
    ]

    result = plan_preparation_allocations(
        products,
        [{"group_key": "product:p-1", "quantity": 30}],
    )

    assert [(row["order_number"], row["quantity"]) for row in result] == [
        ("100", 20),
        ("101", 10),
    ]
    assert result[1]["unit_indices"] == list(range(1, 11))


def test_full_second_product_can_share_same_file():
    products = [
        _product("product:p-1", 50, [_line("100", "line-a", 50)]),
        _product("product:p-2", 10, [_line("200", "line-b", 10)]),
    ]

    result = plan_preparation_allocations(products, [
        {"group_key": "product:p-1", "quantity": 30},
        {"group_key": "product:p-2", "quantity": 10},
    ])

    assert sum(row["quantity"] for row in result) == 40
    assert {row["group_key"] for row in result} == {
        "product:p-1",
        "product:p-2",
    }


def test_allocation_uses_only_free_unit_indices():
    product = _product(
        "product:p-1",
        2,
        [{
            "order_number": "100",
            "order_item_id": "line-a",
            "quantity": 5,
            "allocated_unit_indices": [1, 2, 3],
            "available_unit_indices": [4, 5],
        }],
    )

    result = plan_preparation_allocations(
        [product],
        [{"group_key": "product:p-1", "quantity": 2}],
    )

    assert result[0]["unit_indices"] == [4, 5]


def test_quantity_above_remaining_is_rejected():
    with pytest.raises(ValueError, match="preparation_quantity_exceeds_remaining"):
        plan_preparation_allocations(
            [_product("product:p-1", 20, [_line("100", "line-a", 20)])],
            [{"group_key": "product:p-1", "quantity": 21}],
        )


def test_duplicate_group_is_rejected():
    with pytest.raises(ValueError, match="duplicate_product_group"):
        plan_preparation_allocations(
            [_product("product:p-1", 20, [_line("100", "line-a", 20)])],
            [
                {"group_key": "product:p-1", "quantity": 10},
                {"group_key": "product:p-1", "quantity": 5},
            ],
        )


def test_file_fields_preserve_name_color_size_and_notes():
    projected = _card_field_projection([
        {"spec_key": "name", "name": "الاسم", "value": "سارة"},
        {"spec_key": "color", "name": "اللون", "value": "ذهبي"},
        {"spec_key": "size", "name": "المقاس", "value": "18"},
        {"spec_key": "message", "name": "رسالة الإهداء", "value": "مبروك"},
        {"spec_key": "material", "name": "الخامة", "value": "ستانلس"},
    ], "انتبه للتغليف")

    assert projected["customer_name"] == "سارة"
    assert projected["color"] == "ذهبي"
    assert projected["size"] == "18"
    assert "مبروك" in projected["note"]
    assert "انتبه للتغليف" in projected["note"]
    assert projected["product_options"] == {"الخامة": "ستانلس"}


def test_batch_snapshot_can_regenerate_pdf():
    pdf = render_preparation_batch_pdf({
        "id": "batch-1",
        "title": "تجهيز المنتجات",
        "lines": [{
            "order_number": "275678403",
            "order_date": "2026-08-02",
            "product_name": "سلسال بالاسم",
            "customer_name": "سارة",
            "note": "هدية",
            "quantity": 2,
            "total_products_in_order": 3,
            "line_index": 0,
            "shipping_company": "iMile",
            "size": "18",
            "color": "ذهبي",
            "product_options": {"الخامة": "ستانلس"},
        }],
    })

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_batch_response_is_mezan_only_and_exposes_transitions():
    response = _batch_response({
        "id": "batch-1",
        "status": "ready",
        "file_name": "ملف.pdf",
        "allocated_quantity": 40,
        "selected_product_count": 2,
        "card_count": 2,
        "order_count": 2,
        "transitioned_order_numbers": ["100"],
        "remaining_review_order_numbers": ["101"],
    })

    assert response["ok"] is True
    assert response["allocated_quantity"] == 40
    assert response["transitioned_order_numbers"] == ["100"]
    assert response["salla_updated"] is False
    assert response["qoyod_updated"] is False


def test_router_registers_create_list_and_download_routes():
    router = make_reviewed_preparation_batches_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }

    assert ("/reviewed-preparation-batches-v1/batches", "POST") in routes
    assert ("/reviewed-preparation-batches-v1/batches", "GET") in routes
    assert (
        "/reviewed-preparation-batches-v1/batches/{batch_id}/pdf",
        "GET",
    ) in routes
