import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fulfillment_v2_routes import effective_operation_actor

from preparation_piece_operations import (
    DEFAULT_ESTIMATED_DURATION_MINUTES,
    PIECES,
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_ASSEMBLY,
    WORKFLOWS,
    FileSchedulePatchRequest,
    _assembly_batch_id,
    _assembly_order_board,
    _assembly_piece_public,
    _assembly_piece_route,
    _assembly_source_specs_by_item,
    _merge_assembly_piece_customer_specs,
    _assembly_progress,
    _assembly_search,
    _workflow_assembly_pieces,
    _can_start_assigned_file,
    _piece_has_completed_preparation_receipt,
    _service_context_key,
    _preparation_receipt_order_number,
    _preparation_receipt_piece_public,
    _preparation_receiving_custody_groups,
    _piece_upsert_update,
    build_duration_history,
    build_piece_documents,
    inherit_required_services,
    make_preparation_piece_operations_router,
    preparation_receipt_blocker,
    assembly_piece_blocker,
    provable_piece_actor_attribution_repair,
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
    assert ("/preparation-work-v1/receiving/custody", "GET") in routes
    assert (
        "/preparation-work-v1/receiving/pieces/{piece_id}/receive",
        "POST",
    ) in routes
    assert ("/preparation-work-v1/assembly/search", "GET") in routes
    assert ("/preparation-work-v1/assembly/orders", "GET") in routes
    assert (
        "/preparation-work-v1/assembly/pieces/{piece_id}/ready",
        "POST",
    ) in routes
    assert ("/preparation-work-v1/manager/summary", "GET") in routes
    assert ("/preparation-work-v1/files/{file_number}/start", "POST") in routes
    assert ("/preparation-work-v1/files/{file_number}/schedule", "PUT") in routes



def test_assembly_board_uses_live_salla_status_and_mezan_evidence():
    source = inspect.getsource(_assembly_order_board)

    assert 'if _text(order.status).casefold() != state' in source
    assert '"preparation_batch_ids.0": {"$exists": True}' in source
    assert '"preparation_assignments.0": {"$exists": True}' in source
    assert '"preparation_piece_count": {"$gt": 0}' in source
    assert '"ready_to_ship_source": "preparation_receipt"' in source
    assert '_workflow_assembly_pieces(' in source
    assert 'if not pieces:' in source
    assert 'rows.sort(' in source
    assert 'row["order_created_at"]' in source
    assert 'normalized_query' in source
    assert '"source": "mezan_preparation_current_salla_status"' in source




def test_assembly_search_reopens_work_when_current_salla_status_returns_in_progress():
    module = __import__("preparation_piece_operations")
    search_source = inspect.getsource(module._assembly_search)
    physical_source = inspect.getsource(module._mark_assembly_piece_ready_in_transaction)
    virtual_source = inspect.getsource(module._mark_virtual_assembly_piece_ready)

    assert 'current_order_status == "in_progress"' in search_source
    assert 'and current_order_status != "in_progress"' in physical_source
    assert '"in_progress", "ready_to_ship", "completed"' in virtual_source
    assert '"order_created_at": order.created_at if order else None' in search_source
    assert '"shipping_company": (' in search_source



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
        __import__("preparation_piece_operations")._receive_preparation_piece_in_transaction
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


def test_preparation_receiving_custody_groups_by_source_employee_and_date_range():
    oldest = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
    newest = datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)
    rows = _preparation_receiving_custody_groups([
        {
            "piece_id": "p-new",
            "order_number": "200",
            "product_name": "منتج 2",
            "responsible_employee_id": "prep-1",
            "responsible_employee_name": "شهاب",
            "preparation_received_from_employee_id": "prep-1",
            "preparation_received_from_employee_name": "شهاب",
            "preparation_received_at": newest,
            "preparation_receipt_status": "received",
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assembly_status": "pending",
        },
        {
            "piece_id": "p-old",
            "order_number": "100",
            "product_name": "منتج 1",
            "responsible_employee_id": "prep-1",
            "responsible_employee_name": "شهاب",
            "preparation_received_at": oldest,
            "preparation_receipt_status": "received",
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assembly_status": "pending",
        },
        {
            "piece_id": "p-self",
            "order_number": "300",
            "product_name": "منتج 3",
            "responsible_employee_id": "receiver-1",
            "responsible_employee_name": "عرفات",
            "preparation_received_from_employee_id": "receiver-1",
            "preparation_received_from_employee_name": "عرفات",
            "preparation_received_at": datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc),
            "preparation_receipt_status": "received",
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assembly_status": "pending",
        },
    ])

    assert [row["source_employee_name"] for row in rows] == ["شهاب", "عرفات"]
    shihab = rows[0]
    assert shihab["piece_count"] == 2
    assert shihab["oldest_received_at"] == oldest
    assert shihab["newest_received_at"] == newest
    assert [piece["piece_id"] for piece in shihab["pieces"]] == ["p-new", "p-old"]


