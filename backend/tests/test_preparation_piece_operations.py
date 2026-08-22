import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from preparation_piece_operations import (
    DEFAULT_ESTIMATED_DURATION_MINUTES,
    PIECES,
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_ASSEMBLY,
    WORKFLOWS,
    FileSchedulePatchRequest,
    _assembly_batch_id,
    _assembly_piece_public,
    _assembly_progress,
    _assembly_search,
    _workflow_assembly_pieces,
    _can_start_assigned_file,
    _piece_has_completed_preparation_receipt,
    _service_context_key,
    _preparation_receipt_order_number,
    _preparation_receipt_piece_public,
    _piece_upsert_update,
    build_duration_history,
    build_piece_documents,
    inherit_required_services,
    make_preparation_piece_operations_router,
    preparation_receipt_blocker,
    assembly_piece_blocker,
    validate_materialized_piece_count,
)


def test_services_are_inherited_from_product_and_matching_option_only():
    resources = {
        "cut": {
            "id": "cut",
            "name": "قص",
            "code": "CUT",
            "kind": "service",
            "unit": "piece",
            "unit_cost": 5,
        },
        "paint": {
            "id": "paint",
            "name": "طلاء",
            "code": "PAINT",
            "kind": "service",
            "unit": "piece",
            "unit_cost": 10,
        },
        "chain": {
            "id": "chain",
            "name": "سلسلة",
            "kind": "component",
            "unit": "piece",
            "unit_cost": 3,
        },
    }
    line = {
        "file_spec_fields": [
            {"name": "اللون", "value": "ذهبي"},
            {"name": "الاسم", "value": "سارة"},
        ],
    }

    services = inherit_required_services(
        line=line,
        product_links=[
            {"resource_id": "cut", "quantity": 1},
            {"resource_id": "chain", "quantity": 1},
        ],
        option_bindings=[
            {
                "mode": "resource",
                "resource_id": "paint",
                "quantity": 1,
                "option_id": "color",
                "option_name": "اللون",
                "value_id": "gold",
                "value_name": "ذهبي",
            },
            {
                "mode": "resource",
                "resource_id": "paint",
                "quantity": 1,
                "option_id": "color",
                "option_name": "اللون",
                "value_id": "silver",
                "value_name": "فضي",
            },
        ],
        resources_by_id=resources,
    )

    by_id = {row["service_id"]: row for row in services}
    assert set(by_id) == {"cut", "paint"}
    assert by_id["cut"]["source"] == "product"
    assert by_id["paint"]["source"] == "option"
    assert by_id["paint"]["condition"]["value_name"] == "ذهبي"


def test_batch_units_become_assigned_piece_records_for_file_employee():
    assigned_at = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    documents = build_piece_documents(
        user_id="owner-1",
        registry={
            "file_number": "PF-20260803-0012",
            "file_title": "دفعة الدقل",
            "responsible_employee_id": "employee-1",
            "responsible_employee_name": "محمد",
        },
        batch={
            "id": "batch-1",
            "lines": [{
                "order_number": "3001",
                "order_item_id": "item-1",
                "unit_indices": [2, 3],
                "quantity": 2,
                "group_key": "product:44",
                "product_id": "44",
                "product_name": "دقلة بالاسم",
                "sku": "DQL-44",
                "resolved_image_url": "https://cdn.salla.sa/dql-44.jpg",
                "image_candidates": ["https://cdn.salla.sa/dql-44.jpg"],
                "file_spec_fields": [
                    {"name": "اللون", "value": "ذهبي"},
                ],
            }],
        },
        services_by_product={
            "44": {
                "services": [{
                    "service_id": "engrave",
                    "service_name": "نحت",
                    "status": "pending",
                }],
            },
        },
        assigned_at=assigned_at,
        duration_by_signature={
            ("employee-1", "44", "engrave"): 90,
            ("", "", ""): DEFAULT_ESTIMATED_DURATION_MINUTES,
        },
    )

    assert len(documents) == 2
    assert {row["unit_index"] for row in documents} == {2, 3}
    assert len({row["piece_id"] for row in documents}) == 2
    assert all(row["status"] == PIECE_STATUS_ASSIGNED for row in documents)
    assert all(row["execution_status"] == "not_started" for row in documents)
    assert all(row["responsible_employee_id"] == "employee-1" for row in documents)
    assert all(row["remaining_service_count"] == 1 for row in documents)
    assert all(
        row["resolved_image_url"] == "https://cdn.salla.sa/dql-44.jpg"
        for row in documents
    )
    assert all(
        row["image_url"] == "https://cdn.salla.sa/dql-44.jpg"
        for row in documents
    )
    assert documents[0]["estimated_due_at"] == assigned_at + timedelta(minutes=90)


