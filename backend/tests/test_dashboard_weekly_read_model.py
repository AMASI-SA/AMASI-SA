"""Accepted fee knowledge restates saved cards without HTTP-side computation."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest
import pytest_asyncio

import dashboard_card_builder as builders
import dashboard_card_read_models as models
import dashboard_payment_fees as fees
import dashboard_v2_routes as dashboard
import demand_read_store as store
import legacy_dashboard_reader
from accounting_onboarding_ssot import POLICIES
from operational_provider_statement_fees import derive_statement_average_profile
from provider_fee_weekly_sources import STATES, STATEMENTS
from tests.test_dashboard_bundle_cost_authority import ReadOnlyDB
from tests.test_dashboard_card_snapshots import card_app
from tests.test_dashboard_weekly_payment_fees import tabby_policies
from tests.test_provider_fee_weekly_average import sale, source


FILTERS = {"from_date": "2026-11-11", "to_date": "2026-11-11",
           "payment_methods": "tabby", "shipping_companies": "carrier-a", "report_statuses": "default"}
KNOWLEDGE = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def weekly_card(card_app, monkeypatch):
    env = card_app
    env.state["today"] = date(2026, 11, 22)
    await env.db.users.insert_many([{"id": owner, "role": "owner"} for owner in ("a", "b")])
    await env.db[POLICIES].insert_one({"user_id": "a", "policies": tabby_policies()})
    await env.db[STATES].insert_many([
        {"_id": owner, "owner_id": owner, "status": "active", "revision": 1,
         "started_at": "2026-11-01T00:00:00+03:00", "provider_statement_reconciliation": {}}
        for owner in ("a", "b")
    ])
    await env.db.unified_orders.update_one({"user_id": "a", "order_number": "selected"}, {"$set": {
        "payment_method": "tabby", "total_amount": 150, "products.0.total": 150,
        "raw_by_source.salla_direct": {
            "created_at": "2026-11-11T08:00:00+03:00", "updated_at": "2026-11-11T08:01:00+03:00",
            "currency": "SAR", "payment_method": "tabby",
            "payment_methods": [{"payment_method": "tabby", "amount": 150,
                                 "transaction_reference": "paid-native-reference"}],
            "payment_actions": {"refund_action": {"paid_amount": {"amount": 150, "currency": "SAR"},
                "refund_amount": {"amount": 0, "currency": "SAR"}}},
        },
    }})

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return KNOWLEDGE.astimezone(tz) if tz else KNOWLEDGE.replace(tzinfo=None)

    monkeypatch.setattr(dashboard, "datetime", FixedClock)
    calls, contexts = Counter(), []
    original_average, original_actual = fees.load_weekly_statement_averages, fees.load_operational_statement_fee_index
    original_legacy = legacy_dashboard_reader.dashboard

    async def average(*args, **options):
        calls["weekly"] += 1
        return await original_average(*args, **options)

    async def actual(*args, **options):
        calls["actual"] += 1
        return await original_actual(*args, **options)

    async def legacy(**options):
        contexts.append(options["fee_context"])
        assert options["persist_settings"] is options["allow_self_heal"] is False
        return await original_legacy(**options)

    monkeypatch.setattr(fees, "load_weekly_statement_averages", average)
    monkeypatch.setattr(fees, "load_operational_statement_fee_index", actual)
    monkeypatch.setattr(legacy_dashboard_reader, "dashboard", legacy)
    return SimpleNamespace(env=env, db=ReadOnlyDB(env.db), calls=calls, contexts=contexts)


async def accept(weekly_card, rows):
    db = weekly_card.env.db
    document = source("late-accepted", rows, owner="a", uploaded="2026-11-22T08:00:00+03:00")
    proof = document.pop("accepted_summary")
    profile = derive_statement_average_profile({"owner_id": "a"}, document["statement"],
        statement_id=document["id"], accepted_at=document["uploaded_at"])
    await db[STATEMENTS].insert_one({**document, "metadata": {"row_count": len(rows)}})
    await db[STATES].update_one({"_id": "a"}, {"$inc": {"revision": 1}, "$set": {
        "engine.as_of": "2026-11-22T11:00:00+03:00",
        "engine.provider_fee_inputs.average_profiles": [profile],
        "provider_statement_reconciliation": {
            "statement_summaries": {document["id"]: {"statement_average": proof}},
            "average_profiles": {profile["scope_key"]: profile},
        },
    }})
    return profile


async def spec(weekly_card):
    return await models.make_spec(weekly_card.db, user={"id": "a", "role": "owner"},
                                  section="executive", filters=FILTERS)


@pytest.mark.asyncio
async def test_accepted_upload_invalidates_persistent_demand_card_and_restates_past_report(weekly_card, monkeypatch):
    import background_worker_process

    fixture = weekly_card
    before_spec = await spec(fixture)
    before = await models.build_projection(fixture.db, before_spec, AsyncMock())
    assert before["header"]["totals"]["payment_fees_complete"] is False
    assert before["header"]["totals"]["net_profit"] is None
    previous_key = store.projection_key(before_spec)
    await fixture.env.db[store.JOBS].insert_one({"_id": previous_key, "header": before["header"],
        "generated_at": KNOWLEDGE, "status": "ready"})

    # Exercise the demand HTTP boundary with an already persisted result.
    # Store scheduling is covered independently against a real Mongo replica.
    async def demand(_db, value, *_args, **_options):
        key = store.projection_key(value)
        saved = await fixture.env.db[store.JOBS].find_one({"_id": key})
        return {"key": key, "status": "ready" if saved else "preparing"}

    async def ready(_db, value):
        return await fixture.env.db[store.JOBS].find_one({"_id": store.projection_key(value)})

    monkeypatch.setattr(store, "demand", demand)
    monkeypatch.setattr(store, "read_ready", ready)
    monkeypatch.setattr(background_worker_process, "wake_read_projections", AsyncMock())
    params = {**FILTERS, "demand": "true"}
    path = "/dashboard-v2/cards/executive"
    cached = await fixture.env.client.get(path, params=params)
    assert cached.status_code == 200 and cached.json()["totals"]["net_profit"] is None
    assert fixture.calls == {"weekly": 1, "actual": 1}

    await fixture.env.db[STATES].update_one({"_id": "b"}, {"$inc": {"revision": 1}})
    assert await spec(fixture) == before_spec
    await accept(fixture, [
        sale("outside-week", "2026-11-03", "1000.00", "200.00", "1.00", "30.15"),
        sale("sample-one", "2026-11-04", "100.00", "10.00", "1.00", "1.65"),
        sale("sample-two", "2026-11-10", "300.00", "15.00", "2.00", "2.55"),
    ])
    changed_spec = await spec(fixture)
    assert changed_spec["source_revision"] != before_spec["source_revision"]
    assert store.projection_key(changed_spec) != previous_key
    with pytest.raises(HTTPException) as stale:
        await models.authorize_spec(fixture.db, before_spec)
    assert stale.value.status_code == 409 and stale.value.detail["code"] == "read_source_changed"

    pending = await fixture.env.client.get(path, params=params)
    assert pending.status_code == 202  # The obsolete persistent card is never served.
    assert pending.json()["key"] == store.projection_key(changed_spec)
    assert fixture.calls == {"weekly": 1, "actual": 1}  # HTTP only checks the compact source revision.
    restated = (await models.build_projection(fixture.db, changed_spec, AsyncMock()))["header"]
    assert restated["totals"]["payment_fees_complete"] is True
    assert restated["totals"]["total_payment_fees"] == 12.51
    assert restated["totals"]["net_profit"] is not None
    row = next(row for row in restated["payment_breakdown"] if row["key"] == "tabby")
    assert row["fees_estimated"] is True and row["estimated_orders_count"] == 1
    evidence = row["fee_estimate_sources"][0]
    assert evidence["basis"] == "weekly_statement_average"
    assert (evidence["window_start"], evidence["window_end"], evidence["eligible_sale_rows"]) == (
        "2026-11-04", "2026-11-10", 2)
    assert restated["payment_method_setup"]["fee_revision"] == before["header"]["payment_method_setup"]["fee_revision"]
    assert "weekly_statement_average" in restated["source_contract"]["payment_gateway_fees"]
    assert fixture.calls == {"weekly": 2, "actual": 2}


@pytest.mark.asyncio
async def test_empty_historical_week_uses_exact_saved_operational_period(weekly_card):
    profile = await accept(weekly_card, [
        sale("earlier-one", "2026-11-01", "100.00", "10.00", "1.00", "1.65"),
        sale("earlier-two", "2026-11-03", "300.00", "15.00", "2.00", "2.55"),
    ])
    original = await weekly_card.env.db[STATES].find_one({"_id": "a"})
    result = (await models.build_projection(weekly_card.db, await spec(weekly_card), AsyncMock()))["header"]
    assert result["totals"]["payment_fees_complete"] is True
    assert result["totals"]["total_payment_fees"] == 12.51
    row = next(row for row in result["payment_breakdown"] if row["key"] == "tabby")
    assert row["fees_estimated"] is True and row["estimated_orders_count"] == 1
    evidence = row["fee_estimate_sources"][0]
    assert evidence["basis"] == "operational_balance_average"
    assert evidence["rate_percent"] == profile["rate_percent"] == "6.25000000"
    assert evidence["period_start"] == original["started_at"]
    assert evidence["period_end"] == original["engine"]["as_of"]
    assert await weekly_card.env.db[STATES].find_one({"_id": "a"}) == original
    assert weekly_card.calls == {"weekly": 1, "actual": 1}


@pytest.mark.asyncio
async def test_worker_fee_memo_is_shared_per_computation_and_never_enters_binding_or_result(weekly_card, monkeypatch):
    await accept(weekly_card, [sale("sample", "2026-11-10")])
    fixture = weekly_card
    binding = await builders.prepare_dashboard_card(fixture.db, owner_id="a", section="executive", **FILTERS)
    original = deepcopy(binding)
    value = await spec(fixture)
    # Reusing the same prepared binding must still allocate private task state
    # for each worker computation instead of mutating the saved source context.
    monkeypatch.setattr(models, "prepare_dashboard_card", AsyncMock(return_value=binding))
    first = await models.build_projection(fixture.db, value, AsyncMock())
    assert fixture.calls == {"weekly": 1, "actual": 1}
    legacy_context = fixture.contexts[0]
    collector_context = fixture.env.state["fee_contexts"][0][1]
    assert legacy_context is collector_context
    assert legacy_context["evidence_as_of"] == KNOWLEDGE
    memo = legacy_context["_request_fee_reads"]
    assert len(memo) == 2 and all(task.done() for task in memo.values())
    assert binding == original
    assert "_request_fee_reads" not in repr(first) and "_request_fee_reads" not in repr(value)
    assert "evidence_as_of" not in binding["calculation_fee_context"]

    second = await models.build_projection(fixture.db, value, AsyncMock())
    assert fixture.calls == {"weekly": 2, "actual": 2}
    assert fixture.contexts[1]["_request_fee_reads"] is not memo
    assert second == first
