"""Current driver work follows Salla state without rewriting delivery history."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

import store_delivery_driver_app_routes as routes
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS, ORDERS


ACTOR = {
    "id": "driver-account", "role": "store_driver", "created_by": "merchant",
    "_session_client": "amasi_mobile",
}
DRIVER = {
    "id": "driver", "user_id": "merchant", "account_user_id": "driver-account",
    "status": "active", "name": "Test driver",
}


def order(number, *, slug="delivering", name="جاري التوصيل", **extra):
    return {
        "user_id": "merchant", "order_id": f"internal-{number}", "order_number": number,
        "order_status": name, "order_status_slug": slug,
        "raw_by_source": {"salla_direct": {"status": {"slug": slug, "name": name}}},
        "remaining_amount": 0, "paid_amount": 100, "total_amount": 100,
        "shipping_district": "Current district", **extra,
    }


def assignment(number, *, status="out_for_delivery", **extra):
    return {
        "id": f"assignment-{number}", "user_id": "merchant", "driver_id": "driver",
        "order_id": f"internal-{number}", "order_number": number, "barcode": f"BAR-{number}",
        "status": status, "active": True, "salla_status_slug": "delivering",
        "assigned_at": "2026-10-01T00:00:00+00:00", **extra,
    }


def setup(monkeypatch, *, target_order=None, target_assignment=None):
    db = AsyncMongoMockClient().test_driver_current_status
    async def seed():
        await db[STORE_DRIVERS].insert_one(deepcopy(DRIVER))
        await db[ORDERS].insert_many([
            deepcopy(target_order if target_order is not None else order("target", slug="shipped", name="تم الشحن")),
            order("current"), order("history", slug="delivered", name="تم التوصيل"),
            order("other-driver"), order("other-merchant", user_id="another-merchant"),
        ])
        await db[ASSIGNMENTS].insert_many([
            deepcopy(target_assignment or assignment("target")),
            assignment("current"), assignment("history", status="delivered"),
            assignment("other-driver", driver_id="another-driver"),
            assignment("other-merchant", user_id="another-merchant"),
        ])
        await db[routes.DRIVER_COLLECTIONS].insert_one({
            "id": "collection", "user_id": "merchant", "driver_id": "driver",
            "assignment_id": "assignment-history", "amount": 100, "order_number": "history",
        })
        await db[routes.DRIVER_EARNINGS].insert_one({
            "id": "earning", "user_id": "merchant", "driver_id": "driver",
            "assignment_id": "assignment-history", "amount": 20,
        })
    asyncio.run(seed())
    provider = AsyncMock(side_effect=AssertionError("List reads must not call Salla per order"))
    monkeypatch.setattr(routes, "_call_salla", provider)
    router = routes.make_store_delivery_driver_app_router(db, lambda: ACTOR)
    app = FastAPI()
    app.include_router(router)
    return db, TestClient(app), router, provider


def ids(items):
    return {item["id"] for item in items}


@pytest.mark.parametrize("slug,name", [
    ("shipped", "تم الشحن"), ("delivered", "تم التوصيل"),
    ("completed", "تم التنفيذ"), ("in_progress", "قيد التنفيذ"),
    ("under_review", "بانتظار المراجعة"), ("under_review", "تم المراجعة"),
    ("canceled", "ملغي"), ("cancelled", "ملغى"), ("restored", "مسترجع"),
    ("restoring", "قيد الاسترجاع"), ("payment_pending", "بانتظار الدفع"),
    ("deleted", "محذوف"), ("request_quote", "طلب عرض سعر"),
    ("custom-status", "حالة مخصصة"), ("", ""),
])
def test_non_delivering_order_is_absent_from_home_search_and_current_list(monkeypatch, slug, name):
    _, client, _, provider = setup(monkeypatch, target_order=order("target", slug=slug, name=name))
    home = client.get("/store-delivery/app/deliveries/home")
    assert home.status_code == 200
    assert ids(home.json()["items"]) == {"assignment-current"}
    assert home.json()["total"] == 1
    assert home.json()["out_for_delivery_count"] == 1
    assert home.json()["districts"] == [{"district": "Current district", "count": 1}]
    listed = client.get("/store-delivery/app/deliveries").json()
    assert ids(listed["items"]) == {"assignment-current", "assignment-history"}
    assert listed["total"] == 2
    for query in ("target", "BAR-target", "internal-target"):
        response = client.get(f"/store-delivery/app/deliveries/search/{query}")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "driver_assignment_not_found"
    provider.assert_not_awaited()


@pytest.mark.parametrize("patch", [
    {},
    {"order_status_slug": "shipping"},
    {"order_status_slug": "out_for_delivery"},
    {"raw_by_source": {}},
    {"order_status": "", "order_status_slug": ""},
    {"raw_by_source": {"salla_direct": {"status": {
        "slug": "delivering", "name": "جاري التوصيل",
        "customized": {"name": "جاري التوصيل"},
        "parent": {"slug": "completed", "name": "تم التنفيذ"},
    }}}},
])
def test_current_delivery_remains_visible_and_searchable(monkeypatch, patch):
    _, client, _, _ = setup(monkeypatch, target_order=order("target", **patch))
    assert ids(client.get("/store-delivery/app/deliveries/home").json()["items"]) == {
        "assignment-current", "assignment-target",
    }
    assert client.get("/store-delivery/app/deliveries/search/target").json()["item"]["id"] == "assignment-target"


@pytest.mark.parametrize("patch", [
    {"order_status": "تم الشحن"},
    {"order_status_slug": "shipped"},
    {"raw_by_source": {"salla_direct": {"status": {"slug": "shipped", "name": "تم الشحن"}}}},
    {"raw_by_source": {"salla_direct": {"status_slug": "shipped"}}},
    {"raw_by_source": {"salla_direct": {"status": {"slug": "delivering", "customized": {"name": "معلّق"}}}}},
    {"raw_by_source": {"salla_direct": {"status": {"slug": "delivering", "customized": {"label": "معلّق"}}}}},
    {"raw_by_source": {"salla_direct": {"status": {"slug": "delivering", "customized": {"title": "معلّق"}}}}},
    {"raw_by_source": {"salla_direct": {"status": {"slug": "delivering", "customized": {"id": 42}}}}},
    {"order_status": "", "order_status_slug": "shipping", "raw_by_source": {}},
    {"g47_salla_snapshot": {"requires_authoritative_refresh": True}},
])
def test_conflicting_or_unverified_status_cannot_be_rescued_by_old_assignment(monkeypatch, patch):
    _, client, _, _ = setup(monkeypatch, target_order=order("target", **patch))
    assert "assignment-target" not in ids(client.get("/store-delivery/app/deliveries/home").json()["items"])


def test_missing_or_ambiguous_canonical_order_is_hidden(monkeypatch):
    db, client, _, _ = setup(monkeypatch)
    asyncio.run(db[ORDERS].delete_one({"order_number": "target"}))
    assert client.get("/store-delivery/app/deliveries/search/target").status_code == 404
    async def add_conflicting_identity():
        await db[ORDERS].insert_many([
            order("different-reference", order_id="internal-target"),
            order("target", order_id="different-internal"),
        ])
    asyncio.run(add_conflicting_identity())
    assert client.get("/store-delivery/app/deliveries/search/target").status_code == 404


@pytest.mark.parametrize("patch", [
    {"order_id": None},
    {"order_number": None},
    {"order_id": None, "raw_by_source": {"salla_direct": {"id": "internal-target"}}},
])
def test_unique_sparse_canonical_identity_is_not_a_conflict(monkeypatch, patch):
    _, client, _, _ = setup(monkeypatch, target_order=order("target", **patch))
    response = client.get("/store-delivery/app/deliveries/search/target")
    assert response.status_code == 200
    assert response.json()["item"]["id"] == "assignment-target"


@pytest.mark.parametrize("patch", [
    {"order_number": "different-reference"},
    {"order_id": "different-internal"},
    {"order_id": None, "raw_by_source": {"salla_direct": {"id": "different-internal"}}},
])
def test_explicit_canonical_identity_conflict_is_hidden(monkeypatch, patch):
    _, client, _, _ = setup(monkeypatch, target_order=order("target", **patch))
    assert client.get("/store-delivery/app/deliveries/search/target").status_code == 404


def test_read_filter_preserves_assignments_history_and_financial_records(monkeypatch):
    db, client, router, provider = setup(monkeypatch)
    async def snapshot():
        return {name: await db[name].find({}).to_list(None) for name in (
            ORDERS, ASSIGNMENTS, routes.DRIVER_COLLECTIONS, routes.DRIVER_EARNINGS,
        )}
    before = asyncio.run(snapshot())
    summary = client.get("/store-delivery/app/accounts/summary").json()
    assert summary["delivery_counts"] == {"assigned": 0, "out_for_delivery": 1, "delivered": 1}
    assert summary["earnings_total"] == summary["earnings_due"] == 20
    assert client.get("/store-delivery/app/deliveries/report").json()["items"][0]["amount"] == 100
    assert client.get("/store-delivery/app/deliveries/search/history").status_code == 404
    assert client.get("/store-delivery/app/deliveries/search/other-driver").status_code == 404
    assert client.get("/store-delivery/app/deliveries/search/other-merchant").status_code == 404
    # Exercise every registration, including the legacy duplicated native routes.
    for route in router.routes:
        if route.path.endswith("/deliveries/home"):
            assert ids(asyncio.run(route.endpoint(user=ACTOR))["items"]) == {"assignment-current"}
        if route.path.endswith("/deliveries/search/{query}"):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as caught:
                asyncio.run(route.endpoint(query="target", user=ACTOR))
            assert caught.value.status_code == 404
    assert asyncio.run(snapshot()) == before
    provider.assert_not_awaited()


def test_initial_assignment_does_not_bypass_current_status_rule(monkeypatch):
    _, client, _, _ = setup(monkeypatch, target_assignment=assignment("target", status="assigned"))
    response = client.get("/store-delivery/app/deliveries/home").json()
    assert response["assigned_count"] == 0
    assert "assignment-target" not in ids(response["items"])
