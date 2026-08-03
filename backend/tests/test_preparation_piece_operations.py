from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from preparation_piece_operations import (
    DEFAULT_ESTIMATED_DURATION_MINUTES,
    PIECE_STATUS_ASSIGNED,
    FileSchedulePatchRequest,
    _can_start_assigned_file,
    build_duration_history,
    build_piece_documents,
    inherit_required_services,
    make_preparation_piece_operations_router,
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

    assert [row["service_id"] for row in services] == ["cut", "paint"]
    assert services[0]["source"] == "product"
    assert services[1]["source"] == "option"
    assert services[1]["condition"]["value_name"] == "ذهبي"


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
    assert documents[0]["estimated_due_at"] == assigned_at + timedelta(minutes=90)


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


def test_router_registers_my_work_manager_start_and_schedule_routes():
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
    assert ("/preparation-work-v1/manager/summary", "GET") in routes
    assert ("/preparation-work-v1/files/{file_number}/start", "POST") in routes
    assert ("/preparation-work-v1/files/{file_number}/schedule", "PUT") in routes