def test_receipt_persists_source_employee_for_custody_after_handoff():
    source = inspect.getsource(
        __import__("preparation_piece_operations")._receive_preparation_piece_in_transaction
    )
    assert '"preparation_received_from_employee_id"' in source
    assert '"preparation_received_from_employee_name"' in source
    assert '_text(piece.get("responsible_employee_id"))' in source
    assert '_text(piece.get("responsible_employee_name"))' in source


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


def test_assembly_physical_product_keeps_complete_original_customer_options():
    order = {
        "items": [{
            "order_item_id": "item-1",
            "options": [
                {"name": "الاسم", "value": "غادة"},
                {"name": "هل تريد إضافة كرت إهداء", "value": "نعم"},
            ],
            "custom_fields": [
                {"name": "الكتابة على الكرت", "value": "اختي ونور عيني كل عام وأنت بخير"},
            ],
        }],
    }
    by_item = _assembly_source_specs_by_item(order)
    assert by_item["item-1"] == [
        {"name": "الاسم", "value": "غادة"},
        {"name": "هل تريد إضافة كرت إهداء", "value": "نعم"},
        {"name": "الكتابة على الكرت", "value": "اختي ونور عيني كل عام وأنت بخير"},
    ]

    piece = _merge_assembly_piece_customer_specs(
        {
            "order_item_id": "item-1",
            "item_type": "physical_product",
            # Preparation export intentionally omitted gift-card text after it
            # was linked to an operational item.
            "specifications_snapshot": [
                {"name": "الاسم", "value": "غادة"},
                {"name": "هل تريد إضافة كرت إهداء", "value": "نعم"},
            ],
        },
        by_item["item-1"],
    )
    card = _assembly_piece_public(piece)
    assert card["specifications"] == [
        {"name": "الاسم", "value": "غادة"},
        {"name": "هل تريد إضافة كرت إهداء", "value": "نعم"},
        {"name": "الكتابة على الكرت", "value": "اختي ونور عيني كل عام وأنت بخير"},
    ]


