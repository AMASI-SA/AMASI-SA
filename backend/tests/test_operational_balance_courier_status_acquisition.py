"""Status-only admission never strands an existing dated or paid delivery fact."""
import asyncio
from copy import deepcopy
from datetime import datetime

from mongomock_motor import AsyncMongoMockClient
import pytest

from operational_balance_archive import compact_financial_snapshot
from operational_balance_store import STATES, initial
from operational_courier_transitions import COMPLETED_PROOF, WEBHOOK_PROOF
from operational_delivery_confirmation import EVENT_SCHEMA
from operational_delivery_evidence import STATUS_SCHEMA
from operational_demand_refresh import run_visible_refresh
from operational_visible_evidence import acquire_visible_evidence
from salla_integration import courier_delivery_sync as sync
from salla_shipping import CURRENT_SHIPPING
from test_operational_balance_courier_acquisition import native_provider, page
from test_operational_balance_courier_waybill_acquisition import WAYBILL_GAP, binding_gap
from test_operational_balance_delivery_cutover import DELIVERED, NOW, START
from test_operational_balance_demand_refresh import Request, allowed
from test_operational_balance_prepaid_shipping import COMPLETED, fixture as completed_fixture


CREATED = "2026-10-05T01:00:00+00:00"


@pytest.fixture(autouse=True)
def acquisition_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.fromisoformat(NOW)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(sync, "datetime", Clock)


def history_state(kind):
    state = initial("owner")
    state.update(status="active", started_at=START, revision=1, movements=[])
    state["engine"] = {"facts": {}, "obligations": {}, "issues": [
        {"code": WAYBILL_GAP, "source_id": "100"}]}
    facts, obligations = state["engine"]["facts"], state["engine"]["obligations"]
    if kind == "dated-event":
        facts["delivery_event:100"] = {"schema": "operational_delivery_event_v1", "delivered_at": DELIVERED}
    elif kind == "unpriced-shipping":
        facts["shipping:100"] = {"delivered_at": DELIVERED, "cost": None}
    elif kind == "recorded-zero-cost":
        facts["shipping:100"] = {"cost": "0.00"}
    elif kind == "legacy-fee-fact":
        facts["shipping_fee:100"] = {"cost": "10.00"}
    elif kind == "component-fact":
        facts["shipping:100:base_shipping"] = {"cost": "10.00"}
    elif kind in {"confirmed-obligation", "allocated-obligation", "unpaid-obligation"}:
        key = "shipping:100:base_shipping"
        obligations[key] = {"kind": "shipping", "confirmed": "10.00" if kind == "confirmed-obligation" else "0.00"}
        if kind == "allocated-obligation":
            state["movements"] = [{"id": "paid-fee", "amount": "4.00", "audit_detail": "not needed",
                "allocations": [{"obligation_id": key, "amount": "4.00"}]}]
    return state


async def prepared(state, source, *, packed):
    # The actual in-memory Mongo projection is intentional: the older state
    # fake ignores projections and would hide a missing obligations/allocations
    # field. No external Mongo service is used by these tests.
    db = AsyncMongoMockClient()["courier_status_acquisition"]
    stored = compact_financial_snapshot(state, 100000) if packed else state
    await db[STATES].insert_one(deepcopy(stored))
    await db.unified_orders.insert_one(deepcopy(source))
    return db


async def visible(db):
    return await run_visible_refresh(Request(), db, "owner", "owner", allowed,
        lambda: acquire_visible_evidence(db, "owner", courier_order_id="100"))


