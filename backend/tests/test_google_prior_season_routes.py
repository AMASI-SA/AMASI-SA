from copy import deepcopy
from datetime import datetime
import httpx
import pytest
from fastapi import APIRouter, FastAPI

from integrations_control_center.google_commercial_plan import PLANS, attach_google_commercial_plan_routes
from integrations_control_center.google_prior_season_routes import attach_google_prior_season_routes
from tests.test_google_commercial_plan import Collection, body
from tests.test_google_plan_store_observations import Orders, Users
from tests.test_google_prior_season_context import context
from tests.test_google_prior_season_observations import NOW, prior_order


class Plans(Collection):
    def __init__(self):
        super().__init__()
        self.calls, self.mutate = 0, None
    async def find_one(self, query, projection=None):
        self.calls += 1
        if self.calls == 3 and self.mutate:
            self.mutate(self.rows[0])
        return await super().find_one(query, projection)


class DB:
    def __init__(self):
        self.plans, self.orders, self.users = Plans(), Orders([prior_order()]), Users()
    def __getitem__(self, name):
        return {PLANS: self.plans, "unified_orders": self.orders}[name]


def app_for(monkeypatch, db, identity=None):
    from integrations_control_center import google_commercial_plan as module
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    app, router = FastAPI(), APIRouter()
    async def current_user():
        return dict(identity) if identity is not None else {"id": "owner", "role": "owner"}
    attach_google_commercial_plan_routes(router, db, current_user)
    attach_google_prior_season_routes(router, db, current_user, clock=lambda: NOW)
    app.include_router(router)
    return app


def payload(prior=True, **changes):
    value = body(category_url=None, pinned_product_ids=["123"], excluded_product_ids=["456"], **changes).model_dump(mode="json")
    value["prior_season_context"] = context() if prior else None
    return value


async def get(client, revision=1):
    return await client.get(f"/google_ads/commercial-plans/winter/prior-season-observations?expected_revision={revision}")


@pytest.mark.asyncio
async def test_actual_http_save_then_prior_read_has_exact_prior_query_and_unknown_alignment(monkeypatch):
    db = DB()
    app = app_for(monkeypatch, db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put("/google_ads/commercial-plans/winter", json=payload())
        assert saved.status_code == 200
        assert "acknowledge_review" not in saved.json()["fields"]
        original = deepcopy(db.plans.rows)
        result = await get(client)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["calendar_alignment_verification"] == "unknown"
    assert data["owner_review_provenance"]["reviewed_at"] == NOW.isoformat()
    assert data["product_ids_reviewed"] == ["123"]
    assert data["observations"]["products"][0]["currency_groups"][0]["windows"]["owner_reviewed_prior_season"]["observed_units"] == "2"
    assert db.orders.query["order_date"] == {"$gte": "2025-10-31", "$lte": "2026-01-01"}
    assert "$or" not in db.orders.query
    assert data["growth"] is data["profitability"] is None and data["execution_authorized"] is False
    assert db.plans.rows == original


@pytest.mark.asyncio
async def test_legacy_no_prior_context_does_not_read_orders(monkeypatch):
    db, app = DB(), None
    app = app_for(monkeypatch, db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload(prior=False))).status_code == 200
        result = await get(client)
    assert result.status_code == 200 and result.json()["status"] == "prior_season_not_configured"
    assert result.json()["observations"] is None and db.orders.query is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", [lambda row: row.update(revision=2),
    lambda row: row["fields"]["prior_season_context"].update(alignment_note="تغير السياق"),
    lambda row: row.update(updated_by="foreign")])
async def test_prior_read_rejects_revision_fields_or_owner_review_provenance_race(monkeypatch, mutate):
    db = DB()
    app = app_for(monkeypatch, db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload())).status_code == 200
        db.plans.mutate = mutate
        result = await get(client)
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_stale_or_foreign_or_unclosed_interval_never_reads_orders(monkeypatch):
    db, identity = DB(), {"id": "owner", "role": "owner"}
    app = app_for(monkeypatch, db, identity)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload())).status_code == 200
        assert (await get(client, 2)).status_code == 409
        identity["id"] = "foreign"
        assert (await get(client)).status_code == 404
        assert db.orders.query is None
        identity["id"] = "owner"
        db.plans.rows[0]["fields"].update(starts_on="2027-01-01", ends_on="2027-02-01",
            prior_season_context=context(date_from="2026-11-01", date_to="2026-11-30"))
        result = await get(client)
    assert result.status_code == 409 and result.json()["detail"]["code"] == "google_prior_season_period_not_closed"
    assert db.orders.query is None


@pytest.mark.asyncio
async def test_truncated_prior_read_and_missing_index_never_publish_counts(monkeypatch):
    db = DB()
    app = app_for(monkeypatch, db)
    db.orders.rows = [prior_order() for _ in range(1001)]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/google_ads/commercial-plans/winter", json=payload())).status_code == 200
        result = await get(client)
        assert result.json()["observations"]["truncated"] is True
        assert result.json()["observations"]["products"][0]["currency_groups"] == []
        db.orders.fail = True
        failed = await get(client)
    assert failed.status_code == 503 and "sentinel" not in failed.text
