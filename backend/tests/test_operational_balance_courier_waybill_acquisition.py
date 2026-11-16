"""A saved waybill-binding gap uses the existing bounded native proof reader."""
import asyncio
from copy import deepcopy

import pytest

from operational_balance_store import STATES
from operational_courier_transitions import CARRIER_HISTORY, WEBHOOK_PROOF, native_status_transition
from operational_delivery_evidence import resolve_delivery_event
from operational_demand_refresh import run_visible_refresh
from operational_visible_evidence import acquire_visible_evidence
from salla_integration import courier_delivery_sync as sync
from salla_shipping import CURRENT_SHIPPING
from test_operational_balance_courier_acquisition import fixture, native_provider, page
from test_operational_balance_delivery_confirmation_acquisition import LocalProofDB
from test_operational_balance_delivery_cutover import DELIVERED, NOW, START
from test_operational_balance_demand_refresh import Request, allowed


WAYBILL_GAP = "operational_delivery_order_waybill_unproven"


def binding_gap(identity="100", number="10"):
    data, source, shipment, histories = fixture(identity, number)
    raw = source["raw_by_source"]["salla_direct"]
    raw["status_changed_at"] = DELIVERED
    raw["shipping"]["shipment"] = {"id": "stale-embedded-shipment"}
    return data, source, shipment, histories


def resolve(data, source):
    return resolve_delivery_event(source["raw_by_source"]["salla_direct"], source=source,
        owner="owner", assignments=[], setup=data["mz2_shipping_setup_v2"][0],
        started_at=START, as_of=NOW)


async def seed(db, source, *, issue=True):
    state = db.data[STATES]["owner"]
    state["started_at"] = START
    state.setdefault("engine", {}).setdefault("issues", [])
    if issue:
        state["engine"]["issues"].append({"code": WAYBILL_GAP, "source_id": source["order_id"]})
    await db.sources.unified_orders.insert_one(deepcopy(source))


async def visible(db):
    return await run_visible_refresh(Request(), db, "owner", "owner", allowed,
        lambda: acquire_visible_evidence(db, "owner"))


@pytest.mark.parametrize("generic_notice", [False, True], ids=["carrier-only", "generic-salla-notice"])
def test_visible_waybill_gap_recovers_bound_native_proof_without_financial_write(monkeypatch, generic_notice):
    data, source, shipment, histories = binding_gap()
    if generic_notice:
        notice = {"id": "generic-delivered-notice", "type": "activity", "status": "تم التوصيل",
                  "note": "تم تحديث حالة الطلب", "created_at": NOW,
                  "created_by": {"id": None, "name": "سلة"}}
        assert native_status_transition(notice, "100") is None
        histories.append(notice)
    with pytest.raises(ValueError, match="^" + WAYBILL_GAP + "$"):
        resolve(data, source)
    calls = []
    native_provider(monkeypatch, {"100": (shipment, histories)}, calls)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source)
        financial = deepcopy(db.data[STATES])
        result = await visible(db)
        assert result["courier_candidates"] == 1
        assert calls == [("100", "/orders/100/histories"), ("100", "/shipments")]
        saved = await db.sources.unified_orders.find_one({"user_id": "owner"})
        assert saved["raw_by_source"] == source["raw_by_source"]
        assert saved["g47_salla_snapshot"] == source["g47_salla_snapshot"]
        assert saved[CURRENT_SHIPPING][WEBHOOK_PROOF]["source"] == CARRIER_HISTORY
        assert resolve(data, saved)["delivered_at"] == DELIVERED
        assert db.data[STATES] == financial
    asyncio.run(scenario())


