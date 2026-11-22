"""Queued cards preserve financial output and enforce live actor/source fences."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
import pytest

import dashboard_card_read_models as models
import dashboard_card_builder as builders
import dashboard_v2_routes as dashboard
import demand_read_store as store
import legacy_dashboard_reader
from tests.test_dashboard_card_snapshots import card_app, PARAMS
from tests.test_dashboard_bundle_cost_authority import ReadOnlyDB


async def seed_owner(db):
    await db.users.insert_many([{"id": "a", "role": "owner"}, {"id": "b", "role": "owner"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["executive", "period-summary", "top-products"])
async def test_worker_card_matches_existing_response_without_canonical_writes(card_app, monkeypatch, section):
    env = card_app
    await seed_owner(env.db)
    expected = (await env.client.get("/dashboard-v2/cards/" + section, params=PARAMS)).json()
    expected.pop("snapshot")
    database = ReadOnlyDB(env.db)
    calls = []

    async def legacy(**options):
        calls.append(options)
        assert options["database"] is database and options["owner_id"] == "a"
        assert options["allow_self_heal"] is options["persist_settings"] is False
        return {"totals": {"total_orders": 1, "total_sales": 100, "total_product_cost": 0,
                "total_ads_cost": 0, "operating_expenses_total": 0, "operating_salaries_total": 0,
                "total_payment_fees": 0, "net_profit": 100, "net_sales": 100},
                "payment_breakdown": [], "net_sales_config": {}, "recent_analyses": ["must_be_hidden"]}

    monkeypatch.setattr(legacy_dashboard_reader, "dashboard", legacy)
    before = {name: await env.db[name].find({}).to_list(None) for name in await env.db.list_collection_names()}
    spec = await models.make_spec(database, user={"id": "a", "role": "owner"}, section=section, filters=PARAMS)
    assert await models.authorize_spec(database, spec)
    result = await models.build_projection(database, spec, AsyncMock())
    assert result["rows"] is None and jsonable_encoder(result["header"]) == expected
    assert bool(calls) is (section == "executive")
    assert before == {name: await env.db[name].find({}).to_list(None) for name in before}


@pytest.mark.asyncio
async def test_readonly_order_filter_does_not_initialize_or_migrate_settings(card_app):
    db = card_app.db
    await db.settings.delete_one({"user_id": "a"})
    await dashboard._filtered_orders(ReadOnlyDB(db), "a", **PARAMS, persist_settings=False)
    assert await db.settings.find_one({"user_id": "a"}) is None
    legacy = {"user_id": "a", "payment_methods": [{"name": "manual", "commission_percent": 1}]}
    await db.settings.insert_one(deepcopy(legacy))
    await dashboard._filtered_orders(ReadOnlyDB(db), "a", **PARAMS, persist_settings=False)
    actual = await db.settings.find_one({"user_id": "a"}, {"_id": 0})
    assert actual == legacy


@pytest.mark.asyncio
async def test_demand_get_and_ready_get_never_invoke_heavy_builder(card_app, monkeypatch):
    env = card_app
    await seed_owner(env.db)
    heavy = AsyncMock(side_effect=AssertionError("HTTP must not compute a card"))
    monkeypatch.setattr(builders, "build_dashboard_card", heavy)
    calls = []
    ready = None

    async def demand(db, spec, consumer_id, **options):
        calls.append((deepcopy(spec), consumer_id, options))
        return {"status": "ready" if ready else "preparing", "key": store.projection_key(spec)}

    async def read_ready(db, spec):
        return ready

    monkeypatch.setattr(store, "demand", demand)
    monkeypatch.setattr(store, "read_ready", read_ready)
    params = {**PARAMS, "demand": "true", "refresh": "true"}
    headers = {"X-Mezan-Read-Consumer": "test-consumer-123"}
    first = await env.client.get("/dashboard-v2/cards/top-products", params=params, headers=headers)
    assert first.status_code == 202 and first.json()["status"] == "preparing"
    assert first.json()["release_url"].startswith("/dashboard-v2/read-demand/")
    ready = {"header": {"product_cost_v2": {"total": 10}}, "items": None, "total": 0,
             "generated_at": datetime(2026, 11, 22, tzinfo=timezone.utc), "snapshot": "generation-one"}
    second = await env.client.get("/dashboard-v2/cards/top-products", params=params,
                                  headers={**headers, "X-Mezan-Read-Poll": "1"})
    assert second.status_code == 200 and second.json()["product_cost_v2"]["total"] == 10
    assert second.json()["snapshot"]["kind"] == "dated_display"
    assert calls[0][2]["refresh"] is True and calls[1][2]["refresh"] is False
    assert calls[0][0] == calls[1][0]
    heavy.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_fee_edit_or_page_revocation_rejects_previously_queued_card(card_app):
    db = card_app.db
    await seed_owner(db)
    await db.users.insert_one({"id": "staff", "role": "employee", "created_by": "a",
        "web_page_access": {"owner_id": "a", "grants": [{"id": "home.summary", "mode": "read"}], "revision": 1}})
    spec = await models.make_spec(db, user={"id": "staff"}, section="executive", filters=PARAMS)
    assert spec["owner_id"] == "a" and spec["actor_id"] == "staff"
    assert await models.authorize_spec(db, spec)
    await db.settings.update_one({"user_id": "a"}, {"$set": {"payment_methods.0.fixed_fee": 2}})
    with pytest.raises(HTTPException) as changed:
        await models.authorize_spec(db, spec)
    assert changed.value.status_code == 409
    current = await models.make_spec(db, user={"id": "staff"}, section="executive", filters=PARAMS)
    await db.users.update_one({"id": "staff"}, {"$set": {"web_page_access.grants": []}})
    with pytest.raises(HTTPException) as denied:
        await models.authorize_spec(db, current)
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_stored_private_grants_and_disabled_owner_cannot_authorize_snapshot(card_app):
    db = card_app.db
    await seed_owner(db)
    await db.users.insert_one({"id": "staff", "role": "employee", "created_by": "a",
                               "_web_workspace_read": ["home.summary", "a"]})
    with pytest.raises(HTTPException) as denied:
        await models.make_spec(db, user={"id": "staff", "_web_workspace_read": ["home.summary", "a"]},
                               section="top-products", filters=PARAMS)
    assert denied.value.status_code == 403
    spec = await models.make_spec(db, user={"id": "a"}, section="top-products", filters=PARAMS)
    await db.users.update_one({"id": "a"}, {"$set": {"disabled": True}})
    with pytest.raises(HTTPException) as disabled:
        await models.authorize_spec(db, spec)
    assert disabled.value.status_code == 403