@pytest.mark.parametrize("packed", [False, True], ids=["inline", "packed"])
@pytest.mark.parametrize("kind", [
    "dated-event", "unpriced-shipping", "recorded-zero-cost", "legacy-fee-fact", "component-fact",
    "confirmed-obligation", "allocated-obligation",
])
def test_existing_dated_or_recorded_fee_keeps_native_recovery(packed, kind, monkeypatch):
    _, source, shipment, histories = binding_gap()
    source["raw_by_source"]["salla_direct"]["created_at"] = CREATED
    assert WEBHOOK_PROOF not in source[CURRENT_SHIPPING]
    calls = []
    native_provider(monkeypatch, {"100": (shipment, histories)}, calls)

    async def scenario():
        db = await prepared(history_state(kind), source, packed=packed)
        financial = await db[STATES].find_one({"_id": "owner"})
        result = await visible(db)
        assert calls == [("100", "/orders/100/histories"), ("100", "/shipments")]
        assert result["courier"]["saved"] == result["courier"]["verified"] == 1
        assert result["courier"]["outcomes"] == [{"order_id": "100", "status": "saved"}]
        saved = await db.unified_orders.find_one({"user_id": "owner"})
        assert saved[CURRENT_SHIPPING][WEBHOOK_PROOF]["at"] == DELIVERED
        assert saved["raw_by_source"] == source["raw_by_source"]
        assert saved["g47_salla_snapshot"] == source["g47_salla_snapshot"]
        assert await db[STATES].find_one({"_id": "owner"}) == financial
        assert (await visible(db))["courier"]["selected"] == 0
        assert len(calls) == 2  # The existing retry fence still owns replay.
    asyncio.run(scenario())


@pytest.mark.parametrize("packed", [False, True], ids=["inline", "packed"])
@pytest.mark.parametrize("kind", ["new", "unpaid-obligation", STATUS_SCHEMA, EVENT_SCHEMA])
def test_new_or_prior_status_delivery_does_not_buy_optional_history(packed, kind, monkeypatch):
    _, source, _, _ = binding_gap()
    source["raw_by_source"]["salla_direct"]["created_at"] = CREATED
    state = history_state("allocated-obligation" if kind in {STATUS_SCHEMA, EVENT_SCHEMA} else kind)
    if kind in {STATUS_SCHEMA, EVENT_SCHEMA}:
        state["engine"]["facts"].update({
            "delivery_event:100": {"schema": kind, "delivered_at": None, "recorded_at": NOW},
            "shipping:100": {"delivered_at": None, "cost": "10.00"},
        })
    calls = []
    async def forbidden_provider(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Status-admitted delivery must not read optional native history")
    monkeypatch.setattr(sync, "call_salla", forbidden_provider)

    async def scenario():
        db = await prepared(state, source, packed=packed)
        financial = await db[STATES].find_one({"_id": "owner"})
        result = await visible(db)
        assert result["courier"]["outcomes"] == [{"order_id": "100", "status": "already_eligible"}]
        assert result["courier"]["verified"] == result["courier"]["saved"] == 0 and not calls
        saved = await db.unified_orders.find_one({"user_id": "owner"})
        assert saved[CURRENT_SHIPPING] == source[CURRENT_SHIPPING]
        assert await db[STATES].find_one({"_id": "owner"}) == financial
    asyncio.run(scenario())


@pytest.mark.parametrize("frozen", [False, True])
def test_poststart_delivered_status_never_replaces_separate_completion_proof(frozen, monkeypatch):
    data, _ = completed_fixture(old=False, current="delivered", at=None)
    source = data["unified_orders"][0]
    state = history_state("dated-event" if frozen else "new")
    state["engine"]["issues"] = [{"source_id": "100",
        "code": "operational_prepaid_shipping_completion_timestamp_missing"}]
    calls = []
    async def provider(db, owner, method, path, *, params):
        calls.append((owner, method, path))
        return page([{"id": "completed-1", "order_id": "100", "to_status": "completed",
                      "created_at": COMPLETED}])
    monkeypatch.setattr(sync, "call_salla", provider)

    async def scenario():
        db = await prepared(state, source, packed=True)
        financial = await db[STATES].find_one({"_id": "owner"})
        result = await visible(db)
        assert calls == [("owner", "GET", "/orders/100/histories")]
        assert result["courier"]["outcomes"] == [{"order_id": "100", "status": "saved"}]
        saved = await db.unified_orders.find_one({"user_id": "owner"})
        assert saved[CURRENT_SHIPPING][COMPLETED_PROOF]["at"] == COMPLETED
        assert WEBHOOK_PROOF not in saved[CURRENT_SHIPPING]
        assert await db[STATES].find_one({"_id": "owner"}) == financial
    asyncio.run(scenario())
