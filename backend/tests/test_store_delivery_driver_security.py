"""Security contracts for Amasi Delivery purpose-bound access."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from mobile_app_request_context import mobile_app_request_user
from store_delivery_customer_instruction_routes import _require_customer_service
import store_delivery_driver_app_routes as driver_app_module
from store_delivery_driver_app_routes import (
    ASSIGNMENT_EXCEPTION_FIELDS,
    DELIVERY_EXCEPTION_CODES,
    DRIVER_STATUS_TRANSITIONS,
    ORDER_EXCEPTION_FIELDS,
    WORKFLOW_EXCEPTION_FIELDS,
    _push_salla_delivery_status,
    _require_store_driver,
    _true_barcode_match,
    _unset_fields,
)
from store_delivery_driver_routes import DRIVER_ACCOUNT_ROLE, DriverAccountCreate, DriverCreate

SERVER_SOURCE = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")


def test_store_driver_role_has_zero_legacy_mezan_permissions():
    """Unknown roles fail closed in the legacy RBAC resolver."""
    assert DRIVER_ACCOUNT_ROLE == "store_driver"
    assert 'ROLE_DEFAULT_PERMS.get(role, [])' in SERVER_SOURCE
    assert '"store_driver":' not in SERVER_SOURCE


def test_driver_app_rejects_legacy_viewer_even_with_no_permissions():
    with pytest.raises(HTTPException) as exc:
        _require_store_driver({"id": "u1", "role": "viewer", "extra_permissions": []})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "store_driver_account_required"


def test_driver_app_accepts_only_store_driver_role():
    user = {"id": "u-driver", "role": DRIVER_ACCOUNT_ROLE, "_session_client": "amasi_mobile"}
    assert _require_store_driver(user) is user


def test_driver_app_rejects_non_native_store_driver():
    with pytest.raises(HTTPException) as exc:
        _require_store_driver({"id": "u-driver", "role": DRIVER_ACCOUNT_ROLE})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "store_driver_native_session_required"


def test_native_driver_bypasses_employee_page_guard_only_for_driver_app():
    user = {
        "id": "u-driver",
        "role": DRIVER_ACCOUNT_ROLE,
        "created_by": "owner-1",
        "_session_client": "amasi_mobile",
    }
    result = asyncio.run(
        mobile_app_request_user(
            object(),
            user,
            path="/api/store-delivery/app/deliveries/home",
            method="GET",
        )
    )
    assert result is user

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            mobile_app_request_user(
                object(),
                user,
                path="/api/store-delivery/drivers",
                method="GET",
            )
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "mobile_app_route_not_allowed"


def test_driver_pin_requires_exactly_six_ascii_digits():
    for invalid in ("short", "12345", "1234567", "abcdef", "١٢٣٤٥٦"):
        with pytest.raises(Exception):
            DriverAccountCreate(email="driver@example.com", password=invalid)

    payload = DriverAccountCreate(email="driver@example.com", password="123456")
    assert str(payload.email) == "driver@example.com"
    assert payload.password == "123456"


def test_driver_creation_requires_email_and_pin_together():
    base = {
        "name": "موصل",
        "phone": "0500000000",
        "city": "الرياض",
        "delivery_fee": 20,
    }
    with pytest.raises(Exception):
        DriverCreate(**base, email="driver@example.com")
    with pytest.raises(Exception):
        DriverCreate(**base, password="123456")

    payload = DriverCreate(**base, email="driver@example.com", password="123456")
    assert str(payload.email) == "driver@example.com"


def test_customer_service_existing_inbox_permission_can_manage_delivery_instructions():
    user = {
        "id": "cs1",
        "role": "viewer",
        "created_by": "owner1",
        "permissions": ["customer_intelligence.inbox.read"],
    }
    assert _require_customer_service(user) is user


def test_customer_service_without_role_or_permission_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _require_customer_service({"id": "viewer1", "role": "viewer"})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "delivery_instruction_permission_required"


def test_driver_scanner_accepts_shipment_barcode_fields_only():
    clauses = _true_barcode_match("SHIP-123")
    assert clauses == [
        {"barcode": "SHIP-123"},
        {"shipping_barcode": "SHIP-123"},
        {"tracking_number": "SHIP-123"},
    ]
    assert all("order_number" not in item and "order_id" not in item for item in clauses)


def test_driver_operational_exception_codes_are_bounded():
    assert DELIVERY_EXCEPTION_CODES == {
        "customer_unreachable",
        "customer_requested_delay",
        "customer_requested_cancel",
    }


def test_driver_salla_status_transition_is_written_and_read_back(monkeypatch):
    calls = []

    async def fake_call(db, user_id, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return {"data": {"status": {"slug": "delivering"}}}
        return {"success": True}

    monkeypatch.setattr(driver_app_module, "_call_salla", fake_call)
    result = asyncio.run(_push_salla_delivery_status(
        object(),
        user_id="merchant-1",
        assignment={"order_id": "101"},
        order={"order_id": "101"},
        slug="delivering",
    ))
    assert result["verified_slug"] == "delivering"
    assert calls == [
        ("POST", "/orders/101/status", {"json": {"slug": "delivering", "send_status_sms": False}}),
        ("GET", "/orders/101", {"params": {"format": "light"}}),
    ]


def test_driver_salla_status_readback_mismatch_fails_closed(monkeypatch):
    async def fake_call(db, user_id, method, path, **kwargs):
        if method == "GET":
            return {"data": {"status": {"slug": "processing"}}}
        return {"success": True}

    monkeypatch.setattr(driver_app_module, "_call_salla", fake_call)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_push_salla_delivery_status(
            object(),
            user_id="merchant-1",
            assignment={"order_id": "101"},
            order={"order_id": "101"},
            slug="delivered",
        ))
    assert exc.value.status_code == 502
    assert exc.value.detail["code"] == "salla_delivery_status_readback_mismatch"



def test_resolved_delivery_state_clears_active_exception_projections():
    assignment_unset = _unset_fields(ASSIGNMENT_EXCEPTION_FIELDS)
    order_unset = _unset_fields(ORDER_EXCEPTION_FIELDS)
    workflow_unset = _unset_fields(WORKFLOW_EXCEPTION_FIELDS)

    assert assignment_unset["delivery_exception_code"] == ""
    assert assignment_unset["delivery_exception_evidence_url"] == ""
    assert order_unset["store_delivery_exception_code"] == ""
    assert order_unset["store_delivery_customer_service_attention_required"] == ""
    assert workflow_unset["store_courier_exception_code"] == ""
    assert workflow_unset["customer_service_attention_required"] == ""



def test_out_for_delivery_can_be_reasserted_to_resume_after_exception():
    assert "out_for_delivery" in DRIVER_STATUS_TRANSITIONS["out_for_delivery"]
    assert "delivered" in DRIVER_STATUS_TRANSITIONS["out_for_delivery"]
    assert DRIVER_STATUS_TRANSITIONS["delivered"] == frozenset()
