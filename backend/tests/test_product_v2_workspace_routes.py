import asyncio

from product_v2_workspace_routes import (
    _mongo_id_values,
    _parse_product_ids,
    _restrict_missing_rows,
    _sku_number,
    _sold_missing_mezan_cost_products,
)
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS


def test_sku_number_extracts_sequence():
    assert _sku_number("AMS12047", "AMS") == 12047
    assert _sku_number("ams00001", "AMS") == 1


def test_sku_number_rejects_other_formats():
    assert _sku_number("SKU-12047", "AMS") is None
    assert _sku_number("AMS12A", "AMS") is None
    assert _sku_number("", "AMS") is None


def test_requested_product_ids_are_deduplicated_and_support_numeric_catalog_ids():
    requested = _parse_product_ids("111,222,111")

    assert requested == ["111", "222"]
    assert _mongo_id_values(requested) == ["111", "222", 111, 222]


def test_requested_product_ids_restrict_the_sold_missing_cohort():
    rows = {
        "p-1": {"salla_product_id": "p-1", "mezan_product_id": "m-1"},
        "p-2": {"salla_product_id": "p-2", "mezan_product_id": "m-2"},
    }

    assert list(_restrict_missing_rows(rows, ["m-2"])) == ["p-2"]


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        return self.rows[:length]


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, *_args, **_kwargs):
        return _Cursor(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, name):
        return _Collection(self.rows.get(name, []))


def test_sold_salla_fallback_product_is_returned_as_missing_mezan(monkeypatch):
    async def settings(_db, _user_id):
        return {"report_included_statuses": ["تم التنفيذ"]}

    monkeypatch.setattr(
        "product_v2_workspace_routes._user_reporting_settings",
        settings,
    )
    db = _Db({
        PRODUCTS: [
            {
                "id": "m-1",
                "mezan_product_id": "m-1",
                "salla_product_id": "p-1",
                "name": "سلسال",
                "cost_price_from_salla": 25,
                "variants": [],
            },
            {
                "id": "m-2",
                "mezan_product_id": "m-2",
                "salla_product_id": "p-2",
                "name": "مريول",
                "cost_price_from_salla": 40,
                "variants": [],
            },
        ],
        COST_PROFILES: [
            {"salla_product_id": "p-2", "base_cost": 18},
        ],
        "unified_orders": [
            {
                "order_status": "تم التنفيذ",
                "products": [
                    {"product_id": "p-1", "quantity": 1},
                    {"product_id": "p-2", "quantity": 1},
                ],
            },
        ],
    })

    result = asyncio.run(_sold_missing_mezan_cost_products(
        db,
        "owner-1",
        from_date="2026-08-01",
        to_date="2026-08-01",
    ))

    assert list(result) == ["p-1"]
    assert result["p-1"]["uses_salla_fallback"] is True
    assert result["p-1"]["missing_everywhere"] is False


def test_sold_missing_products_use_same_payment_and_shipping_cohort(monkeypatch):
    async def settings(_db, _user_id):
        return {"report_included_statuses": ["تم التنفيذ"]}

    monkeypatch.setattr(
        "product_v2_workspace_routes._user_reporting_settings",
        settings,
    )
    db = _Db({
        PRODUCTS: [
            {
                "id": "m-mada",
                "mezan_product_id": "m-mada",
                "salla_product_id": "p-mada",
                "name": "منتج مدى",
                "cost_price_from_salla": 20,
                "variants": [],
            },
            {
                "id": "m-cod",
                "mezan_product_id": "m-cod",
                "salla_product_id": "p-cod",
                "name": "منتج الدفع عند الاستلام",
                "cost_price_from_salla": 25,
                "variants": [],
            },
        ],
        COST_PROFILES: [],
        "unified_orders": [
            {
                "order_status": "تم التنفيذ",
                "payment_method": "مدى",
                "shipping_company": "سمسا",
                "products": [{"product_id": "p-mada", "quantity": 1}],
            },
            {
                "order_status": "تم التنفيذ",
                "payment_method": "الدفع عند الاستلام",
                "shipping_company": "أرامكس",
                "products": [{"product_id": "p-cod", "quantity": 1}],
            },
        ],
    })

    result = asyncio.run(_sold_missing_mezan_cost_products(
        db,
        "owner-1",
        from_date="2026-08-01",
        to_date="2026-08-01",
        payment_methods=["مدى"],
        shipping_companies=["سمسا"],
    ))

    assert list(result) == ["p-mada"]