def test_operational_card_keeps_linked_specs_without_physical_merge_override():
    piece = {
        "item_type": "internal_operational",
        "specifications_snapshot": [
            {"name": "الكتابة على الكرت", "value": "النص التشغيلي"},
        ],
    }
    merged = _merge_assembly_piece_customer_specs(
        piece,
        [{"name": "الاسم", "value": "غادة"}],
    )
    assert merged is piece
    assert _assembly_piece_public(merged)["specifications"] == [
        {"name": "الكتابة على الكرت", "value": "النص التشغيلي"},
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
    assert workflows.find_one_queries[0] == {
        "user_id": "merchant-1", "order_number": "276628330",
    }


@pytest.mark.asyncio
async def test_received_piece_can_enter_assembly_before_other_order_pieces():
    piece_id = "0123456789abcdef0123456789abcdef"
    received = {
        "piece_id": piece_id,
        "order_number": "10452",
        "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "preparation_receipt_status": "received",
    }
    waiting = {
        "piece_id": "a" * 32,
        "order_number": "10452",
        "status": PIECE_STATUS_IN_PROGRESS,
    }
    workflows = _AssemblySearchCollection(row={
        "order_number": "10452",
        "stage": "in_progress",
        "preparation_receipt_status": "partial",
    })
    pieces = _AssemblySearchCollection(row=received, rows=[received, waiting])

    result = await _assembly_search(
        {WORKFLOWS: workflows, PIECES: pieces},
        user_id="merchant-1",
        query=piece_id.upper(),
    )

    assert result["pieces"][0]["piece_id"] == piece_id
    assert result["pieces"][0]["can_mark_ready"] is True
    assert result["pieces"][1]["can_mark_ready"] is False
    assert result["summary"]["all_ready"] is False
    assert result["stage"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("scan_piece", [False, True])
async def test_assembly_search_shows_unreceived_pieces_with_frozen_actions(scan_piece):
    piece_id = "0123456789abcdef0123456789abcdef"
    waiting = {
        "piece_id": piece_id,
        "order_number": "288407431",
        "status": PIECE_STATUS_IN_PROGRESS,
        "supplier_dispatch_status": "sent",
        "supplier_name": "مورد الرياض",
        "sent_to_supplier_at": "2026-09-24T10:00:00Z",
        "responsible_employee_name": "محمد",
    }
    workflows = _AssemblySearchCollection(row={
        "order_number": "288407431", "stage": "in_progress",
    })
    pieces = _AssemblySearchCollection(row=waiting, rows=[waiting])

    result = await _assembly_search(
        {WORKFLOWS: workflows, PIECES: pieces},
        user_id="merchant-1",
        query=piece_id.upper() if scan_piece else "288407431",
    )

    assert result["summary"] == {
        "total": 1, "ready": 0, "remaining": 1, "all_ready": False,
    }
    card = result["pieces"][0]
    assert card["search_match"] is scan_piece
    assert card["can_mark_ready"] is False
    assert card["assembly_blocker_code"] == "assembly_piece_supplier_receipt_required"
    assert card["current_stage_label"] == "تم إسناد المنتج إلى المورد"
    assert [step["label"] for step in card["route_steps"]] == [
        "جاهز من التجميع والعنونة",
        "تم الاستلام من موظف التجهيز",
        "تم الاستلام من المورد",
        "تم إسناد المنتج إلى المورد",
        "تم إسناد المنتج لموظف التجهيز",
    ]
    assert [step["state"] for step in card["route_steps"][:3]] == [
        "pending", "pending", "pending",
    ]
    assert card["route_steps"][-1]["actor_name"] == "محمد"


def test_assembly_route_advances_only_after_recorded_supplier_and_preparation_receipts():
    piece = {
        "piece_id": "piece-1", "status": PIECE_STATUS_IN_PROGRESS,
        "supplier_dispatch_status": "received", "supplier_name": "مورد الرياض",
        "received_at": "2026-09-24T10:00:00Z",
        "responsible_employee_name": "محمد",
    }
    with_employee = _assembly_piece_public(piece)
    assert with_employee["current_stage_label"] == "تم الاستلام من المورد"
    assert [step["label"] for step in with_employee["route_steps"]] == [
        "جاهز من التجميع والعنونة",
        "تم الاستلام من موظف التجهيز",
        "تم الاستلام من المورد",
        "تم إسناد المنتج إلى المورد",
        "تم إسناد المنتج لموظف التجهيز",
    ]
    assert with_employee["route_steps"][0]["state"] == "pending"
    assert with_employee["route_steps"][1]["state"] == "pending"
    assert with_employee["assembly_blocker_code"] == (
        "assembly_piece_preparation_receipt_required"
    )

    received = _assembly_piece_public({
        **piece, "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "preparation_received_at": "2026-09-24T11:00:00Z",
        "preparation_received_by_name": "فاطمة",
    })
    assert received["can_mark_ready"] is True
    assert received["current_stage_label"] == "تم الاستلام من موظف التجهيز"
    assert received["route_steps"][0]["label"] == "جاهز من التجميع والعنونة"
    assert received["route_steps"][0]["state"] == "pending"
    assert received["route_steps"][1]["label"] == "تم الاستلام من موظف التجهيز"
    assert received["route_steps"][1]["actor_name"] == "فاطمة"
    assert received["route_steps"][-1]["actor_name"] == "محمد"

    inconsistent = _assembly_piece_public({
        **piece, "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "supplier_dispatch_status": "sent",
    })
    assert inconsistent["can_mark_ready"] is False
    assert inconsistent["assembly_blocker_code"] == (
        "assembly_piece_supplier_receipt_required"
    )


@pytest.mark.asyncio
async def test_partial_order_can_mark_received_piece_ready(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import preparation_piece_operations as operations

    piece_id = "0123456789abcdef0123456789abcdef"
    piece = {
        "piece_id": piece_id,
        "order_number": "10452",
        "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "preparation_receipt_status": "received",
    }
    db = {
        PIECES: MagicMock(),
        WORKFLOWS: MagicMock(),
        operations.PIECE_EVENTS: MagicMock(),
    }
    collection = db[PIECES]
    collection.find_one = AsyncMock(side_effect=[piece, {**piece, "assembly_status": "ready"}])
    collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
    db[WORKFLOWS].find_one = AsyncMock(return_value={
        "stage": "in_progress", "preparation_receipt_status": "partial",
    })
    db[operations.PIECE_EVENTS].insert_one = AsyncMock()
    monkeypatch.setattr(operations, "enforce_stage_instructions", AsyncMock())
    # This small fixture exercises the body; real Mongo verification below
    # covers the public wrapper, its owner transaction and component effects.
    consume_components = AsyncMock()
    monkeypatch.setattr(operations, "_consume_piece_components", consume_components)
    monkeypatch.setattr(operations, "_assembly_progress", AsyncMock(return_value={
        "ready_count": 1,
        "total_count": 3,
        "order_completed": False,
        "stage": "in_progress",
        "print_batch_id": None,
    }))

    result = await operations._mark_assembly_piece_ready_in_transaction(
        db,
        user_id="merchant-1",
        piece_id=piece_id,
        client_request_id="client-request-1",
        actor_id="assembly-worker",
        actor_name="موظف التجميع",
    )

    assert result["idempotent"] is False
    assert result["piece"]["assembly_ready"] is True
    assert result["progress"]["order_completed"] is False
    assert db[WORKFLOWS].find_one.call_args.args[0]["$or"][1] == {
        "stage": "in_progress",
    }
    collection.update_one.assert_awaited_once()
    consume_components.assert_awaited_once_with(
        db, user_id="merchant-1", piece=piece, actor_id="assembly-worker",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("second_assembly_status,ready_count", [
    ("pending", 1), ("ready", 2),
])
async def test_assembly_stays_open_without_shipment_while_preparation_is_partial(
    second_assembly_status, ready_count,
):
    from unittest.mock import AsyncMock, MagicMock
    from preparation_piece_operations import SHIPPING_BATCHES

    workflow = {"stage": "in_progress", "preparation_receipt_status": "partial"}
    pieces = _AssemblySearchCollection(rows=[
        {"piece_id": "first", "assembly_status": "ready"},
        {"piece_id": "second", "assembly_status": second_assembly_status},
    ])
    workflow_collection = _AssemblySearchCollection(row=workflow)
    workflow_collection.update_one = AsyncMock()
    db = {
        WORKFLOWS: workflow_collection,
        PIECES: pieces,
        SHIPPING_BATCHES: MagicMock(),
    }

    progress = await _assembly_progress(
        db,
        user_id="merchant-1",
        order_number="10452",
        actor_id="assembly-worker",
        actor_name="موظف التجميع",
        now=datetime.now(timezone.utc),
    )

    assert progress["ready_count"] == ready_count
    assert progress["total_count"] == 2
    assert progress["order_completed"] is False
    assert progress["stage"] == "in_progress"
    assert progress["print_batch_id"] is None
    assert db[SHIPPING_BATCHES].update_one.call_count == 0
    assert workflow_collection.update_one.call_args.args[0]["$or"][1] == {
        "stage": "in_progress",
    }


@pytest.mark.asyncio
async def test_all_assembly_pieces_enable_shipment_after_full_preparation_receipt():
    from unittest.mock import AsyncMock, MagicMock
    from preparation_piece_operations import SHIPPING_BATCHES

    workflows = _AssemblySearchCollection(row={"stage": "ready_to_ship"})
    workflows.update_one = AsyncMock()
    pieces = _AssemblySearchCollection(rows=[
        {"piece_id": "first", "assembly_status": "ready"},
        {"piece_id": "second", "assembly_status": "ready"},
    ])
    shipping_batches = MagicMock()
    shipping_batches.update_one = AsyncMock()

    progress = await _assembly_progress(
        {WORKFLOWS: workflows, PIECES: pieces, SHIPPING_BATCHES: shipping_batches},
        user_id="merchant-1",
        order_number="10452",
        actor_id="assembly-worker",
        actor_name="موظف التجميع",
        now=datetime.now(timezone.utc),
    )

    assert progress["order_completed"] is True
    assert progress["stage"] == "completed"
    assert progress["print_batch_id"] == _assembly_batch_id("merchant-1", "10452")
    shipping_batches.update_one.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize("already_ready,fail_consumption", [
    (False, False), (True, False), (False, True),
])
async def test_live_status_and_components_share_assembly_owner_transaction(
    monkeypatch, already_ready, fail_consumption,
):
    from unittest.mock import AsyncMock, MagicMock
    import preparation_piece_operations as operations
    # This fixture characterizes the disabled path and intentionally supplies
    # a sentinel outer DB; owner marker I/O is covered by lifecycle tests.
    import fulfillment_lifecycle
    monkeypatch.setattr(fulfillment_lifecycle, "guarded_owner", AsyncMock(return_value=False))

    piece = {
        "piece_id": "a" * 32, "order_number": "10452", "order_item_id": "line-1",
        "unit_index": 1, "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "assembly_status": "ready" if already_ready else "pending",
    }
    scoped = {name: MagicMock() for name in (PIECES, WORKFLOWS, operations.PIECE_EVENTS)}
    scoped[PIECES].find_one = AsyncMock(return_value=piece)
    scoped[PIECES].update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
    scoped[WORKFLOWS].find_one = AsyncMock(return_value={
        "stage": "completed", "assembly_status": "pending",
    })
    scoped[operations.PIECE_EVENTS].insert_one = AsyncMock()
    seen = []

    async def current_order(db, **kwargs):
        assert db is scoped
        seen.append("live_status")
        return SimpleNamespace(status="in_progress")

    async def consume(db, **kwargs):
        assert db is scoped
        scoped[PIECES].update_one.assert_not_awaited()
        seen.append("consume")
        if fail_consumption:
            raise HTTPException(409, detail={"code": "synthetic_component_stockout"})

    current = AsyncMock(side_effect=current_order)
    consume_mock = AsyncMock(side_effect=consume)
    ready_check = AsyncMock()
    progress = AsyncMock(return_value={"order_completed": False})
    monkeypatch.setattr(operations, "_current_assembly_order", current)
    monkeypatch.setattr(operations, "_consume_piece_components", consume_mock)
    monkeypatch.setattr(operations, "_assert_ready_piece_components", ready_check)
    monkeypatch.setattr(operations, "_assembly_progress", progress)
    monkeypatch.setattr(operations, "enforce_stage_instructions", AsyncMock())
    import preparation_transition_fence
    guard = AsyncMock()
    monkeypatch.setattr(preparation_transition_fence, "assert_transition_current", guard)
    outer_db = object()

    async def transact(db, owner, callback):
        assert db is outer_db and owner == "merchant-1"
        return await callback(scoped)

    transaction = AsyncMock(side_effect=transact)
    monkeypatch.setattr(operations, "operational_owner", transaction)
    args = dict(user_id="merchant-1", piece_id=piece["piece_id"],
                client_request_id="stable-request", actor_id="actor", actor_name="Synthetic")
    if fail_consumption:
        with pytest.raises(HTTPException) as failure:
            await operations._mark_assembly_piece_ready(outer_db, **args)
        assert failure.value.detail["code"] == "synthetic_component_stockout"
        scoped[PIECES].update_one.assert_not_awaited()
        scoped[operations.PIECE_EVENTS].insert_one.assert_not_awaited()
        progress.assert_not_awaited()
    else:
        result = await operations._mark_assembly_piece_ready(outer_db, **args)
        assert result["idempotent"] is already_ready
        if already_ready:
            consume_mock.assert_not_awaited()
            ready_check.assert_awaited_once_with(scoped, user_id="merchant-1", piece=piece)
            scoped[PIECES].update_one.assert_not_awaited()
        else:
            consume_mock.assert_awaited_once()
            scoped[PIECES].update_one.assert_awaited_once()
            scoped[operations.PIECE_EVENTS].insert_one.assert_awaited_once()
        assert progress.await_args.args[0] is scoped
    assert seen == (["live_status"] if already_ready else ["live_status", "consume"])
    transaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_operational_virtual_components_never_access_inventory():
    import preparation_piece_operations as operations

    # Any collection access would fail: operational annotations have no demand.
    db = object()
    piece = {"virtual_kind": "operational"}
    await operations._consume_piece_components(db, user_id="owner", piece=piece, actor_id="owner")
    await operations._assert_ready_piece_components(db, user_id="owner", piece=piece)


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [
    ("v2_active", "accounting_legacy_writer_disabled"),
    ("transition_blocked", "accounting_transition_blocked"),
])
async def test_supplier_receiving_transition_rejects_before_legacy_ledger(state, expected):
    from unittest.mock import AsyncMock, MagicMock
    import supplier_receiving_routes as receiving

    owner_row = {
        "_id": "owner", "ledger_backend_state": state, "ledger_backend_revision": 2,
        "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "synthetic-reviewed",
    }
    db = MagicMock()
    db.__getitem__.return_value.find_one = AsyncMock(return_value=owner_row)
    session = object()
    with pytest.raises(HTTPException) as failure:
        await receiving._post_supplier_invoice_ledger(
            db, user_id="owner", actor={"id": "owner"},
            invoice={"total_halalas": 125}, mongo_session=session,
        )
    assert failure.value.detail["code"] == expected
    db.__getitem__.return_value.find_one.assert_awaited_once_with({"_id": "owner"}, session=session)
    db.general_ledger.aggregate.assert_not_called()
    db.general_ledger.insert_many.assert_not_called()

# Explicit execution only: CI's ordinary pytest collection remains unchanged.
async def run_overlap_mongo_verification():
    """Verify the authorized overlap against disposable loopback Mongo only."""
    import os
    import sys
    from datetime import datetime
    from pathlib import Path
    from urllib.parse import urlsplit
    from unittest.mock import AsyncMock, patch
    import preparation_piece_operations as operations
    import supplier_receiving_routes as supplier

    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    parsed = urlsplit(uri)
    if (parsed.scheme != "mongodb" or parsed.hostname != "127.0.0.1"
            or not parsed.port or parsed.username or parsed.password
            or parsed.path not in {"", "/"}):
        raise RuntimeError("Explicit unauthenticated loopback MZ2_TEST_MONGO_URI required")
    sys.path.insert(0, str(Path(operations.__file__).resolve().parent / "tests"))
    from test_g47_component_lifecycle_integration import (
        ComponentRouteTests, WHEN, fulfillment, PLANS, UNITS,
    )

    async def live_order(case):
        await case.db.unified_orders.insert_one({
            "user_id": "owner", "order_number": "order-1", "order_date": WHEN,
            "order_status": "in_progress",
            "raw_by_source": {"salla_direct": case.source_payload(
                number="order-1", status="in_progress")},
        })
        current = await operations._current_assembly_order(
            case.db, user_id="owner", order_number="order-1")
        case.assertEqual(current.status, "in_progress")

    async def setup_physical(case):
        response, _ = await case.accept()
        case.assertEqual(response.status_code, 200, response.text)
        case.assertEqual(await case.db[PLANS].count_documents({}), 1)
        await case.seed_physical()
        await live_order(case)
        # The current Salla status must reopen this formerly completed workflow.
        await case.db[fulfillment.WORKFLOWS].update_one(
            {"user_id": "owner", "order_number": "order-1"}, {"$set": {
                "stage": "completed", "assembly_status": "pending",
                "preparation_receipt_status": "partial"}})

    async def physical_live_once(case):
        await setup_physical(case)
        response = await case.mark_piece("piece-1")
        case.assertEqual(response.status_code, 200, response.text)
        case.assertFalse(response.json()["idempotent"])
        case.assertEqual(await case.on_hand(), 18)
        response = await case.mark_piece("piece-1")
        case.assertEqual(response.status_code, 200, response.text)
        case.assertTrue(response.json()["idempotent"])
        case.assertEqual(await case.on_hand(), 18)
        case.assertEqual(await case.db[UNITS].count_documents({"state": "consumed"}), 1)
        case.assertEqual(await case.db[UNITS].count_documents({"state": "reserved"}), 1)
        case.assertEqual(await case.db[operations.PIECE_EVENTS].count_documents({
            "piece_id": "piece-1", "event_type": "assembly_piece_marked_ready"}), 1)

    async def abort_mutation(case, collection, validator):
        await setup_physical(case)
        workflow_before = await case.db[fulfillment.WORKFLOWS].find_one({"order_number": "order-1"})
        await case.db.command({"collMod": collection, "validator": validator, "validationLevel": "strict"})
        response = await case.mark_piece("piece-1")
        case.assertGreaterEqual(response.status_code, 400, response.text)
        case.assertEqual(await case.on_hand(), 20)
        case.assertEqual(await case.db[UNITS].count_documents({"state": "consumed"}), 0)
        case.assertEqual(await case.db[UNITS].count_documents({"state": "reserved"}), 2)
        case.assertEqual((await case.db[operations.PIECES].find_one({"piece_id": "piece-1"}))["assembly_status"], "pending")
        case.assertEqual(await case.db[fulfillment.WORKFLOWS].find_one({"order_number": "order-1"}), workflow_before)
        case.assertEqual(await case.db[operations.PIECE_EVENTS].count_documents({
            "event_type": "assembly_piece_marked_ready"}), 0)
        await case.db.command({"collMod": collection, "validator": {}})
        response = await case.mark_piece("piece-1")
        case.assertEqual(response.status_code, 200, response.text)
        case.assertEqual(await case.on_hand(), 18)

    async def consume_failure(case):
        await abort_mutation(case, UNITS, {"state": {"$ne": "consumed"}})

    async def ready_failure(case):
        await abort_mutation(case, operations.PIECES, {"assembly_status": {"$ne": "ready"}})

    async def virtual_once_and_operational_zero(case):
        response, _ = await case.accept()
        case.assertEqual(response.status_code, 200, response.text)
        await live_order(case)
        direct_id = operations._direct_assembly_piece_id("order-1", "line-1", 1)
        await case.db[fulfillment.WORKFLOWS].update_one(
            {"user_id": "owner", "order_number": "order-1"}, {"$set": {
                "stage": "in_progress", "preparation_receipt_status": "partial",
                "items": [{"order_item_id": "line-1", "preparation_route": "direct_assembly",
                    "product_id": "p", "quantity": 2,
                    "direct_assembly_piece_ids": [direct_id, operations._direct_assembly_piece_id("order-1", "line-1", 2)]}],
                "operational_items": [{"operational_item_id": "operational-overlap",
                    "name": "Synthetic note", "assembly_status": "pending",
                    "source_order_item_id": "line-1", "blocks_order_completion": True}]}})
        before = await case.db[UNITS].find({}).sort("_id", 1).to_list(10)
        for attempt in range(2):
            response = await case.mark_piece("operational-overlap")
            case.assertEqual(response.status_code, 200, response.text)
            case.assertEqual(response.json()["idempotent"], bool(attempt))
            case.assertEqual(await case.on_hand(), 20)
            case.assertEqual(await case.db[UNITS].find({}).sort("_id", 1).to_list(10), before)
        for attempt in range(2):
            response = await case.mark_piece(direct_id)
            case.assertEqual(response.status_code, 200, response.text)
            case.assertEqual(response.json()["idempotent"], bool(attempt))
            case.assertEqual(await case.on_hand(), 18)
            case.assertEqual(await case.db[UNITS].count_documents({"state": "consumed"}), 1)
        case.assertEqual(await case.db[operations.PIECE_EVENTS].count_documents({
            "piece_id": direct_id, "event_type": "direct_assembly_product_marked_ready"}), 1)
        case.assertEqual(await case.db[operations.PIECE_EVENTS].count_documents({
            "piece_id": "operational-overlap", "event_type": "operational_assembly_item_marked_ready"}), 1)

    async def supplier_v2_real_close_rollback(case):
        # Keep the real close/finalize, cost application and ledger writer;
        # override authentication only, as in the shared ASGI fixture.
        context = {**case.context, "permissions": {
            supplier.RECEIVE_PERMISSION, supplier.EDIT_PRODUCT_PRICE_PERMISSION}}
        replacement = patch.object(supplier, "_actor_context", AsyncMock(return_value=context))
        replacement.start()
        case.patches.append(replacement)

        async def actor():
            return case.actor

        case.app.include_router(supplier.make_supplier_receiving_router(case.db, actor))
        await case.db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {
            "ledger_backend_state": "v2_active", "ledger_backend_revision": 2,
            "ledger_backend_contract_revision": 1,
            "ledger_backend_activation_ref": "SYNTHETIC-OVERLAP"}})
        await case.db[supplier.COST_PROFILES].insert_one({
            "id": "profile", "user_id": "owner", "salla_product_id": "p", "base_cost": 5})
        await case.db[supplier.SESSIONS].insert_one({
            "id": "supplier-overlap", "user_id": "owner", "opened_by": "owner",
            "status": "open", "reference": "SR-SYNTHETIC-OVERLAP",
            "supplier_id": "supplier",
            "supplier_snapshot": {"id": "supplier", "company_name": "Synthetic supplier", "service_links": []},
            "scan_count": 1})
        await case.db[supplier.PIECES].insert_one({
            "id": "supplier-piece", "piece_id": "supplier-piece", "user_id": "owner",
            "product_id": "p", "status": supplier.PIECE_STATUS_IN_PROGRESS,
            "supplier_receiving_session_id": "supplier-overlap", "services": []})
        await case.db[supplier.RECEIVING_EVENTS].insert_one({
            "id": "supplier-scan", "user_id": "owner", "session_id": "supplier-overlap",
            "event_type": "supplier_piece_scanned", "piece_id": "supplier-piece",
            "product_id": "p", "product_name": "Synthetic product", "sku": "SYN-P",
            "services": [], "occurred_at": datetime.fromisoformat(WHEN)})
        observed = []
        original = supplier._post_supplier_invoice_ledger

        async def observe_actual_guard(db, **kwargs):
            session = kwargs["mongo_session"]
            profile = await db[supplier.COST_PROFILES].find_one({"id": "profile"}, session=session)
            observed.append((session.in_transaction, profile["base_cost"]))
            return await original(db, **kwargs)

        with patch.object(supplier, "_post_supplier_invoice_ledger", observe_actual_guard):
            response = await case.client.post("/supplier-receiving-v1/sessions/supplier-overlap/close", json={
                "confirmed_total_halalas": 600, "expected_supplier_id": "supplier",
                "invoice_lines": [{"piece_ids": ["supplier-piece"],
                    "product_unit_price_halalas": 600, "services": []}]})
        case.assertEqual(response.status_code, 423, response.text)
        case.assertEqual(response.json()["detail"]["code"], "accounting_legacy_writer_disabled")
        case.assertEqual(observed, [(True, 6)])
        case.assertEqual((await case.db[supplier.COST_PROFILES].find_one({"id": "profile"}))["base_cost"], 5)
        case.assertEqual((await case.db[supplier.SESSIONS].find_one({"id": "supplier-overlap"}))["status"], "open")
        case.assertEqual((await case.db[supplier.PIECES].find_one({"piece_id": "supplier-piece"}))["status"], supplier.PIECE_STATUS_IN_PROGRESS)
        for name in (supplier.SUPPLIER_INVOICES, "general_ledger", "accounting_audit_log",
                     "liabilities", "accounting_journal_groups_v2", "accounting_general_ledger_v2"):
            case.assertEqual(await case.db[name].count_documents({}), 0, name)
        case.assertEqual(await case.db[supplier.RECEIVING_EVENTS].count_documents({
            "event_type": "supplier_receiving_session_closed"}), 0)

    checks = (physical_live_once, consume_failure, ready_failure,
              virtual_once_and_operational_zero, supplier_v2_real_close_rollback)
    for check in checks:
        case = ComponentRouteTests(methodName="runTest")
        await case.asyncSetUp()
        try:
            await check(case)
            print("OVERLAP_MONGO_PASS " + check.__name__)
        finally:
            await case.asyncTearDown()
    print("OVERLAP_MONGO_COMPLETE: 5 executed, zero skips")


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_overlap_mongo_verification())


