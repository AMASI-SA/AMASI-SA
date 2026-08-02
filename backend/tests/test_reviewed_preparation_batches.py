from types import SimpleNamespace

import pytest

import reviewed_preparation_batches as batch_module
from order_review_forward_stage_guard import (
    FORWARD_FULFILLMENT_STAGES,
    install_order_review_forward_stage_guard,
)
from order_review_routes import EVENTS, REVIEW_COMPLETED_STAGES, WORKFLOWS
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS
from reviewed_preparation_batches import (
    _batch_response,
    _card_field_projection,
    _reconcile_order_stage,
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


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length=None):
        return list(self.rows[:length] if length else self.rows)


class _Collection:
    def __init__(self, *, one=None, rows=None):
        self.one = one
        self.rows = list(rows or [])
        self.last_update = None
        self.inserted = []

    async def find_one(self, *_args, **_kwargs):
        return dict(self.one) if isinstance(self.one, dict) else self.one

    def find(self, *_args, **_kwargs):
        return _Cursor(self.rows)

    async def update_one(self, _selector, update, **_kwargs):
        self.last_update = update
        if isinstance(self.one, dict):
            self.one.update(update.get("$set") or {})
            self.one["revision"] = int(self.one.get("revision") or 0) + int((update.get("$inc") or {}).get("revision") or 0)
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def insert_one(self, row):
        self.inserted.append(dict(row))
        return SimpleNamespace(inserted_id="event-1")


class _DB:
    def __init__(self, workflow, allocations):
        self.collections = {
            WORKFLOWS: _Collection(one=workflow),
            PREPARATION_UNIT_ALLOCATIONS: _Collection(rows=allocations),
            EVENTS: _Collection(),
        }

    def __getitem__(self, name):
        return self.collections[name]


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


@pytest.mark.asyncio
async def test_partial_file_keeps_order_in_reviewed(monkeypatch):
    workflow = {
        "user_id": "owner-1",
        "order_number": "100",
        "stage": "reviewed",
        "revision": 1,
        "items": [],
    }
    order = SimpleNamespace(
        order_number="100",
        items=[SimpleNamespace(order_item_id="line-a", quantity=2)],
    )
    db = _DB(workflow, [
        {"status": "committed", "order_item_id": "line-a", "unit_index": 1},
    ])

    async def context(*_args, **_kwargs):
        return {"pairs": [(order, workflow)]}

    monkeypatch.setattr(batch_module, "load_reviewed_product_context", context)
    complete, remaining = await _reconcile_order_stage(
        db,
        user_id="owner-1",
        order_number="100",
        batch_id="batch-1",
        actor={"id": "employee-1", "name": "موظف"},
    )

    assert complete is False
    assert remaining == 1
    update = db[WORKFLOWS].last_update
    assert update["$set"]["preparation_progress"]["remaining_quantity"] == 1
    assert "stage" not in update["$set"]
    assert db[EVENTS].inserted == []


@pytest.mark.asyncio
async def test_final_unit_moves_order_to_in_progress(monkeypatch):
    workflow = {
        "user_id": "owner-1",
        "order_number": "100",
        "stage": "reviewed",
        "revision": 1,
        "items": [],
    }
    order = SimpleNamespace(
        order_number="100",
        items=[SimpleNamespace(order_item_id="line-a", quantity=2)],
    )
    db = _DB(workflow, [
        {"status": "committed", "order_item_id": "line-a", "unit_index": 1},
        {"status": "committed", "order_item_id": "line-a", "unit_index": 2},
    ])

    async def context(*_args, **_kwargs):
        return {"pairs": [(order, workflow)]}

    monkeypatch.setattr(batch_module, "load_reviewed_product_context", context)
    complete, remaining = await _reconcile_order_stage(
        db,
        user_id="owner-1",
        order_number="100",
        batch_id="batch-2",
        actor={"id": "employee-1", "name": "موظف"},
    )

    assert complete is True
    assert remaining == 0
    update = db[WORKFLOWS].last_update
    assert update["$set"]["stage"] == "in_progress"
    assert update["$set"]["preparation_progress"]["remaining_quantity"] == 0
    assert db[EVENTS].inserted[0]["event_type"] == "order_moved_to_in_progress"
    assert db[EVENTS].inserted[0]["salla_updated"] is False


def test_forward_stages_freeze_review_mutations():
    previous = set(REVIEW_COMPLETED_STAGES)
    try:
        install_order_review_forward_stage_guard()
        assert FORWARD_FULFILLMENT_STAGES.issubset(REVIEW_COMPLETED_STAGES)
    finally:
        REVIEW_COMPLETED_STAGES.clear()
        REVIEW_COMPLETED_STAGES.update(previous)


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