def test_same_product_order_lines_keep_independent_option_services():
    assigned_at = datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc)
    named_line = {
        "order_number": "276628330",
        "order_item_id": "item-named",
        "unit_indices": [1],
        "quantity": 1,
        "group_key": "product:AMS10836",
        "product_id": "AMS10836",
        "product_name": "دقله ولادي بشكل جديد",
        "file_spec_fields": [
            {"name": "هل تريد تطريز الاسم على الدقله", "value": "نعم"},
            {"name": "الإسم", "value": "10"},
        ],
    }
    plain_line = {
        "order_number": "276628330",
        "order_item_id": "item-plain",
        "unit_indices": [1],
        "quantity": 1,
        "group_key": "product:AMS10836",
        "product_id": "AMS10836",
        "product_name": "دقله ولادي بشكل جديد",
        "file_spec_fields": [
            {"name": "هل تريد تطريز الاسم على الدقله", "value": "لا"},
        ],
    }
    embroidery = {
        "service_id": "embroider-name",
        "service_name": "تطريز الاسم",
        "status": "pending",
    }

    documents = build_piece_documents(
        user_id="owner-1",
        registry={
            "file_number": "PF-20260816-0018",
            "file_title": "اختبار فصل الخدمات",
            "responsible_employee_id": "employee-turki",
            "responsible_employee_name": "تركي صادق",
        },
        batch={"id": "batch-service-split", "lines": [named_line, plain_line]},
        services_by_product={
            _service_context_key(named_line): {"services": [embroidery]},
            _service_context_key(plain_line): {"services": []},
        },
        assigned_at=assigned_at,
    )

    assert len(documents) == 2
    by_item = {row["order_item_id"]: row for row in documents}
    assert [row["service_name"] for row in by_item["item-named"]["services"]] == [
        "تطريز الاسم",
    ]
    assert by_item["item-named"]["service_plan_status"] == "pending"
    assert by_item["item-plain"]["services"] == []
    assert by_item["item-plain"]["service_plan_status"] == "no_external_services"


def test_piece_upsert_never_reuses_a_path_across_mongodb_operators():
    updated_at = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    update = _piece_upsert_update(
        {
            "id": "piece-1",
            "user_id": "owner-1",
            "batch_id": "batch-1",
            "file_number": "PF-20260804-0005",
            "file_title": "تجهيز المنتجات",
            "responsible_employee_id": "employee-1",
            "responsible_employee_name": "محمد",
            "selected_image_url": None,
            "resolved_image_url": "https://cdn.salla.sa/product.jpg",
            "image_url": "https://cdn.salla.sa/product.jpg",
            "status": PIECE_STATUS_ASSIGNED,
            "created_at": updated_at - timedelta(minutes=5),
            "updated_at": updated_at - timedelta(minutes=5),
        },
        updated_at=updated_at,
    )

    insert_paths = set(update["$setOnInsert"])
    mutable_paths = set(update["$set"])
    assert insert_paths.isdisjoint(mutable_paths)
    assert update["$setOnInsert"]["id"] == "piece-1"
    assert update["$set"]["file_number"] == "PF-20260804-0005"
    assert update["$set"]["responsible_employee_id"] == "employee-1"
    assert update["$set"]["resolved_image_url"] == "https://cdn.salla.sa/product.jpg"
    assert update["$set"]["updated_at"] == updated_at


