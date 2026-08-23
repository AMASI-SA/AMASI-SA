"""Security contracts for Amasi Delivery purpose-bound access."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from store_delivery_customer_instruction_routes import _require_customer_service
from store_delivery_driver_app_routes import _require_store_driver
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
    user = {"id": "u-driver", "role": DRIVER_ACCOUNT_ROLE}
    assert _require_store_driver(user) is user


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
