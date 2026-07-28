"""Stage-one review invariants: image learning, RBAC and status lookup."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from order_engine.mapper import map_salla_order
from order_item_engine.models import (
    OrderItemIdentityDTO,
    OrderItemOptionDTO,
    OrderItemSourceDTO,
)
from order_review_routes import (
    _can_review,
    _merchant_user_id,
    _refresh_review_source_once,
    _review_item_identities,
    _reviewed_status_id,
    build_image_preference_identity,
)


def item(*, color="فضي", personal_name="أحمد", product_id="p-1"):
    return OrderItemIdentityDTO(
        order_item_id="item-1",
        order_id="order-1",
        order_number="300",
        order_created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        line_index=0,
        source=OrderItemSourceDTO(source_order_id="order-1"),
        product_id=product_id,
        name="سلسال",
        quantity=1,
        color=color,
        options=[
            OrderItemOptionDTO(name="اللون", value=color),
            OrderItemOptionDTO(name="الاسم المنقوش", value=personal_name),
        ],
    )


def test_image_preference_reuses_visual_options_not_personal_text():
    first = build_image_preference_identity(item(personal_name="أحمد"))
    second = build_image_preference_identity(item(personal_name="سارة"))

    assert first[0] == "product:p-1"
    assert first[1] == second[1]
    assert "الاسم المنقوش" not in first[2]


def test_image_preference_separates_color_and_product():
    silver = build_image_preference_identity(item(color="فضي"))
    gold = build_image_preference_identity(item(color="ذهبي"))
    other_product = build_image_preference_identity(item(product_id="p-2"))

    assert silver[1] != gold[1]
    assert silver[1] != other_product[1]


def test_image_preference_keeps_visual_color_even_when_label_mentions_name():
    white = item().model_copy(update={
        "options": [
            OrderItemOptionDTO(name="الاسم", value="أحمد"),
            OrderItemOptionDTO(name="لون حفر الاسم", value="أبيض"),
        ],
    })
    black = white.model_copy(update={
        "options": [
            OrderItemOptionDTO(name="الاسم", value="سارة"),
            OrderItemOptionDTO(name="لون حفر الاسم", value="أسود"),
        ],
    })

    white_identity = build_image_preference_identity(white)
    black_identity = build_image_preference_identity(black)

    assert "الاسم" not in white_identity[2]
    assert white_identity[2]["لون حفر الاسم"] == "أبيض"
    assert white_identity[1] != black_identity[1]


def test_only_order_managers_can_review():
    assert _can_review({"id": "1", "role": "owner"})
    assert _can_review({"id": "2", "role": "operations", "created_by": "1"})
    assert _can_review({"id": "3", "role": "viewer", "extra_permissions": ["orders.manage"]})
    assert not _can_review({"id": "4", "role": "viewer"})
    assert not _can_review({"id": "5", "role": "operations", "denied_permissions": ["orders.manage"]})


def test_employee_reads_store_owner_data_but_keeps_separate_actor():
    assert _merchant_user_id({"id": "employee-1", "role": "operations", "created_by": "owner-1"}) == "owner-1"
    assert _merchant_user_id({"id": "owner-1", "role": "owner"}) == "owner-1"


def test_salla_reviewed_status_id_accepts_both_arabic_names():
    response = {
        "data": [
            {"id": 10, "name": "بانتظار المراجعة"},
            {"children": [{"status_id": "22", "name": "تمت المراجعة"}]},
        ]
    }
    assert _reviewed_status_id(response) == 22


class _GalleryCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def limit(self, _limit):
        return self

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _GalleryCollection:
    def __init__(self):
        self.rows = []

    def find(self, _query, _projection=None):
        return _GalleryCursor(self.rows)

    async def update_one(self, selector, update, upsert=False):
        row = next(
            (
                existing for existing in self.rows
                if all(existing.get(key) == value for key, value in selector.items())
            ),
            None,
        )
        if row is None:
            if not upsert:
                return None
            row = dict(selector)
            row.update(update.get("$setOnInsert") or {})
            self.rows.append(row)
        row.update(update.get("$set") or {})
        return None


class _GalleryDB:
    def __init__(self):
        self.salla_products = _GalleryCollection()
        self.products = _GalleryCollection()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.mark.asyncio
async def test_review_refreshes_and_caches_the_complete_product_gallery():
    db = _GalleryDB()
    order = map_salla_order({
        "id": 604952191,
        "reference_id": "273106396",
        "date": "2026-07-19T14:38:01+03:00",
        "amounts": {"total": {"amount": 350, "currency": "SAR"}},
        "items": [{
            "id": 1471692337,
            "product_id": 1008190362,
            "sku": "AMS11889",
            "name": "قلادة روز بالاسم مطلي ذهب",
            "quantity": 1,
            "thumbnail": "https://example.test/order-thumbnail.jpg",
        }],
    })
    product_response = {
        "data": {
            "id": 1008190362,
            "sku": "AMS11889",
            "name": "قلادة روز بالاسم مطلي ذهب",
            "main_image": "https://example.test/main.jpg",
            "images": [
                {"url": "https://example.test/silver.jpg"},
                {"url": "https://example.test/gold.jpg"},
                {"url": "https://example.test/close-up.jpg"},
            ],
        },
    }

    with patch("order_review_routes.call_salla", new=AsyncMock(return_value=product_response)) as fetch:
        items = await _review_item_identities(db, "owner-1", order)

    assert items[0].image_urls == [
        "https://example.test/order-thumbnail.jpg",
        "https://example.test/main.jpg",
        "https://example.test/silver.jpg",
        "https://example.test/gold.jpg",
        "https://example.test/close-up.jpg",
    ]
    fetch.assert_awaited_once()

    with patch("order_review_routes.call_salla", new=AsyncMock()) as second_fetch:
        cached_items = await _review_item_identities(db, "owner-1", order)

    assert cached_items[0].image_urls == items[0].image_urls
    second_fetch.assert_not_awaited()



class _ReviewRefreshCollection:
    def __init__(self):
        self.row = {"user_id": "owner-1", "order_number": "274724433"}

    async def find_one(self, _query, _projection=None):
        return dict(self.row)

    async def update_one(self, _selector, update):
        self.row.update(update.get("$set") or {})
        return None


class _ReviewRefreshDB:
    def __init__(self):
        self.unified_orders = _ReviewRefreshCollection()


@pytest.mark.asyncio
async def test_review_open_refreshes_authoritative_salla_details_only_once():
    db = _ReviewRefreshDB()
    result = {"ok": True, "found": True}

    with patch("order_review_routes.resync_single_order", new=AsyncMock(return_value=result)) as refresh:
        assert await _refresh_review_source_once(db, "owner-1", "274724433") is True
        assert await _refresh_review_source_once(db, "owner-1", "274724433") is False

    refresh.assert_awaited_once_with(db, "owner-1", "274724433")
    assert db.unified_orders.row["order_review_source_refresh_mode"] == "explicit_review_open"
