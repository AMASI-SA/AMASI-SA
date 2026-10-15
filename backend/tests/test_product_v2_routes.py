from copy import deepcopy
from datetime import datetime, timezone

import pytest

import product_v2_routes
from product_v2_routes import normalize_salla_product


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$nin" in expected and actual in expected["$nin"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _Result:
    matched_count = 1


class _Collection:
    def __init__(self, rows=None):
        self.rows = [deepcopy(row) for row in (rows or [])]

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))
        return object()

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    async def update_one(self, query, update):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set") or {}))
            return _Result()
        return _Result()

    async def find_one_and_update(
        self,
        query,
        update,
        *,
        upsert=False,
        return_document=None,
    ):
        for row in self.rows:
            if not _matches(row, query):
                continue
            before = deepcopy(row)
            row.update(deepcopy(update.get("$set") or {}))
            return before
        if upsert:
            inserted = {
                **deepcopy(query),
                **deepcopy(update.get("$setOnInsert") or {}),
                **deepcopy(update.get("$set") or {}),
            }
            self.rows.append(inserted)
        return None

    async def update_many(self, query, update):
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set") or {}))
        return _Result()


class _DB:
    def __init__(self, product):
        self.collections = {
            product_v2_routes.PRODUCTS: _Collection([product]),
            product_v2_routes.SYNC_RUNS: _Collection(),
            product_v2_routes.CHANGE_LOG: _Collection(),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


def test_normalize_salla_product_builds_stable_v2_contract():
    now = datetime.now(timezone.utc)
    raw = {
        "id": 12345,
        "name": "سلسال بالاسم",
        "sku": "NAME-01",
        "barcode": "628000001",
        "price": {"amount": "149.00", "currency": "SAR"},
        "sale_price": {"amount": 129},
        "quantity": 17,
        "status": "sale",
        "main_image": {"url": "https://cdn.example.com/product.jpg"},
        "categories": [{"id": 8, "name": "السلاسل"}],
        "options": [{"id": 1}],
        "skus": [{"id": 1}, {"id": 2}],
    }

    product = normalize_salla_product(raw, user_id="owner-1", synced_at=now)

    assert product["user_id"] == "owner-1"
    assert product["mezan_product_id"] == "mpv2_12345"
    assert product["salla_product_id"] == "12345"
    assert product["name"] == "سلسال بالاسم"
    assert product["sku"] == "NAME-01"
    assert product["barcode"] == "628000001"
    assert product["price"] == 149.0
    assert product["sale_price"] == 129.0
    assert product["currency"] == "SAR"
    assert product["quantity"] == 17.0
    assert product["status"] == "active"
    assert product["main_image"] == "https://cdn.example.com/product.jpg"
    assert product["categories"] == [{"id": "8", "name": "السلاسل"}]
    assert product["options_count"] == 1
    assert product["variants_count"] == 2
    assert product["archived"] is False
    assert product["source"] == "salla"
    assert len(product["source_revision"]) == 64


def test_normalize_salla_product_rejects_missing_external_id():
    try:
        normalize_salla_product({"name": "بدون رقم"}, user_id="owner-1", synced_at=datetime.now(timezone.utc))
    except ValueError as exc:
        assert str(exc) == "missing_salla_product_id"
    else:
        raise AssertionError("missing id must be rejected")


def test_normalize_salla_product_is_independent_from_legacy_shape():
    product = normalize_salla_product(
        {
            "product_id": "p-9",
            "title": "منتج مستقل",
            "stock_quantity": "3",
            "availability": "hidden",
        },
        user_id="owner-2",
        synced_at=datetime.now(timezone.utc),
    )

    assert product["salla_product_id"] == "p-9"
    assert product["name"] == "منتج مستقل"
    assert product["quantity"] == 3.0
    assert product["status"] == "inactive"
    assert "legacy" not in product


def test_normalize_salla_product_preserves_unlimited_inventory():
    product = normalize_salla_product(
        {
            "id": "1152485499",
            "name": "كوب دبدوب شفاف بغطاء ملون وشفاط",
            "quantity": None,
            "unlimited_quantity": True,
            "status": "sale",
        },
        user_id="owner-1",
        synced_at=datetime.now(timezone.utc),
    )

    assert product["quantity"] is None
    assert product["unlimited_quantity"] is True
    assert product["status"] == "active"


@pytest.mark.asyncio
async def test_format_light_sync_does_not_erase_previously_loaded_variants(monkeypatch):
    details_synced_at = datetime(2026, 8, 10, 9, tzinfo=timezone.utc)
    existing_variants = [
        {"id": "v-gold", "sku": "GOLD", "quantity": 8},
        {"id": "v-silver", "sku": "SILVER", "quantity": 4},
    ]
    db = _DB(
        {
            "id": "mezan-row-1",
            "user_id": "owner-1",
            "mezan_product_id": "mpv2_12345",
            "salla_product_id": "12345",
            "name": "الاسم الكامل",
            "source_revision": "full-detail-revision",
            "variants": deepcopy(existing_variants),
            "variants_count": 2,
            "details_synced_at": details_synced_at,
            "raw_salla": {"id": 12345, "variants": deepcopy(existing_variants)},
            "archived": False,
        }
    )

    async def light_products(*args, **kwargs):
        assert kwargs["params"]["format"] == "light"
        return {
            "data": [
                {
                    "id": 12345,
                    "name": "الاسم المختصر من light",
                    "status": "sale",
                    "quantity": 12,
                }
            ],
            "pagination": {"currentPage": 1, "totalPages": 1},
        }

    monkeypatch.setattr(product_v2_routes, "call_salla", light_products)
    result = await product_v2_routes.run_product_v2_sync(db, "owner-1")

    stored = db[product_v2_routes.PRODUCTS].rows[0]
    assert result["status"] == "completed"
    assert stored["variants"] == existing_variants
    assert stored["variants_count"] == 2
    assert stored["details_synced_at"] == details_synced_at