def test_ready_file_cannot_materialize_with_zero_or_partial_piece_records():
    with pytest.raises(HTTPException) as missing:
        validate_materialized_piece_count(
            batch={"allocated_quantity": 1},
            registry={"allocated_quantity": 1},
            pieces=[],
        )
    assert missing.value.detail == {
        "code": "preparation_piece_count_mismatch",
        "expected_piece_count": 1,
        "actual_piece_count": 0,
    }

    assert validate_materialized_piece_count(
        batch={"allocated_quantity": 2},
        registry={"allocated_quantity": 2},
        pieces=[{"piece_id": "a"}, {"piece_id": "b"}],
    ) == 2


def test_previous_duration_uses_employee_product_median_then_fallback():
    start = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    rows = [
        {
            "responsible_employee_id": "employee-1",
            "product_id": "44",
            "services": [{"service_id": "cut"}],
            "started_at": start,
            "completed_at": start + timedelta(minutes=60),
        },
        {
            "responsible_employee_id": "employee-1",
            "product_id": "44",
            "services": [{"service_id": "cut"}],
            "started_at": start,
            "completed_at": start + timedelta(minutes=120),
        },
        {
            "responsible_employee_id": "employee-2",
            "product_id": "44",
            "services": [{"service_id": "cut"}],
            "started_at": start,
            "completed_at": start + timedelta(minutes=180),
        },
    ]

    history = build_duration_history(rows)

    assert history[("employee-1", "44", "cut")] == 90
    assert history[("", "44", "cut")] == 120
    assert history[("", "", "")] == 120


def test_only_assigned_employee_or_manager_can_start_file():
    registry = {"responsible_employee_id": "employee-1"}
    assigned = {
        "id": "employee-1",
        "role": "viewer",
        "created_by": "owner-1",
        "extra_permissions": ["preparation.manage"],
    }
    unrelated = {
        "id": "employee-2",
        "role": "viewer",
        "created_by": "owner-1",
        "extra_permissions": ["preparation.manage"],
    }
    owner = {"id": "owner-1", "role": "owner"}

    assert _can_start_assigned_file(assigned, registry) is True
    assert _can_start_assigned_file(unrelated, registry) is False
    assert _can_start_assigned_file(owner, registry) is True


def test_schedule_contract_supports_automatic_and_required_modes():
    automatic = FileSchedulePatchRequest(mode="automatic")
    required = FileSchedulePatchRequest(
        mode="required",
        required_due_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
    )
    assert automatic.required_due_at is None
    assert required.mode == "required"


def test_router_registers_work_receiving_manager_start_and_schedule_routes():
    router = make_preparation_piece_operations_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }

    assert ("/preparation-work-v1/my-work", "GET") in routes
    assert ("/preparation-work-v1/receiving/search", "GET") in routes
    assert (
        "/preparation-work-v1/receiving/pieces/{piece_id}/receive",
        "POST",
    ) in routes
    assert ("/preparation-work-v1/assembly/search", "GET") in routes
    assert (
        "/preparation-work-v1/assembly/pieces/{piece_id}/ready",
        "POST",
    ) in routes
    assert ("/preparation-work-v1/manager/summary", "GET") in routes
    assert ("/preparation-work-v1/files/{file_number}/start", "POST") in routes
    assert ("/preparation-work-v1/files/{file_number}/schedule", "PUT") in routes


def test_my_work_discovers_reassigned_pieces_before_registry_employee_filter():
    source = inspect.getsource(__import__("preparation_piece_operations")._my_work_view)

    assert '"responsible_employee_id": employee_id' in source
    assert '"batch_id": {"$in": batch_ids}' in source
    assert source.index('"responsible_employee_id": employee_id') < source.index(
        '"batch_id": {"$in": batch_ids}'
    )
    assert "PIECE_STATUS_READY_FOR_ASSEMBLY" in source
    assert '"preparation_receipt_status": {"$ne": "received"}' in source
    assert '"branch_handoff_at": now' in inspect.getsource(
        __import__("preparation_piece_operations")._receive_preparation_piece
    )