def test_waybill_targets_keep_four_order_limit_retry_fence_and_owner_scope(monkeypatch):
    snapshots, calls = {}, []
    native_provider(monkeypatch, snapshots, calls)

    async def scenario():
        db = LocalProofDB()
        foreign = []
        for index in range(6):
            identity = str(8100 + index)
            _, source, shipment, histories = binding_gap(identity, str(9100 + index))
            snapshots[identity] = shipment, histories
            await seed(db, source)
            other = {**deepcopy(source), "user_id": "other"}
            foreign.append(other)
            await seed(db, other, issue=False)
        financial = deepcopy(db.data[STATES])
        assert (await visible(db))["courier_candidates"] == 6
        assert len(calls) == 8
        assert len({identity for identity, _ in calls}) == 4
        await visible(db)
        assert len(calls) == len(set(calls)) == 12
        await visible(db)
        assert len(calls) == 12
        for original in foreign:
            saved = await db.sources.unified_orders.find_one(
                {"user_id": "other", "order_id": original["order_id"]}, {"_id": 0})
            assert saved == original
        owner_rows = await db.sources.unified_orders.find({"user_id": "owner"}).to_list(None)
        assert len(owner_rows) == 6
        assert all(WEBHOOK_PROOF in row[CURRENT_SHIPPING] for row in owner_rows)
        assert db.data[STATES] == financial
    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [
    "wrong-order", "wrong-waybill", "return", "multiple-shipments", "repeated-delivery",
    "incomplete-history", "incomplete-shipments",
])
def test_new_waybill_target_preserves_native_proof_rejections(monkeypatch, fault):
    _, source, shipment, histories = binding_gap()
    shipments = [shipment]
    if fault == "wrong-order":
        shipment["order_id"] = "another-order"
    elif fault == "wrong-waybill":
        shipment["shipping_number"] = "another-waybill"
    elif fault == "return":
        shipment["type"] = "return"
    elif fault == "multiple-shipments":
        shipments.append({**shipment, "id": "another-shipment"})
    elif fault == "repeated-delivery":
        histories.append({**deepcopy(histories[0]), "id": "delivered-again", "created_at": NOW})
    calls = []

    async def native(db, owner, method, path, *, params):
        assert owner == "owner" and method == "GET" and params["page"] == 1
        calls.append(path)
        response = page(shipments if path == "/shipments" else histories)
        if ((fault == "incomplete-history" and path.endswith("/histories"))
                or (fault == "incomplete-shipments" and path == "/shipments")):
            response["pagination"]["total"] += 1
        return response
    monkeypatch.setattr(sync, "call_salla", native)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source)
        financial = deepcopy(db.data[STATES])
        assert (await visible(db))["courier_candidates"] == 1
        assert calls
        saved = await db.sources.unified_orders.find_one({"user_id": "owner"}, {"_id": 0})
        saved.pop(sync._RETRY)
        assert saved == source
        assert db.data[STATES] == financial
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["raw", "watermark", "waybill", "status"])
def test_new_waybill_target_cannot_overwrite_source_changed_during_native_reads(monkeypatch, change):
    _, source, shipment, histories = binding_gap()
    patch = {
        "raw": {"raw_by_source.salla_direct.updated_at": "2026-10-08T00:00:00+00:00"},
        "watermark": {"g47_salla_snapshot.revision": 10},
        "waybill": {"tracking_number": "REPLACED"},
        "status": {"order_status_slug": "returned"},
    }[change]

    async def concurrent_source(db):
        await db.unified_orders.update_one({"user_id": "owner"}, {"$set": patch})
    calls = []
    native_provider(monkeypatch, {"100": (shipment, histories)}, calls, during_read=concurrent_source)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source)
        financial = deepcopy(db.data[STATES])
        assert (await visible(db))["courier_candidates"] == 1
        assert len(calls) == 2
        saved = await db.sources.unified_orders.find_one({"user_id": "owner"})
        assert saved[CURRENT_SHIPPING] == source[CURRENT_SHIPPING]
        assert WEBHOOK_PROOF not in saved[CURRENT_SHIPPING]
        assert db.data[STATES] == financial
    asyncio.run(scenario())
