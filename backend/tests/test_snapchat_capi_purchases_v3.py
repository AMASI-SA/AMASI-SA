from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from integrations_control_center import snapchat_capi_purchases as capi


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc)


def _raw_order(**overrides):
    payload = {
        "id": 99101,
        "reference_id": "280001234",
        "created_at": "2026-08-01T15:30:00+03:00",
        "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
        "customer": {
            "id": 777,
            "email": " Buyer@Example.COM ",
            "mobile": "0555123456",
        },
        "amounts": {"total": {"amount": 250.75, "currency": "SAR"}},
        "shipping": {
            "address": {
                "city": " الرياض ",
                "country": "المملكة العربية السعودية",
                "postal_code": "12345",
            }
        },
        "landing_page_url": "https://amasi-sa.com/products/necklace?ScCid=snap-click-123&utm_source=snapchat",
        "_scid": "snap-cookie-456",
        "client_ip_address": "203.0.113.10",
        "client_user_agent": "Browser Secret Agent",
        "items": [
            {
                "id": 10,
                "quantity": 2,
                "product": {"id": 44, "sku": "SKU-44"},
                "amounts": {"price": {"amount": 100}},
            }
        ],
    }
    payload.update(overrides)
    return payload


def _order(**overrides):
    row = {
        "order_number": "280001234",
        "order_date_raw": "2026-08-01T15:30:00+03:00",
        "order_status": "بانتظار المراجعة",
        "order_status_slug": "under_review",
        "total_amount": 250.75,
        "currency": "SAR",
        "shipping_city": "الرياض",
        "shipping_country": "المملكة العربية السعودية",
        "shipping_postal_code": "12345",
        "customer_mobile": "0555123456",
        "products": [
            {"product_id": "44", "sku": "SKU-44", "quantity": 2, "price": 100}
        ],
    }
    row.update(overrides)
    return row


def test_normalizes_contact_fields_before_hashing():
    assert capi.normalize_email(" Buyer@Example.COM ") == "buyer@example.com"
    assert capi.normalize_phone(
        "0555 123 456",
        country="المملكة العربية السعودية",
    ) == "966555123456"
    assert capi.normalize_phone("00967 777 123 456", country="YE") == "967777123456"


def test_builds_purchase_with_hashed_contacts_and_snap_attribution_ids():
    event = capi.build_snapchat_purchase_event(
        _order(),
        raw=_raw_order(),
        now=_now(),
    )

    assert event is not None
    assert event["event_name"] == "PURCHASE"
    assert event["event_id"] == "280001234"
    assert event["custom_data"]["order_id"] == "280001234"
    assert event["custom_data"]["currency"] == "SAR"
    assert event["custom_data"]["value"] == 250.75
    assert event["custom_data"]["content_ids"] == ["SKU-44"]
    assert event["custom_data"]["contents"][0] == {
        "id": "SKU-44",
        "quantity": "2",
        "item_price": 100.0,
    }
    assert event["action_source"] == "WEB"
    assert event["event_source_url"] == "https://amasi-sa.com/products/necklace"

    user_data = event["user_data"]
    assert user_data["em"] == _sha("buyer@example.com")
    assert user_data["ph"] == _sha("966555123456")
    assert user_data["ct"] == _sha("الرياض")
    assert user_data["country"] == _sha("sa")
    assert user_data["zp"] == _sha("12345")
    assert user_data["external_id"] == _sha("777")
    assert user_data["sc_click_id"] == "snap-click-123"
    assert user_data["sc_cookie1"] == "snap-cookie-456"
    assert "client_ip_address" not in user_data
    assert "client_user_agent" not in user_data
    assert "integration" not in event

    serialized = json.dumps(event, ensure_ascii=False)
    assert "Buyer@Example.COM" not in serialized
    assert "buyer@example.com" not in serialized
    assert "0555123456" not in serialized
    assert "Browser Secret Agent" not in serialized
    assert "203.0.113.10" not in serialized


