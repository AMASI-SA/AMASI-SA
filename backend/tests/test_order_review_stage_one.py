"""Stage-one review invariants: image learning, RBAC and status lookup."""

from datetime import datetime, timezone
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from order_engine.mapper import map_salla_order
from order_engine.product_identity_enrichment import enrich_order_item_identity
from salla_integration.sync import _fetch_salla_shipment_details
from order_item_engine.models import (
    OrderItemIdentityDTO,
    OrderItemOptionDTO,
    OrderItemSourceDTO,
)
from order_review_routes import (
    _can_review,
    _find_pending_review_order,
    _is_pending_review_order,
    _merchant_user_id,
    _review_item_identities,
    _reviewed_status_id,
    build_image_preference_identity,
    make_order_review_router,
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


class _SearchWorkflowCollection:
    def __init__(self, stage=None):
        self.stage = stage

    async def find_one(self, _query, _projection=None):
        return {"stage": self.stage} if self.stage else None


class _SearchDB:
    def __init__(self, stage=None):
        self.order_review_workflows = _SearchWorkflowCollection(stage)

    def __getitem__(self, name):
        return getattr(self, name)


def _search_order(status_name="بانتظار المراجعة", status_slug="under_review"):
    return map_salla_order({
        "id": 604952191,
        "reference_id": "276218536",
        "date": "2026-08-04T01:00:00+03:00",
        "status": {"slug": status_slug, "name": status_name},
        "customer": {"full_name": "عميل اختبار"},
        "amounts": {"total": {"amount": 213.84, "currency": "SAR"}},
        "items": [{
            "id": 1471692337,
            "product_id": 1008190362,
            "name": "منتج اختبار",
            "quantity": 1,
        }],
    })


def test_exact_search_accepts_salla_pending_review_status():
    assert _is_pending_review_order(_search_order()) is True
    assert _is_pending_review_order(
        _search_order(status_name="قيد التنفيذ", status_slug="in_progress")
    ) is False


@pytest.mark.asyncio
async def test_exact_search_refreshes_from_salla_without_auto_advancing_workflow():
    order = _search_order()
    db = _SearchDB()
    with (
        patch(
            "order_review_routes.refresh_order_from_salla",
            new=AsyncMock(return_value={"ok": True, "found": True}),
        ) as refresh,
        patch("order_review_routes.get_order", new=AsyncMock(return_value=order)),
    ):
        found = await _find_pending_review_order(
            db,
            object(),
            user_id="owner-1",
            order_number="#276218536",
        )

    assert found is order
    refresh.assert_awaited_once_with(
        db,
        "owner-1",
        "276218536",
        force=False,
        minimum_fresh_seconds=30,
        allow_auto_fulfillment=False,
    )


@pytest.mark.asyncio
async def test_exact_search_hides_orders_already_completed_in_mezan():
    order = _search_order()
    db = _SearchDB(stage="reviewed")
    with (
        patch(
            "order_review_routes.refresh_order_from_salla",
            new=AsyncMock(return_value={"ok": True, "found": True}),
        ),
        patch("order_review_routes.get_order", new=AsyncMock(return_value=order)),
    ):
        found = await _find_pending_review_order(
            db,
            object(),
            user_id="owner-1",
            order_number="276218536",
        )

    assert found is None


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
        self.mezan_products_v2 = _GalleryCollection()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.mark.asyncio
async def test_review_identity_fills_missing_variant_sku_from_product_v2():
    db = _GalleryDB()
    db.mezan_products_v2.rows = [{
        "user_id": "owner-1",
        "salla_product_id": "p-cup",
        "sku": "AMS13032",
        "barcode": "base-barcode",
        "variants": [{
            "id": "variant-red",
            "sku": "AMS13032-RED",
            "barcode": "red-barcode",
        }],
    }]
    sparse = item(product_id="p-cup").model_copy(update={
        "variant_id": "variant-red",
        "sku": None,
        "barcode": None,
    })

    enriched = await enrich_order_item_identity(
        db,
        user_id="owner-1",
        items=[sparse],
    )

    assert enriched[0].order_item_id == sparse.order_item_id
    assert enriched[0].product_id == "p-cup"
    assert enriched[0].variant_id == "variant-red"
    assert enriched[0].sku == "AMS13032-RED"
    assert enriched[0].barcode == "red-barcode"


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




def test_review_detail_uses_central_orders_v2_refresh():
    source = inspect.getsource(make_order_review_router)

    assert "refresh_order_from_salla(" in source
    assert "minimum_fresh_seconds=120" in source
    assert "resync_single_order" not in source
    assert "_refresh_review_source_once" not in source


@pytest.mark.asyncio
async def test_empty_current_shipments_preserve_embedded_delivery_context():
    embedded = [{
        "id": "shipment-1",
        "courier": {"name": "iMile"},
        "shipping_address": {
            "country": {"name": "السعودية"},
            "city": {"name": "الرياض"},
            "district": "العليا",
            "street": "شارع الاختبار",
        },
        "label_url": "https://example.test/stale-label.pdf",
        "tracking_number": "STALE-TRACKING",
    }]

    async def fake_call(_db, _user_id, method, path, params=None):
        assert method == "GET"
        assert path == "/shipments"
        assert params == {"order_id": "order-1", "per_page": 50}
        return {"data": []}

    with patch("salla_integration.sync.call_salla", new=fake_call):
        rows = await _fetch_salla_shipment_details(
            object(), "owner-1", "order-1", embedded
        )

    assert len(rows) == 1
    assert rows[0]["courier"]["name"] == "iMile"
    assert rows[0]["shipping_address"]["city"]["name"] == "الرياض"
    assert rows[0]["shipping_address"]["district"] == "العليا"
    assert "label_url" not in rows[0]
    assert "tracking_number" not in rows[0]


@pytest.mark.asyncio
async def test_current_shipment_merges_embedded_address_without_reviving_stale_label():
    embedded = [{
        "id": "shipment-1",
        "courier": {"name": "iMile"},
        "shipping_address": {
            "city": {"name": "جدة"},
            "district": "الروضة",
            "street": "شارع الأمير",
        },
        "label_url": "https://example.test/stale-label.pdf",
    }]

    async def fake_call(_db, _user_id, method, path, params=None):
        assert method == "GET"
        if path == "/shipments":
            return {"data": [{"id": "shipment-1", "status": "created"}]}
        assert path == "/shipments/shipment-1"
        return {"data": {
            "id": "shipment-1",
            "tracking_number": "CURRENT-TRACKING",
            "shipping_address": {},
        }}

    with patch("salla_integration.sync.call_salla", new=fake_call):
        rows = await _fetch_salla_shipment_details(
            object(), "owner-1", "order-1", embedded
        )

    assert rows[0]["shipping_address"]["city"]["name"] == "جدة"
    assert rows[0]["shipping_address"]["street"] == "شارع الأمير"
    assert rows[0]["tracking_number"] == "CURRENT-TRACKING"
    assert "label_url" not in rows[0]
