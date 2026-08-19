"""Security contract for the standalone Amasi Delivery driver account."""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "mezan_test")

from server import _effective_perms  # noqa: E402
from store_delivery_driver_app_routes import _require_store_driver  # noqa: E402
from store_delivery_driver_routes import (  # noqa: E402
    DRIVER_ACCOUNT_ROLE,
    DriverAccountCreate,
)


def test_store_driver_role_has_zero_legacy_mezan_permissions():
    assert DRIVER_ACCOUNT_ROLE == "store_driver"
    assert _effective_perms({
        "role": DRIVER_ACCOUNT_ROLE,
        "extra_permissions": [],
        "denied_permissions": [],
    }) == set()


def test_driver_app_rejects_legacy_viewer_even_with_no_permissions():
    with pytest.raises(HTTPException) as exc:
        _require_store_driver({"id": "u1", "role": "viewer", "extra_permissions": []})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "store_driver_account_required"


def test_driver_app_accepts_only_store_driver_role():
    user = {"id": "u-driver", "role": DRIVER_ACCOUNT_ROLE}
    assert _require_store_driver(user) is user


def test_driver_password_minimum_is_enforced():
    with pytest.raises(Exception):
        DriverAccountCreate(email="driver@example.com", password="short")

    payload = DriverAccountCreate(
        email="driver@example.com",
        password="DeliveryPass123!",
    )
    assert str(payload.email) == "driver@example.com"
