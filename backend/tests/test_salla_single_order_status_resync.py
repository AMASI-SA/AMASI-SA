"""Regression tests for single-order Salla status resync."""
from unittest.mock import AsyncMock, patch

import pytest

from salla_integration.sync import (
    _fetch_salla_order_details,
    _refresh_plan_b_status_snapshot,
)


@pytest.mark.asyncio
async def test_fetch_uses_order_details_as_authority():
    calls = []

    async def fake_call(db, user_id, method, path, params=None):
        calls.append((method, path, params))
        if path == "/orders":
            return {"data": [{
                "id": 987654,
                "reference_id": "271887616",
                "status": {"slug": "completed", "name": "تم التنفيذ"},
            }]}
        if path == "/orders/987654":
            return {"data": {
                "id": 987654,
                "reference_id": "271887616",
                "status": {
                    "slug": "under_review",
                    "name": "تم المراجعة",
                },
                }}

        if path == "/shipments":
            assert params == {"order_id": "987654", "per_page": 50}
            return {"data": []}

        assert path == "/orders/items"
        assert params == {"order_id": "987654"}

        return {
            "data": [
                {
                    "id": 365435777,
                    "name": "منتج اختبار",
                    "sku": "SKU-TEST",
                    "quantity": 1,
                    "amounts": {
                        "price_without_tax": {
                            "amount": 100,
                            "currency": "SAR",
                        },
                        "total": {
                            "amount": 115,
                            "currency": "SAR",
                        },
                    },
                    "options": [
                        {
                            "name": "المقاس",
                            "value": {
                                "id": 55,
                                "name": "60",
                            },
                        },
                    ],
                },
            ],
        }

    with patch("salla_integration.sync.call_salla", new=fake_call):
        details = await _fetch_salla_order_details(
            object(), "main", "271887616"
        )

    assert details["status"]["slug"] == "under_review"
    assert len(details["items"]) == 1
    assert details["items"][0]["sku"] == "SKU-TEST"
    assert details["items"][0]["options"][0]["name"] == "المقاس"

    assert calls[0][1] == "/orders"
    assert calls[1][1] == "/orders/987654"
    assert (
        "GET",
        "/orders/items",
        {"order_id": "987654"},
    ) in calls


@pytest.mark.asyncio
async def test_fetch_recovers_real_sku_from_variant_details():
    calls = []

    async def fake_call(db, user_id, method, path, params=None):
        calls.append((method, path, params))
        if path == "/orders":
            return {"data": [{
                "id": 987655,
                "reference_id": "277674576",
            }]}
        if path == "/orders/987655":
            return {"data": {
                "id": 987655,
                "reference_id": "277674576",
                "status": {"slug": "completed", "name": "تم التنفيذ"},
            }}
        if path == "/orders/items":
            return {"data": [{
                "id": 365435778,
                "product_sku_id": 2001,
                "product": {"id": 3001, "name": "منتج بلا SKU في الطلب"},
                "quantity": 1,
                "amounts": {
                    "price_without_tax": {"amount": 100, "currency": "SAR"},
                    "total": {"amount": 115, "currency": "SAR"},
                },
            }]}
        if path == "/shipments":
            assert params == {"order_id": "987655", "per_page": 50}
            return {"data": []}
        if path == "/products/variants/2001":
            return {"data": {"id": 2001, "sku": "REAL-VARIANT-SKU"}}
        raise AssertionError(f"unexpected Salla endpoint: {path}")

    with patch("salla_integration.sync.call_salla", new=fake_call):
        details = await _fetch_salla_order_details(
            object(), "orders-owner", "277674576"
        )

    assert details["items"][0]["sku"] == "REAL-VARIANT-SKU"
    assert details["items"][0]["_mezan_sku_resolution"] == {
        "source": "variant_details",
        "endpoint": "/products/variants/2001",
    }
    called_paths = [path for _, path, _ in calls]
    assert called_paths[:2] == ["/orders", "/orders/987655"]
    assert set(called_paths[2:4]) == {"/orders/items", "/shipments"}
    assert called_paths[-1] == "/products/variants/2001"


class _Result:
    def __init__(self, upserted_id=None):
        self.upserted_id = upserted_id


class _Inbox:
    def __init__(self):
        self.rows = []

    async def find_one(self, query, sort=None):
        if not self.rows:
            return {
                "user_id": "main",
                "canonical_payload": {
                    "order_number": "271887616",
                    "order_status": "completed",
                    "order_status_native": "تم التنفيذ",
                },
            }
        return self.rows[-1]

    async def update_one(self, selector, update, upsert=False):
        row = dict(update.get("$set") or {})
        existing = next((r for r in self.rows if all(
            r.get(k) == v for k, v in selector.items()
        )), None)
        if existing:
            existing.update(row)
            return _Result(None)
        row.update(update.get("$setOnInsert") or {})
        self.rows.append(row)
        return _Result("inserted")


class _DB:
    def __init__(self):
        self.integration_inbox = _Inbox()


@pytest.mark.asyncio
async def test_snapshot_is_status_aware_idempotent_and_never_sendable():
    db = _DB()
    doc = {
        "order_status_slug": "under_review",
        "order_status": "تم المراجعة",
    }

    first = await _refresh_plan_b_status_snapshot(
        db, "tenant-user", "271887616", doc
    )
    second = await _refresh_plan_b_status_snapshot(
        db, "tenant-user", "271887616", doc
    )

    assert first["created"] is True
    assert second["updated"] is True
    assert len(db.integration_inbox.rows) == 1
    row = db.integration_inbox.rows[0]
    assert row["idempotency_key"] == (
        "salla:order:271887616:order.updated:under_review"
    )
    assert row["canonical_payload"]["order_status"] == "under_review"
    assert row["canonical_payload"]["order_status_native"] == "تم المراجعة"
    assert row["user_id"] == "tenant-user"
    assert row["connector_key"] == "salla_direct_status_resync"
    assert row["pipeline_stage"] == "STATUS_SNAPSHOT"
    assert row["no_qoyod_send"] is True
    assert row["manual_send_allowed"] is False
    assert row["auto_send_allowed"] is False