def test_build37_native_operation_actor_uses_real_employee_not_rewritten_owner():
    user = {
        "id": "owner-1",
        "name": "عرفات",
        "email": "owner@example.com",
        "role": "owner",
        "_session_client": "amasi_mobile",
        "_mobile_owner_id": "owner-1",
        "_mobile_actor_id": "employee-mohammed",
        "_mobile_actor_name": "محمد فؤاد",
        "_mobile_actor_email": "mohammed@example.com",
    }
    actor = effective_operation_actor(
        user,
        {"actor_id": "owner-1", "merchant_id": "owner-1"},
    )
    assert actor == {
        "id": "employee-mohammed",
        "name": "محمد فؤاد",
        "email": "mohammed@example.com",
    }


def test_build37_genuine_owner_operation_actor_stays_owner():
    user = {
        "id": "owner-1",
        "name": "عرفات",
        "email": "owner@example.com",
        "role": "owner",
        "_session_client": "amasi_mobile",
    }
    actor = effective_operation_actor(
        user,
        {"actor_id": "owner-1", "merchant_id": "owner-1"},
    )
    assert actor["id"] == "owner-1"
    assert actor["name"] == "عرفات"


def test_build37_preparation_and_assembly_routes_use_effective_operation_actor():
    source = inspect.getsource(make_preparation_piece_operations_router)
    receive_block = source.split(
        '@router.post("/receiving/pieces/{piece_id}/receive")', 1
    )[1].split('@router.get("/assembly/search")', 1)[0]
    ready_block = source.split(
        '@router.post("/assembly/pieces/{piece_id}/ready")', 1
    )[1].split('@router.get("/manager/summary")', 1)[0]
    for block in (receive_block, ready_block):
        assert "effective_operation_actor(user, context)" in block
        assert 'actor_id=operation_actor["id"]' in block
        assert 'actor_name=operation_actor["name"]' in block
        assert 'user.get("name") or user.get("email")' not in block