def test_rejects_cancelled_old_or_unmatchable_orders():
    assert capi.build_snapchat_purchase_event(
        _order(order_status_slug="cancelled", order_status="ملغي"),
        raw=_raw_order(status={"slug": "cancelled", "name": "ملغي"}),
        now=_now(),
    ) is None

    assert capi.build_snapchat_purchase_event(
        _order(order_date_raw="2026-07-20T10:00:00+03:00"),
        raw=_raw_order(created_at="2026-07-20T10:00:00+03:00"),
        now=_now(),
    ) is None

    raw = _raw_order(customer={}, receiver={})
    order = _order(customer_mobile=None)
    assert capi.build_snapchat_purchase_event(order, raw=raw, now=_now()) is None


class _ClaimCollection:
    def __init__(self):
        self.query = None

    async def find_one_and_update(self, query, update, **kwargs):
        self.query = query
        return None


class _ClaimDb:
    def __init__(self):
        self.collection = _ClaimCollection()

    def __getitem__(self, name):
        assert name == capi.OUTBOX_COLLECTION
        return self.collection


@pytest.mark.asyncio
async def test_outbox_claim_is_tenant_scoped():
    db = _ClaimDb()
    await capi._claim_event(
        db,
        user_id="owner-1",
        worker_id="worker-1",
        now=_now(),
    )
    assert db.collection.query["user_id"] == "owner-1"
    assert db.collection.query["status"] == {"$in": ["pending", "retry"]}


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        return list(self.rows)[:length]


class _PixelCollection:
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def find(self, query, projection):
        self.query = query
        return _Cursor(self.rows)


class _PixelDb:
    def __init__(self, rows):
        self.collection = _PixelCollection(rows)

    def __getitem__(self, name):
        assert name == capi.TRACKING_ASSET_COLLECTION
        return self.collection


@pytest.mark.asyncio
async def test_pixel_resolution_fails_closed_for_multiple_pixels(monkeypatch):
    monkeypatch.delenv(capi.CAPI_PIXEL_ID_ENV, raising=False)

    async def selected_accounts(db, user_id):
        return [{"ad_account_id": "a1"}, {"ad_account_id": "a2"}]

    monkeypatch.setattr(capi, "_load_selected_accounts", selected_accounts)
    db = _PixelDb(
        [
            {"pixel_id": "pixel-one"},
            {"pixel_id": "pixel-two"},
        ]
    )
    result = await capi.resolve_capi_pixel_id(
        db,
        "owner-1",
    )
    assert result.status == "pixel_selection_required"
    assert result.pixel_id is None
    assert result.candidates == ("pixel-one", "pixel-two")
    assert db.collection.query["$or"] == [
        {"ad_account_id": {"$in": ["a1", "a2"]}},
        {"ad_account_ids": {"$in": ["a1", "a2"]}},
    ]


@pytest.mark.asyncio
async def test_explicit_pixel_is_preferred_without_discovery(monkeypatch):
    monkeypatch.setenv(capi.CAPI_PIXEL_ID_ENV, "canonical-pixel")
    result = await capi.resolve_capi_pixel_id(object(), "owner-1")
    assert result.status == "ready"
    assert result.pixel_id == "canonical-pixel"
    assert result.source == "environment"


def test_capi_is_fail_closed_until_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(capi.CAPI_ENABLED_ENV, raising=False)
    assert capi.capi_enabled() is False
    monkeypatch.setenv(capi.CAPI_ENABLED_ENV, "true")
    assert capi.capi_enabled() is True


def test_capi_v3_endpoint_and_source_contract():
    assert capi.CAPI_ENDPOINT == "https://tr.snapchat.com/v3/{pixel_id}/events"
    assert capi.EVENT_MAX_AGE == timedelta(days=7)
    assert capi.SOURCE_MODE == "snapchat_conversions_api_v3_purchase_outbox"