def test_preparation_receipt_card_merges_customer_specs_and_marks_search_match():
    card = _preparation_receipt_piece_public(
        {
            "piece_id": "piece-1",
            "order_number": "10452",
            "product_name": "ميدالية باسم",
            "responsible_employee_id": "employee-1",
            "responsible_employee_name": "عرفات",
            "status": PIECE_STATUS_IN_PROGRESS,
            "specifications_snapshot": [
                {"name": "الاسم", "value": "سارة"},
                {"name": "اللون", "value": "ذهبي"},
            ],
            "product_options_snapshot": {
                "اللون": "ذهبي",
                "المقاس": "وسط",
            },
        },
        matched_piece_id="piece-1",
    )

    assert card["search_match"] is True
    assert card["can_receive"] is True
    assert card["responsible_employee_name"] == "عرفات"
    assert card["specifications"] == [
        {"name": "الاسم", "value": "سارة"},
        {"name": "اللون", "value": "ذهبي"},
        {"name": "المقاس", "value": "وسط"},
    ]


def test_preparation_receipt_blocks_supplier_piece_until_supplier_receipt_finishes():
    base = {
        "piece_id": "piece-1",
        "status": PIECE_STATUS_IN_PROGRESS,
        "responsible_employee_id": "employee-1",
    }
    assert preparation_receipt_blocker({
        **base,
        "supplier_dispatch_status": "sent",
    }) == "preparation_piece_supplier_receipt_required"
    assert preparation_receipt_blocker({
        **base,
        "supplier_dispatch_status": "partial_received",
    }) == "preparation_piece_supplier_receipt_required"
    assert preparation_receipt_blocker({
        **base,
        "supplier_dispatch_status": "received",
        "status": "received",
    }) is None


def test_preparation_receipt_blocks_every_unfinished_required_service():
    piece = {
        "piece_id": "piece-1",
        "status": PIECE_STATUS_IN_PROGRESS,
        "responsible_employee_id": "employee-1",
        "services": [
            {
                "service_id": "engrave",
                "service_name": "حفر الاسم",
                "status": "pending",
                "required_quantity": 1,
                "completed_quantity": 0,
            }
        ],
    }

    assert preparation_receipt_blocker(piece) == (
        "preparation_piece_services_incomplete"
    )
    card = _preparation_receipt_piece_public(piece)
    assert card["can_receive"] is False
    assert card["remaining_service_count"] == 1
    assert card["pending_service_names"] == ["حفر الاسم"]

    piece["services"][0]["completed_quantity"] = 1
    assert preparation_receipt_blocker(piece) is None


def test_preparation_receipt_is_final_and_order_search_accepts_arabic_prefix():
    assert _piece_has_completed_preparation_receipt({
        "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
    }) is True
    assert _preparation_receipt_order_number("طلب #10452") == "10452"


def test_assembly_product_card_keeps_full_information_and_search_priority():
    card = _assembly_piece_public(
        {
            "piece_id": "piece-1",
            "order_number": "10452",
            "unit_index": 2,
            "product_name": "سلسال بالاسم",
            "sku": "AMS-22",
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "responsible_employee_name": "عرفات",
            "specifications_snapshot": [
                {"name": "الاسم", "value": "سارة"},
                {"name": "اللون", "value": "ذهبي"},
            ],
            "services": [
                {"service_name": "كتابة الاسم", "status": "completed"},
            ],
        },
        matched_piece_id="piece-1",
    )

    assert card["search_match"] is True
    assert card["can_mark_ready"] is True
    assert card["assembly_ready"] is False
    assert card["product_name"] == "سلسال بالاسم"
    assert card["specifications"] == [
        {"name": "الاسم", "value": "سارة"},
        {"name": "اللون", "value": "ذهبي"},
    ]
    assert card["services"] == [
        {"name": "كتابة الاسم", "status": "completed"},
    ]


