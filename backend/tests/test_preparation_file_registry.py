from datetime import datetime, timezone
from types import SimpleNamespace

from preparation_file_registry import (
    PreparationFileDraftRequest,
    make_preparation_file_registry_router,
    normalize_file_title,
    preparation_file_name,
    preparation_file_number,
    preparation_file_view,
)


def test_file_title_is_safe_for_pdf_file_names():
    assert normalize_file_title(' دفعة / سلاسل : "الاسم" ') == "دفعة سلاسل الاسم"


def test_file_name_contains_date_and_actual_piece_count():
    assert preparation_file_name("دفعة السلاسل", "2026-08-02", 30) == (
        "دفعة السلاسل — 2026-08-02 — 30 قطعة.pdf"
    )


def test_permanent_file_number_uses_date_and_sequence():
    assert preparation_file_number("2026-08-02", 7) == "PF-20260802-0007"


def test_registry_view_preserves_employee_and_audit_fields():
    view = preparation_file_view({
        "file_number": "PF-20260802-0007",
        "batch_id": "batch-1",
        "client_request_id": "request-123",
        "status": "ready",
        "file_title": "دفعة السلاسل",
        "file_name": "دفعة السلاسل — 2026-08-02 — 30 قطعة.pdf",
        "file_date": "2026-08-02",
        "file_date_display": "2026/8/2",
        "allocated_quantity": 30,
        "selected_product_count": 2,
        "order_count": 25,
        "responsible_employee_id": "employee-1",
        "responsible_employee_name": "محمد",
        "responsible_employee_email": "m@example.com",
        "created_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
    })

    assert view["file_number"] == "PF-20260802-0007"
    assert view["allocated_quantity"] == 30
    assert view["responsible_employee_name"] == "محمد"
    assert view["mezan_only"] is True
    assert view["salla_updated"] is False
    assert view["qoyod_updated"] is False


def test_draft_requires_name_employee_and_expected_counts():
    payload = PreparationFileDraftRequest(
        client_request_id="request-123",
        file_title="دفعة السلاسل",
        responsible_employee_id="employee-1",
        expected_quantity=30,
        selected_product_count=2,
    )
    assert payload.expected_quantity == 30
    assert payload.selected_product_count == 2


def test_router_registers_employee_draft_finalize_and_history_routes():
    router = make_preparation_file_registry_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }

    assert ("/preparation-file-registry-v1/employees", "GET") in routes
    assert ("/preparation-file-registry-v1/drafts", "POST") in routes
    assert (
        "/preparation-file-registry-v1/finalize/{client_request_id}",
        "POST",
    ) in routes
    assert ("/preparation-file-registry-v1/files", "GET") in routes
