import asyncio

from fastapi.routing import APIRoute

from product_v2_workspace_routes import (
    make_product_v2_workspace_router,
    _mongo_id_values,
    _parse_product_ids,
    _requested_missing_mezan_cost_products,
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


def test_sold_missing_cost_filter_has_a_dedicated_non_generic_route():
    async def current_user():
        return {"id": "owner-1"}

    router = make_product_v2_workspace_router(object(), current_user)
    paths = {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
    }

    assert "/products-v2/workspace/sold-missing-cost-products" in paths
    assert "/products-v2/workspace/products" in paths


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
            {
                "id": "m-3",
                "mezan_product_id": "m-3",
                "salla_product_id": "p-3",
                "name": "بدون أي تكلفة",
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
                    {"product_id": "p-3", "quantity": 1},
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

    assert list(result) == ["p-1", "p-3"]
    assert result["p-1"]["uses_salla_fallback"] is True
    assert result["p-1"]["missing_everywhere"] is False
    assert result["p-1"]["calculation_cost_available"] is True
    assert result["p-3"]["uses_salla_fallback"] is False
    assert result["p-3"]["missing_everywhere"] is True
    assert result["p-3"]["calculation_cost_available"] is False


def test_dashboard_snapshot_cohort_keeps_salla_fallback_and_drops_current_mezan_cost():
    db = _Db({
        PRODUCTS: [
            {
                "id": "m-fallback",
                "mezan_product_id": "m-fallback",
                "salla_product_id": "p-fallback",
                "name": "تكلفة سلة فقط",
                "variants": [],
                "raw_salla_details": {"cost_price": 31.5},
            },
            {
                "id": "m-mezan",
                "mezan_product_id": "m-mezan",
                "salla_product_id": "p-mezan",
                "name": "اكتملت تكلفة ميزان",
                "cost_price_from_salla": 40,
                "variants": [],
            },
        ],
        COST_PROFILES: [
            {"salla_product_id": "p-mezan", "base_cost": 18},
        ],
    })

    result = asyncio.run(_requested_missing_mezan_cost_products(
        db,
        "owner-1",
        ["p-fallback", "p-mezan"],
    ))

    assert list(result) == ["p-fallback"]
    assert result["p-fallback"]["uses_salla_fallback"] is True
    assert result["p-fallback"]["missing_everywhere"] is False
    assert result["p-fallback"]["calculation_cost_available"] is True
    assert result["p-fallback"]["fallback_sources"] == ["salla_product_fallback"]
    assert result["p-fallback"]["cohort_source"] == "dashboard_snapshot_product_ids"


def test_targeted_missing_cost_revalidation_never_reads_orders():
    accessed = []

    class CountingDb(_Db):
        def __getitem__(self, name):
            accessed.append(name)
            if name == "unified_orders":
                raise AssertionError("targeted product deep-link must not scan orders")
            return super().__getitem__(name)

    db = CountingDb({
        PRODUCTS: [{
            "id": "m-one",
            "mezan_product_id": "m-one",
            "salla_product_id": "p-one",
            "name": "منتج واحد",
            "variants": [],
            "raw_salla_details": {"cost_price": 31.5},
        }],
        COST_PROFILES: [],
    })

    result = asyncio.run(_requested_missing_mezan_cost_products(
        db, "owner-1", ["m-one"],
    ))

    assert list(result) == ["p-one"]
    assert accessed == [PRODUCTS, COST_PROFILES]


def test_sold_missing_cohort_resolves_historical_line_from_current_full_snapshot(monkeypatch):
    async def settings(_db, _user_id):
        return {"report_included_statuses": ["تم التنفيذ"]}

    monkeypatch.setattr(
        "product_v2_workspace_routes._user_reporting_settings",
        settings,
    )
    db = _Db({
        PRODUCTS: [
            {
                "id": "m-current",
                "mezan_product_id": "m-current",
                "salla_product_id": "p-current",
                "name": "فستان بناتي أخضر لليوم الوطني",
                "variants": [],
                "raw_salla_details": {"cost_price": 31.5},
            },
        ],
        COST_PROFILES: [],
        "unified_orders": [
            {
                "order_status": "تم التنفيذ",
                "products": [
                    {
                        "product_id": "historical-product-id",
                        "name": "فستان بناتي أخضر لليوم الوطني",
                        "quantity": 1,
                    },
                ],
            },
        ],
    })

    result = asyncio.run(_sold_missing_mezan_cost_products(
        db,
        "owner-1",
        from_date="2026-08-16",
        to_date="2026-08-16",
    ))

    assert list(result) == ["p-current"]
    assert result["p-current"]["uses_salla_fallback"] is True
    assert result["p-current"]["missing_everywhere"] is False
    assert result["p-current"]["calculation_cost_available"] is True


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