def test_operational_and_direct_stock_items_exist_only_in_assembly():
    rows = _workflow_assembly_pieces(
        {
            "operational_items": [{
                "operational_item_id": "op:gift-card",
                "name": "كرت إهداء",
                "source_order_item_id": "item-1",
                "source_product_name": "هدية",
                "linked_specs": [{"name": "الاسم", "value": "سارة"}],
                "preparation_status": "pending",
                "supplier_export": False,
            }],
            "items": [{
                "order_item_id": "item-2",
                "preparation_route": "direct_assembly",
                "product_id": "product-2",
                "product_name": "منتج مخزون",
                "quantity": 2,
                "direct_assembly_piece_ids": ["direct-a", "direct-b"],
                "assembly_ready_piece_ids": ["direct-a"],
            }],
        },
        order_number="10452",
    )

    assert len(rows) == 3
    operational = next(row for row in rows if row["virtual_kind"] == "operational")
    direct = [row for row in rows if row["virtual_kind"] == "direct_assembly"]
    assert operational["supplier_export"] is False
    assert operational["inventory_item"] is False
    assert operational["assembly_status"] == "pending"
    assert [row["assembly_status"] for row in direct] == ["ready", "pending"]
    assert all(row["supplier_export"] is False for row in direct)
    assert all(row["inventory_item"] is True for row in direct)


def test_virtual_assembly_cards_are_counted_before_printing():
    source = inspect.getsource(_assembly_progress)

    assert "_workflow_assembly_pieces" in source
    assert "pieces.extend" in source
    assert "total_count = len(pieces)" in source


def test_assembly_ready_is_idempotent_and_batch_id_is_stable():
    assert assembly_piece_blocker({
        "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
    }) is None
    assert assembly_piece_blocker({
        "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "assembly_status": "ready",
    }) == "assembly_piece_already_ready"
    assert _assembly_batch_id("merchant-1", "10452") == _assembly_batch_id(
        "merchant-1", "10452"
    )
    assert _assembly_batch_id("merchant-1", "10452").startswith(
        "ship_assembly_"
    )


def test_last_assembly_piece_moves_order_to_completed_and_creates_print_batch():
    source = inspect.getsource(_assembly_progress)

    assert '"stage": "completed"' in source
    assert '"shipping_print_batch_id": batch_id' in source
    assert "SHIPPING_BATCHES" in source
    assert '"source": "assembly_completion"' in source


class _AssemblySearchCursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, _limit):
        return list(self.rows)


class _AssemblySearchCollection:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.find_one_queries = []

    async def find_one(self, query, *_args, **_kwargs):
        self.find_one_queries.append(query)
        return self.row

    def find(self, *_args, **_kwargs):
        return _AssemblySearchCursor(self.rows)


@pytest.mark.asyncio
async def test_assembly_search_keeps_completed_order_as_read_only_history():
    workflows = _AssemblySearchCollection(row={
        "order_number": "276628330",
        "stage": "delivering",
        "assembly_status": "completed",
        "carrier_label_ready": True,
        "carrier_label_type": "store_courier",
        "carrier_label_print_confirmed": True,
        "carrier_label_print_confirmed_at": "2026-08-21T18:00:00+00:00",
        "carrier_label_print_confirmed_by_name": "موظف العنونة",
        "store_courier_assignment_state": "assigned_waiting_pickup",
        "store_courier_assignee_name": "مندوب الرياض",
        "carrier_label_print_data": {
            "order_number": "276628330",
            "qr_code": "data:image/svg+xml;base64,QR",
        },
    })
    pieces = _AssemblySearchCollection(rows=[{
        "piece_id": "piece-1",
        "order_number": "276628330",
        "unit_index": 1,
        "product_name": "منتج تجريبي",
        "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "assembly_status": "ready",
    }])
    db = {
        WORKFLOWS: workflows,
        PIECES: pieces,
    }

    result = await _assembly_search(
        db,
        user_id="merchant-1",
        query="276628330",
    )

    assert result["history_only"] is True
    assert result["pieces"][0]["product_name"] == "منتج تجريبي"
    assert result["pieces"][0]["can_mark_ready"] is False
    assert result["carrier_label"]["shipment_state"] == (
        "assigned_waiting_pickup"
    )
    assert result["carrier_label"]["store_courier_assignee_name"] == (
        "مندوب الرياض"
    )
    workflow_query = workflows.find_one_queries[0]
    history_query = workflow_query["$or"][1]
    assert history_query["assembly_status"] == "completed"
    assert history_query["stage"]["$in"] == [
        "completed",
        "delivering",
        "delivered",
    ]
    assert "carrier_label_print_confirmed" not in history_query