def test_build37_piece_route_starts_from_bottom_assignment_and_freezes_future_steps():
    piece = {
        "piece_id": "piece-1",
        "responsible_employee_name": "خالد",
        "assigned_at": datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc),
        "supplier_dispatch_status": "sent",
        "supplier_name": "مشتريات كاش",
        "supplier_id": "supplier-1",
        "sent_to_supplier_at": datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
        "status": PIECE_STATUS_IN_PROGRESS,
        "assembly_status": "pending",
    }
    current, steps = _assembly_piece_route(piece)
    assert current == "تم إسناد المنتج إلى المورد"
    assert [row["label"] for row in steps] == [
        "جاهز من التجميع والعنونة",
        "تم الاستلام من موظف التجهيز",
        "تم الاستلام من المورد",
        "تم إسناد المنتج إلى المورد",
        "تم إسناد المنتج لموظف التجهيز",
    ]
    assert [row["state"] for row in steps] == [
        "pending",
        "pending",
        "pending",
        "completed",
        "completed",
    ]
    assert steps[-1]["actor_name"] == "خالد"
    assert steps[-2]["actor_name"] == "مشتريات كاش"


def test_build37_piece_route_completed_actor_names_are_factual_and_upward():
    piece = {
        "piece_id": "piece-2",
        "responsible_employee_name": "خالد",
        "assigned_at": datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc),
        "supplier_dispatch_status": "received",
        "supplier_name": "مشتريات كاش",
        "supplier_id": "supplier-1",
        "sent_to_supplier_at": datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
        "received_at": datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc),
        "preparation_received_at": datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc),
        "preparation_received_by_name": "محمد فؤاد",
        "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
        "assembly_status": "ready",
        "assembly_ready_at": datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        "assembly_ready_by_name": "عبدالباري",
    }
    current, steps = _assembly_piece_route(piece)
    assert current == "جاهز من التجميع والعنونة"
    assert [row["actor_name"] for row in steps] == [
        "عبدالباري",
        "محمد فؤاد",
        "مشتريات كاش",
        "مشتريات كاش",
        "خالد",
    ]
    assert all(row["state"] == "completed" for row in steps)


