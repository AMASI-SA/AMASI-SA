import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import Response

import dashboard_v2_routes as routes


class FakeCollection:
    def __init__(self, cached=None):
        self.cached = cached
        self.updates = []

    async def find_one(self, *_args, **_kwargs):
        return self.cached

    async def update_one(self, query, update, *, upsert=False):
        self.updates.append((query, update, upsert))
        return object()


class FakeDb:
    def __init__(self, cached=None):
        self.collection = FakeCollection(cached)

    def __getitem__(self, name):
        assert name == routes.OPTIONAL_CACHE
        return self.collection


def request(path="/api/dashboard-v2"):
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
        "state": {"request_id": "regression-request-id"},
    })


def dashboard_response():
    return {
        "totals": {
            "total_sales": 100.0,
            "total_orders": 1,
            "total_product_cost": 20.0,
            "total_ads_cost": 12.0,
            "operating_expenses_total": 3.0,
            "operating_salaries_total": 3.0,
            "net_profit": 65.0,
            "net_sales": 65.0,
        },
        "net_sales_config": {
            "deduct_product_costs": True,
            "deduct_ads": True,
            "deduct_operating_expenses": True,
        },
    }


def product_cost():
    return {
        "total": 20.0,
        "missing_products_count": 0,
        "incomplete_orders_count": 0,
        "no_products_orders_count": 0,
        "source_contract": {"source": "test"},
    }


def recurring():
    return {
        "total": 0.0,
        "rentals_total": 0.0,
        "utilities_total": 0.0,
        "renewals_total": 0.0,
        "by_type": {},
    }


def install_fast_fakes(monkeypatch):
    monkeypatch.setattr(
        routes.legacy,
        "make_dashboard_v2_router",
        lambda *_args, **_kwargs: APIRouter(),
    )

    async def filtered_orders(*_args, **_kwargs):
        return [{"total_amount": 100.0, "products": []}]

    async def build_product_cost(*_args, **_kwargs):
        return product_cost()

    async def build_recurring(*_args, **_kwargs):
        return recurring()

    monkeypatch.setattr(routes.legacy, "_filtered_orders", filtered_orders)
    monkeypatch.setattr(
        routes.legacy,
        "build_mezan_v2_product_cost",
        build_product_cost,
    )
    monkeypatch.setattr(
        routes.legacy,
        "compute_recurring_obligations_for_range",
        build_recurring,
    )
    monkeypatch.setattr(
        routes.legacy,
        "merge_ad_bank_fees_into_dashboard",
        lambda *_args, **_kwargs: None,
    )


def route_endpoint(router, path):
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == path
    )


def build_router(monkeypatch, db):
    install_fast_fakes(monkeypatch)

    async def current_user(_request):
        return {"id": "owner-1"}

    async def legacy_dashboard(**_kwargs):
        return dashboard_response()

    return routes.make_dashboard_v2_router(
        db,
        current_user,
        legacy_dashboard,
        lambda _user: None,
    )


def test_core_dashboard_never_waits_for_optional_ads(monkeypatch):
    db = FakeDb()
    ads_calls = 0

    async def must_not_run(*_args, **_kwargs):
        nonlocal ads_calls
        ads_calls += 1
        await asyncio.sleep(3600)

    monkeypatch.setattr(routes.legacy, "build_mezan_v2_ads", must_not_run)
    router = build_router(monkeypatch, db)
    endpoint = route_endpoint(router, "/dashboard-v2")
    response = Response()

    payload = asyncio.run(endpoint(
        request=request(),
        response_header=response,
        from_date=None,
        to_date=None,
        payment_methods=None,
        shipping_companies=None,
        user={"id": "owner-1"},
    ))

    assert ads_calls == 0
    assert payload["totals"]["total_orders"] == 1
    assert payload["totals"]["total_sales"] == 100.0
    assert payload["product_cost_v2"]["total"] == 20.0
    assert payload["ads_v2"]["available"] is False
    assert payload["optional_sources"]["advertising"]["status"] == "cache_miss"
    assert payload["stage_timings_ms"]["ads_ms"] == 0.0
    assert payload["stage_timings_ms"]["salla_ms"] == 0.0
    assert payload["request_id"] == "regression-request-id"
    assert response.headers["x-request-id"] == "regression-request-id"
    assert "auth;dur=" in response.headers["server-timing"]
    assert "ads;dur=0.00" in response.headers["server-timing"]


def test_core_dashboard_uses_last_good_ads_snapshot_without_refreshing_it(monkeypatch):
    last_success_at = datetime.now(timezone.utc).isoformat()
    cached_ads = {
        "total": 25.0,
        "breakdown": {"meta": 25.0},
        "history": [{"date": "2026-08-17", "meta": 25.0}],
        "providers": {
            "meta": {"spend": 25.0, "orders": 2, "revenue": 100.0, "roas": 4.0},
            "tiktok": {"spend": 0.0, "orders": 0, "revenue": 0.0, "roas": 0.0},
        },
        "source_contract": {"meta": "test"},
    }
    db = FakeDb({
        "ads_v2": cached_ads,
        "last_success_at": last_success_at,
    })
    router = build_router(monkeypatch, db)
    endpoint = route_endpoint(router, "/dashboard-v2")

    payload = asyncio.run(endpoint(
        request=request(),
        response_header=Response(),
        from_date=None,
        to_date=None,
        payment_methods=None,
        shipping_companies=None,
        user={"id": "owner-1"},
    ))

    assert payload["ads_v2"]["available"] is True
    assert payload["ads_v2"]["stale"] is True
    assert payload["ads_v2"]["last_success_at"] == last_success_at
    assert payload["totals"]["total_ads_cost"] == 25.0
    assert payload["optional_sources"]["advertising"]["status"] == "last_good_cache"


def test_optional_ads_endpoint_refreshes_cache_independently(monkeypatch):
    db = FakeDb()
    router = build_router(monkeypatch, db)

    async def build_ads(*_args, **_kwargs):
        return {
            "total": 25.0,
            "breakdown": {"meta": 25.0},
            "history": [],
            "providers": {},
            "source_contract": {"meta": "test"},
        }

    monkeypatch.setattr(routes.legacy, "build_mezan_v2_ads", build_ads)
    monkeypatch.setattr(
        routes.legacy,
        "build_salla_ads_executive_breakdown",
        lambda _orders, _ads: {"providers": {}, "total": {}},
    )
    endpoint = route_endpoint(router, "/dashboard-v2/optional-sources")
    response = Response()

    payload = asyncio.run(endpoint(
        request=request("/api/dashboard-v2/optional-sources"),
        response_header=response,
        from_date=None,
        to_date=None,
        payment_methods=None,
        shipping_companies=None,
        user={"id": "owner-1"},
    ))

    assert payload["ads_v2"]["available"] is True
    assert payload["ads_v2"]["stale"] is False
    assert payload["ads_v2"]["total"] == 25.0
    assert db.collection.updates
    query, update, upsert = db.collection.updates[0]
    assert query["user_id"] == "owner-1"
    assert update["$set"]["ads_v2"]["total"] == 25.0
    assert upsert is True
