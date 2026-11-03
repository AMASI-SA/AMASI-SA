"""Safety/regression coverage: Salla edited order address must reach Mezan."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from order_engine.repository import _apply_v2_root_fallbacks
from order_engine.mapper import map_salla_order
from salla_integration.address_webhook_sync import sync_verified_shipping_address


class _Orders:
    def __init__(self, row):
        self.row = deepcopy(row)
        self.writes = []

    async def find_one(self, query, projection=None):
        if self.row and self.row.get("user_id") == query.get("user_id") and self.row.get("order_number") == query.get("order_number"):
            return deepcopy(self.row)
        return None

    async def update_one(self, query, update, **kwargs):
        self.writes.append(deepcopy(update))
        if not self.row:
            return SimpleNamespace(matched_count=0)
        for key, value in update.get("$set", {}).items():
            self.row[key] = deepcopy(value)
        for key in update.get("$unset", {}):
            self.row.pop(key, None)
        return SimpleNamespace(matched_count=1)


class _DB:
    def __init__(self, order):
        self.unified_orders = _Orders(order)


def _event(address, *, event="order.shipping.address.updated", created="2026-11-03T16:05:00+03:00"):
    return {
        "event": event, "merchant": "store-1", "created_at": created,
        "data": {
            "id": 42, "reference_id": "295617138",
            "status": {"slug": "in_progress"},
            "shipping": {"address": deepcopy(address)},
            "shipments": [{
                "id": 21, "order_id": 42,
                "ship_to": {"city": "جدة", "address_line": "عنوان قديم"},
            }],
        },
    }


async def _scoped(_db, _owner, fn):
    return await fn(_db)


@pytest.mark.asyncio
async def test_address_webhook_overrides_old_shipping_fields_only():
    old = {"user_id": "owner", "order_number": "295617138", "order_id": "42",
           "order_status": "in_progress", "shipping_city": "جدة",
           "shipping_street": "شارع قديم", "shipping_postal_code": "22222",
           "salla_shipment_id": "21", "tracking_number": "AWB-21",
           "carrier_label_url": "https://carrier.example/old.pdf",
           "total_amount": 100,
           "raw_by_source": {"salla_direct": {
               "id": 42, "reference_id": "295617138",
               "shipments": [{"id": 21, "ship_to": {
                   "city": "جدة", "address_line": "عنوان قديم"}}],
           }}}
    db = _DB(old)
    address = {"city": {"name": "الرياض"}, "block": "النرجس",
               "address_line": "شارع جديد", "short_address": "RYYY1234"}
    with patch("salla_integration.address_webhook_sync._resolve_user_id", new=AsyncMock(return_value="owner")), \\
         patch("salla_integration.address_webhook_sync.operational_owner", side_effect=_scoped):
        result = await sync_verified_shipping_address(db, _event(address))
    assert result["synced"] is True
    assert db.unified_orders.row["shipping_address_raw"] == address
    assert db.unified_orders.row["shipping_city"] == "الرياض"
    assert db.unified_orders.row["shipping_address"] == "شارع جديد"
    assert db.unified_orders.row["shipping_short_address"] == "RYYY1234"
    assert "shipping_street" not in db.unified_orders.row
    assert "shipping_postal_code" not in db.unified_orders.row
    for field in ("total_amount", "tracking_number", "salla_shipment_id", "carrier_label_url", "order_status", "raw_by_source"):
        assert db.unified_orders.row[field] == old[field]
    assert result["no_provider_writes"] and result["no_accounting_writes"]


@pytest.mark.asyncio
async def test_older_address_event_must_not_restore_old_destination():
    db = _DB({"user_id": "owner", "order_number": "295617138", "order_id": "42",
              "shipping_city": "الرياض", "salla_address_provider_updated_at": "2026-11-03T17:00:00+00:00"})
    with patch("salla_integration.address_webhook_sync._resolve_user_id", new=AsyncMock(return_value="owner")), \\
         patch("salla_integration.address_webhook_sync.operational_owner", side_effect=_scoped):
        result = await sync_verified_shipping_address(db, _event(
            {"city": "جدة"}, created="2026-11-03T14:00:00+00:00"))
    assert result["synced"] is False
    assert result["reason"] == "older_address_event"
    assert not db.unified_orders.writes


@pytest.mark.asyncio
async def test_wrong_order_identity_rejected_without_write():
    db = _DB({"user_id": "owner", "order_number": "295617138", "order_id": "999"})
    with patch("salla_integration.address_webhook_sync._resolve_user_id", new=AsyncMock(return_value="owner")), \\
         patch("salla_integration.address_webhook_sync.operational_owner", side_effect=_scoped):
        result = await sync_verified_shipping_address(db, _event({"city": "الرياض"}))
    assert result["reason"] == "order_identity_mismatch"
    assert not db.unified_orders.writes


def test_address_projection_prefers_verified_new_address_over_issued_shipment():
    old_shipment_address = {"city": "جدة", "address_line": "عنوان قديم"}
    raw = {
        "id": 42, "reference_id": "295617138",
        "date": "2026-11-03T08:00:00+03:00",
        "status": {"slug": "in_progress"},
        "customer": {"full_name": "اختبار"},
        "shipping": {"address": old_shipment_address},
        "shipments": [{"id": 21, "status": "created",
                       "ship_to": old_shipment_address}],
        "items": [],
    }
    new_address = {"city": "الرياض", "address_line": "حي النرجس",
                   "short_address": "RYYY1234"}
    row = {
        "shipping_address_raw": new_address,
        "shipping_city": "الرياض",
        "salla_address_verified_at": "2026-11-03T16:05:00+00:00",
    }
    after = _apply_v2_root_fallbacks(raw, row)
    order = map_salla_order(after)
    assert order.customer.shipping_address.city == "الرياض"
    assert order.customer.shipping_address.address_line == "حي النرجس"
    assert raw["shipments"][0]["ship_to"] == old_shipment_address
    assert after["shipments"][0]["ship_to"] == old_shipment_address
    assert after["shipping"]["address"]["city"] == "الرياض"


def test_unverified_shipping_address_keeps_original_provider_precedence():
    raw = {"shipping": {"address": {"city": "جدة"}},
           "shipments": [{"id": 21, "ship_to": {"city": "الرياض"}}]}
    after = _apply_v2_root_fallbacks(raw, {"shipping_address_raw": {"city": "تبوك"}})
    assert "_mezan_current_shipping_address" not in after
