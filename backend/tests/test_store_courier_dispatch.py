import sys
import types

from fastapi import HTTPException
import pytest

# The tests below exercise the store-courier permission and state contracts.
# Stub the heavy Order Engine package before importing the route module so this
# focused unit suite does not initialize every Salla integration through
# order_engine.__init__. Production/release workflows still import the real app
# with the complete dependency set.
order_engine_package = types.ModuleType("order_engine")
order_engine_package.__path__ = []
repository_module = types.ModuleType("order_engine.repository")
service_module = types.ModuleType("order_engine.service")


class _MongoOrderRepository:
    def __init__(self, db):
        self.db = db


class _OrderNotFoundError(LookupError):
    pass


async def _get_order(*args, **kwargs):  # pragma: no cover - not used here
    raise _OrderNotFoundError()


repository_module.MongoOrderRepository = _MongoOrderRepository
service_module.OrderNotFoundError = _OrderNotFoundError
service_module.get_order = _get_order
sys.modules.setdefault("order_engine", order_engine_package)
sys.modules.setdefault("order_engine.repository", repository_module)
sys.modules.setdefault("order_engine.service", service_module)

from ai_store_access_contract import ROLE_CATALOG
from store_courier_dispatch_routes import (
    ASSIGNED_WAITING_PICKUP,
    ASSIGN_PERMISSION,
    DELIVER_PERMISSION,
    _address_text,
    _assignment_is_store_courier,
    _my_stage_filter,
    _store_courier_assignment_blocker,
)


def test_dispatch_and_delivery_roles_are_separated():
    assert ASSIGN_PERMISSION in ROLE_CATALOG["shipping_operator"]
    assert DELIVER_PERMISSION not in ROLE_CATALOG["shipping_operator"]
    assert DELIVER_PERMISSION in ROLE_CATALOG["store_courier"]
    assert ASSIGN_PERMISSION not in ROLE_CATALOG["store_courier"]


def test_only_explicit_store_courier_identity_is_eligible():
    assert _assignment_is_store_courier({
        "role_key": "store_courier",
        "enabled": True,
    }) is True

    # Granting the low-level permission alone must not make a generic shipping
    # employee appear in the courier picker.
    assert _assignment_is_store_courier({
        "role_key": "shipping_operator",
        "enabled": True,
        "extra_permissions": [DELIVER_PERMISSION],
    }) is False

    # A custom operational assignment is accepted only with the explicit
    # store-courier delivery responsibility.
    assert _assignment_is_store_courier({
        "role_key": "shipping_operator",
        "enabled": True,
        "extra_permissions": [DELIVER_PERMISSION],
        "fulfillment_responsibilities": ["store_courier_delivery"],
    }) is True

    assert _assignment_is_store_courier({
        "role_key": "store_courier",
        "enabled": False,
    }) is False


def test_my_shipment_stage_filters_keep_each_queue_isolated():
    assert _my_stage_filter("waiting") == {
        "stage": "completed",
        "store_courier_assignment_state": ASSIGNED_WAITING_PICKUP,
    }
    assert _my_stage_filter("delivering") == {
        "stage": "delivering",
        "store_courier_assignment_state": "delivering",
    }
    assert _my_stage_filter("delivered") == {
        "stage": "delivered",
        "store_courier_assignment_state": "delivered",
    }
    assert _my_stage_filter("all") == {
        "store_courier_assignment_state": {
            "$nin": [None, "", "cancelled"],
        }
    }


def test_invalid_courier_stage_fails_closed():
    with pytest.raises(HTTPException) as exc_info:
        _my_stage_filter("external-carrier")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "store_courier_stage_invalid"


def test_address_text_prefers_canonical_formatted_address():
    assert _address_text({
        "formatted": "الرياض، حي الياسمين، شارع أنس بن مالك",
        "city": "الرياض",
    }) == "الرياض، حي الياسمين، شارع أنس بن مالك"


def test_address_text_builds_stable_fallback_without_duplicates():
    assert _address_text({
        "short_address": "RRAA1234",
        "district": "الياسمين",
        "street": "أنس بن مالك",
        "building_number": "12",
        "city": "الرياض",
        "country": "السعودية",
    }) == "RRAA1234، الياسمين، أنس بن مالك، 12، الرياض، السعودية"


def test_dispatch_requires_labeling_employee_print_confirmation():
    ready = {
        "carrier_label_type": "store_courier",
        "carrier_label_ready": True,
        "stage": "completed",
        "assembly_status": "completed",
    }

    assert _store_courier_assignment_blocker(ready) == (
        "store_courier_label_not_confirmed"
    )
    assert _store_courier_assignment_blocker({
        **ready,
        "carrier_label_print_confirmed": True,
    }) is None
