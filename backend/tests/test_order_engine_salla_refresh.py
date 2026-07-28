"""Contracts for the central Order Engine V2 Salla refresh capability."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from order_engine.salla_refresh import (
    REFRESH_TIMESTAMP_FIELD,
    extract_order_details_address,
    refresh_order_from_salla,
)


class _UnifiedOrders:
    def __init__(self, row):
        self.row = dict(row)
        self.updates = []

    async def find_one(self, _query, _projection=None):
        return dict(self.row)

    async def update_one(self, _query, update, upsert=False):
        self.updates.append(update)
        self.row.update(update.get("$set") or {})
        return SimpleNamespace(matched_count=1, upserted_id=None)


class _DB:
    def __init__(self, row):
        self.unified_orders = _UnifiedOrders(row)


@pytest.mark.asyncio
async def test_refresh_reads_shipping_from_order_details_and_items_only():
    db = _DB({
        "user_id": "owner-1",
        "order_number": "274682897",
        "order_id": "901",
        "raw_by_source": {
            "salla_direct": {
                "id": "901",
                "reference_id": "274682897",
                "date": "2026-07-28T09:00:00+03:00",
                "customer": {"full_name": "عبدالله جمعي"},
            },
        },
    })
    calls = []

    async def fake_call(_db, _user_id, method, path, params=None, json=None):
        calls.append((method, path, params, json))
        assert method == "GET"
        if path == "/orders/901":
            assert params == {"format": "light"}
            return {"data": {
                "id": 901,
                "reference_id": "274682897",
                "date": "2026-07-28T09:00:00+03:00",
                "status": {"name": "بانتظار المراجعة", "slug": "under_review"},
                "payment_method": "credit_card",
                "amounts": {"total": {"amount": 127.60, "currency": "SAR"}},
                "customer": {
                    "full_name": "عبدالله جمعي",
                    "mobile": "561752841",
                    "city": "الرياض",
                    "country": "السعودية",
                    "country_code": "SA",
                    "location": "حي العليا، طريق الملك فهد",
                },
                "receiver": {
                    "name": "عبدالله جمعي",
                    "phone": "561752841",
                },
                "shipping": {
                    "company": {"name": "iMile", "code": "imile"},
                    "method": {"name": "توصيل"},
                },
            }}
        if path == "/orders/items":
            assert params == {"order_id": "901"}
            return {"data": [{
                "id": 501,
                "product_id": 1001,
                "name": "قلادة اختبار",
                "sku": "AMS12095",
                "quantity": 1,
                "options": [{"name": "هل تريد إضافة كرت إهداء", "value": {"name": "لا"}}],
                "amounts": {"total": {"amount": 127.60, "currency": "SAR"}},
            }]}
        raise AssertionError(f"Unexpected Salla endpoint: {path}")

    captured = {}

    async def fake_upsert(_db, user_id, order_number, doc, source, raw=None):
        captured.update({
            "user_id": user_id,
            "order_number": order_number,
            "doc": doc,
            "source": source,
            "raw": raw,
        })
        return {"created": False, "doc": doc}

    async def passthrough_bank(_db, _user_id, order):
        return order

    with (
        patch("order_engine.salla_refresh.call_salla", new=fake_call),
        patch("order_engine.salla_refresh.upsert_order", new=fake_upsert),
        patch("order_engine.salla_refresh._enrich_order_receiving_bank", new=passthrough_bank),
    ):
        result = await refresh_order_from_salla(
            db,
            "owner-1",
            "274682897",
            force=True,
        )

    assert result["ok"] is True
    assert result["found"] is True
    assert result["address_found"] is True
    assert result["no_shipments_api_calls"] is True
    assert result["no_qoyod_calls"] is True
    assert all(not path.startswith("/shipments") for _, path, _, _ in calls)

    doc = captured["doc"]
    assert captured["source"] == "salla_direct"
    assert doc["shipping_company"] == "iMile"
    assert doc["shipping_city"] == "الرياض"
    assert doc["shipping_country"] == "السعودية"
    assert doc["shipping_address"] == "حي العليا، طريق الملك فهد"
    assert len(doc["products"]) == 1
    assert doc["products"][0]["options"][0]["value"] == "لا"

    raw = captured["raw"]
    assert raw["shipping_address"]["city"] == "الرياض"
    assert raw["shipping_address"]["formatted"] == "حي العليا، طريق الملك فهد"
    assert raw["shipping"]["address"]["country"] == "السعودية"
    assert len(raw["items"]) == 1
    assert db.unified_orders.row["shipping_company"] == "iMile"
    assert db.unified_orders.row["shipping_city"] == "الرياض"
    assert db.unified_orders.row["shipping_country"] == "السعودية"
    assert db.unified_orders.row["shipping_address"] == "حي العليا، طريق الملك فهد"


@pytest.mark.asyncio
async def test_refresh_preserves_richer_existing_raw_when_light_details_omit_it():
    db = _DB({
        "user_id": "owner-1",
        "order_number": "274724433",
        "order_id": "902",
        "raw_by_source": {
            "salla_direct": {
                "id": "902",
                "reference_id": "274724433",
                "date": "2026-07-28T10:00:00+03:00",
                "shipping_address": {
                    "city": "جدة",
                    "district": "الروضة",
                    "street": "شارع الأمير",
                },
            },
        },
    })
    captured = {}

    async def fake_call(_db, _user_id, method, path, params=None, json=None):
        if path == "/orders/902":
            return {"data": {
                "id": 902,
                "reference_id": "274724433",
                "date": "2026-07-28T10:00:00+03:00",
                "status": {"name": "بانتظار المراجعة", "slug": "under_review"},
                "customer": {"full_name": "شهد", "city": "جدة"},
                "amounts": {"total": {"amount": 134, "currency": "SAR"}},
            }}
        if path == "/orders/items":
            return {"data": []}
        raise AssertionError(path)

    async def fake_upsert(_db, _user_id, _order_number, doc, source, raw=None):
        captured["raw"] = raw
        return {"created": False, "doc": doc}

    async def passthrough_bank(_db, _user_id, order):
        return order

    with (
        patch("order_engine.salla_refresh.call_salla", new=fake_call),
        patch("order_engine.salla_refresh.upsert_order", new=fake_upsert),
        patch("order_engine.salla_refresh._enrich_order_receiving_bank", new=passthrough_bank),
    ):
        result = await refresh_order_from_salla(db, "owner-1", "274724433", force=True)

    assert result["ok"] is True
    assert captured["raw"]["shipping_address"]["city"] == "جدة"
    assert captured["raw"]["shipping_address"]["district"] == "الروضة"
    assert captured["raw"]["shipping_address"]["street"] == "شارع الأمير"


@pytest.mark.asyncio
async def test_recent_central_snapshot_skips_salla_calls():
    db = _DB({
        "user_id": "owner-1",
        "order_number": "274711596",
        "order_id": "903",
        REFRESH_TIMESTAMP_FIELD: datetime.now(timezone.utc).isoformat(),
        "raw_by_source": {"salla_direct": {"id": "903"}},
    })

    with patch("order_engine.salla_refresh.call_salla", new=AsyncMock()) as call:
        result = await refresh_order_from_salla(
            db,
            "owner-1",
            "274711596",
            force=False,
            minimum_fresh_seconds=120,
        )

    assert result["ok"] is True
    assert result["skipped"] is True
    call.assert_not_awaited()


def test_address_extractor_accepts_customer_city_country_and_location():
    address, source = extract_order_details_address({
        "customer": {
            "city": "الرياض",
            "country": "السعودية",
            "location": "حي النرجس، شارع عثمان بن عفان",
        },
        "receiver": {"name": "مستلم الطلب"},
    })

    assert source == "order.customer"
    assert address["city"] == "الرياض"
    assert address["country"] == "السعودية"
    assert address["formatted"] == "حي النرجس، شارع عثمان بن عفان"