def test_build37_historical_actor_repair_requires_exact_non_owner_event_proof():
    received_at = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)
    ready_at = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    piece = {
        "piece_id": "piece-history-1",
        "preparation_receipt_client_request_id": "receive-request-1",
        "preparation_received_at": received_at,
        "preparation_received_by": "owner-1",
        "preparation_received_by_name": "عرفات",
        "assembly_client_request_id": "ready-request-1",
        "assembly_ready_at": ready_at,
        "assembly_ready_by": "owner-1",
        "assembly_ready_by_name": "عرفات",
    }
    events = [
        {
            "event_type": "preparation_piece_received_for_assembly",
            "client_request_id": "receive-request-1",
            "occurred_at": received_at,
            "actor_id": "employee-mohammed",
            "actor_name": "محمد فؤاد",
        },
        {
            "event_type": "assembly_piece_marked_ready",
            "client_request_id": "ready-request-1",
            "occurred_at": ready_at,
            "actor_id": "employee-abdulbari",
            "actor_name": "عبدالباري",
        },
    ]
    assert provable_piece_actor_attribution_repair(
        piece,
        events,
        merchant_owner_id="owner-1",
    ) == {
        "preparation_received_by": "employee-mohammed",
        "preparation_received_by_name": "محمد فؤاد",
        "assembly_ready_by": "employee-abdulbari",
        "assembly_ready_by_name": "عبدالباري",
    }


def test_build37_historical_actor_repair_does_not_guess_from_owner_or_ambiguous_events():
    at = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)
    piece = {
        "preparation_receipt_client_request_id": "receive-request-1",
        "preparation_received_at": at,
        "preparation_received_by": "owner-1",
        "preparation_received_by_name": "عرفات",
    }
    owner_event = [{
        "event_type": "preparation_piece_received_for_assembly",
        "client_request_id": "receive-request-1",
        "occurred_at": at,
        "actor_id": "owner-1",
        "actor_name": "عرفات",
    }]
    assert provable_piece_actor_attribution_repair(
        piece,
        owner_event,
        merchant_owner_id="owner-1",
    ) == {}

    ambiguous = [
        {
            "event_type": "preparation_piece_received_for_assembly",
            "client_request_id": "receive-request-1",
            "occurred_at": at,
            "actor_id": "employee-a",
            "actor_name": "أ",
        },
        {
            "event_type": "preparation_piece_received_for_assembly",
            "client_request_id": "receive-request-1",
            "occurred_at": at,
            "actor_id": "employee-b",
            "actor_name": "ب",
        },
    ]
    assert provable_piece_actor_attribution_repair(
        piece,
        ambiguous,
        merchant_owner_id="owner-1",
    ) == {}
