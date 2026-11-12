"""Carrier activity plus its native current shipment is a dated delivery source."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from operational_courier_transitions import WEBHOOK_PROOF, native_status_transition
from salla_integration import courier_delivery_sync as sync
from salla_shipping import CURRENT_SHIPPING, shipping_root_fields
from test_operational_balance_delivery_cutover import DELIVERED, NOW, START, courier_data, fee_total
from test_operational_balance_verified_shipping_fees import calculated


def fixture(identity="100", number="10"):
    data = courier_data()
    source = data["unified_orders"][0]
    raw = source["raw_by_source"]["salla_direct"]
    raw.pop("status_changed_at", None)
    raw.update(id=identity, reference_id=number, payment={"method": "mada"}, paid_amount="300")
    source.update(order_id=identity, salla_order_id=identity, order_number=number,
                  order_status="تم التوصيل", order_status_slug="delivered",
                  g47_salla_snapshot={"source_updated_at": NOW, "revision": 9, "component_pending": False})
    current = source[CURRENT_SHIPPING]
    current.update(company_code=None, company_name="iMile للتوصيل", status="delivering", source_kind="order",
                   shipment_id="shipment-" + identity, tracking_number="AWB-" + identity,
                   shipment_updated_at="2026-10-05T23:00:00+00:00", provider_updated_at=NOW,
                   carrier_updated_at=NOW, event_name="order.updated")
    for row in (raw["shipping"], raw["shipments"][0]):
        row.pop("delivered_at")
        row.update(status="delivering", tracking_number=current["tracking_number"])
    raw["shipments"][0]["id"] = current["shipment_id"]
    raw["shipping"]["shipment_id"] = current["shipment_id"]
    source.update(shipping_root_fields(current))
    data["mz2_shipping_setup_v2"][0]["couriers"][0]["salla_carrier_keys"] = ["iMile للتوصيل"]
    shipment = {"id": current["shipment_id"], "order_id": identity, "order_reference_id": number,
                "courier_id": "synthetic-native-carrier", "courier_name": "iMile للتوصيل", "type": "shipment",
                "tracking_number": current["tracking_number"], "shipping_number": current["tracking_number"],
                "status": "delivered", "created_at": {"date": "2026-10-06 02:00:00", "timezone": "Asia/Riyadh"},
                "actual_delivered_at": None}
    delivered = {"id": "delivered-" + identity, "type": "activity", "status": "تم التوصيل", "note": "تم التوصيل",
                 "created_at": {"date": "2026-10-06 15:00:00", "timezone": "Asia/Riyadh"},
                 "created_by": {"id": "synthetic-publisher", "name": "iMile Delivery"}}
    dispatch = {**deepcopy(delivered), "id": "dispatch-" + identity, "status": "جاري التوصيل", "note": "جاري التوصيل",
                "created_at": {"date": "2026-10-06 12:00:00", "timezone": "Asia/Riyadh"}}
    return data, source, shipment, [delivered, dispatch]


def issue(identity="100"):
    return {"code": "operational_delivery_shipment_not_delivered", "source_id": identity}


def accept(source, shipment, histories):
    current = sync.verified_delivery(source, [shipment], histories, "owner", datetime.fromisoformat(NOW))
    source.update({CURRENT_SHIPPING: current, **shipping_root_fields(current)})
    return current


def test_native_activity_recovers_current_delivery_and_fee_once_with_provenance():
    data, source, shipment, histories = fixture()
    before = deepcopy(source)
    assert fee_total(calculated(data)) == 0
    current = accept(source, shipment, histories)
    assert current["status"] == "delivered"
    assert current["company_code"] is None  # Keep the approved carrier key.
    assert current["carrier_updated_at"] == before[CURRENT_SHIPPING]["carrier_updated_at"]
    assert current["shipment_updated_at"] == DELIVERED
    assert source["raw_by_source"] == before["raw_by_source"]
    assert source["g47_salla_snapshot"] == before["g47_salla_snapshot"]
    state = calculated(data)
    assert fee_total(state) == Decimal("10.00")
    event = state["engine"]["facts"]["delivery_event:100"]
    assert event["delivered_at"] == DELIVERED
    assert event["source_history_id"] == "delivered-100"
    assert event["source_dispatch_history_id"] == "dispatch-100"
    assert event["source_native_type"] == "activity"
    assert event["source_actor_id"] == "synthetic-publisher"
    assert event["source_actor_name"] == "iMile Delivery"
    assert event["timestamp_source"] == "salla_carrier_history"
    assert event["waybill"] == "AWB-100"
    assert "cod:100" not in state["engine"]["obligations"]
    assert state["movements"] == []
    assert calculated(data, state) == state
    accept(source, shipment, list(reversed(histories)))
    assert fee_total(calculated(data, state)) == 10


@pytest.mark.parametrize("fault", [
    "comment", "free-note", "no-actor", "different-publisher", "wrong-publisher", "name-substring",
    "wrong-order", "wrong-reference", "wrong-shipment", "wrong-waybill", "wrong-carrier", "returned",
    "later-return", "duplicate-delivery", "shipment-after-dispatch", "shipment-after-delivery",
    "event-after-source", "naive-event", "updated-only", "superseded", "different-direct-time",
    "different-native-time", "two-direct-times", "dispatch-missing", "wrong-history-order", "another-actor-return",
    "custom-return", "custom-cancel", "return-note", "custom-delivery",
    "future-shipment-clock", "naive-shipment-clock",
])
def test_unproven_native_source_cannot_become_a_delivery(fault):
    _, source, shipment, histories = fixture()
    event, dispatch = histories
    if fault == "comment": event["type"] = "comment"
    elif fault == "free-note": event["note"] = "Please mark this delivered"
    elif fault == "no-actor": event.pop("created_by")
    elif fault == "different-publisher": dispatch["created_by"]["id"] = "another-publisher"
    elif fault == "wrong-publisher": event["created_by"]["name"] = dispatch["created_by"]["name"] = "SMSA Express"
    elif fault == "name-substring": event["created_by"]["name"] = dispatch["created_by"]["name"] = "My imile customer"
    elif fault == "wrong-order": shipment["order_id"] = "another-order"
    elif fault == "wrong-reference": shipment["order_reference_id"] = "another-reference"
    elif fault == "wrong-shipment": shipment["id"] = "another-shipment"
    elif fault == "wrong-waybill": shipment["shipping_number"] = "another-waybill"
    elif fault == "wrong-carrier": shipment["courier_name"] = "سمسا"
    elif fault == "returned": shipment["type"] = "return"
    elif fault == "later-return": histories.append({**deepcopy(event), "id": "return", "status": "returned", "note": "returned", "created_at": NOW})
    elif fault == "duplicate-delivery": histories.append({**deepcopy(event), "id": "again", "created_at": NOW})
    elif fault == "shipment-after-dispatch": shipment["created_at"] = "2026-10-06T10:00:00+00:00"
    elif fault == "shipment-after-delivery": shipment["created_at"] = NOW
    elif fault == "event-after-source": event["created_at"] = "2026-10-08T00:00:00+00:00"
    elif fault == "naive-event": event["created_at"] = "2026-10-06 15:00:00"
    elif fault == "updated-only": event.update(created_at=None, updated_at=DELIVERED)
    elif fault == "superseded": source[CURRENT_SHIPPING]["superseded_shipment_ids"] = ["old"]
    elif fault == "different-direct-time": shipment["delivered_at"] = NOW
    elif fault == "different-native-time": shipment["actual_delivered_at"] = NOW
    elif fault == "two-direct-times": shipment.update(actual_delivered_at=DELIVERED, delivered_at=NOW)
    elif fault == "dispatch-missing": histories.pop()
    elif fault == "wrong-history-order": event["order_id"] = "another-order"
    elif fault == "another-actor-return": histories.append({**deepcopy(event), "id": "return", "status": "returned", "note": "returned", "created_at": NOW, "created_by": {"id": "merchant", "name": "Merchant"}})
    elif fault in {"custom-return", "custom-cancel", "return-note"}:
        later = {**deepcopy(event), "id": "later", "status": "returned", "note": "returned", "created_at": NOW}
        if fault == "custom-return": later["customized"] = {"id": 1, "slug": "returned", "name": "مرتجع"}
        elif fault == "custom-cancel": later["customized"] = {"id": 2, "slug": "cancelled", "name": "ملغي"}
        else: later["note"] = "The carrier returned this shipment"
        histories.append(later)
    elif fault == "custom-delivery": event["customized"] = {"slug": "delivered", "name": "تم التوصيل"}
    elif fault == "future-shipment-clock": shipment["updated_at"] = "2026-10-08T00:00:00+00:00"
    elif fault == "naive-shipment-clock": shipment["updated_at"] = "2026-10-06 15:00:00"
    original = deepcopy(source)
    with pytest.raises(ValueError):
        accept(source, shipment, histories)
    assert source == original


@pytest.mark.parametrize("offset, expected", [(-1, 0), (0, 10), (1, 10)])
def test_native_history_delivery_uses_exact_opening_boundary(offset, expected):
    data, source, shipment, histories = fixture()
    at = datetime.fromisoformat(START) + timedelta(microseconds=offset)
    histories[0]["created_at"] = at.isoformat()
    histories[1]["created_at"] = (at - timedelta(hours=1)).isoformat()
    shipment["created_at"] = (at - timedelta(hours=2)).isoformat()
    source[CURRENT_SHIPPING]["shipment_updated_at"] = shipment["created_at"]
    accept(source, shipment, histories)
    assert fee_total(calculated(data)) == expected


@pytest.mark.parametrize("key", ["delivered_at", "actual_delivered_at"])
def test_native_explicit_delivery_time_must_agree_with_carrier_history(key):
    data, source, shipment, histories = fixture()
    shipment[key] = DELIVERED
    accept(source, shipment, histories)
    state = calculated(data)
    assert fee_total(state) == 10
    assert state["engine"]["facts"]["delivery_event:100"]["source_history_id"] == "delivered-100"


@pytest.mark.parametrize("fault", ["owner", "number", "shipment", "waybill", "time", "actor", "incomplete", "raw-version", "watermark"])
def test_stored_carrier_proof_remains_bound_to_the_source(fault):
    data, source, shipment, histories = fixture()
    current = accept(source, shipment, histories)
    proof = current[WEBHOOK_PROOF]
    if fault == "owner": proof["owner"] = "other"
    elif fault == "number": proof["order_number"] = "other"
    elif fault == "shipment": proof["shipment"]["id"] = "other"
    elif fault == "waybill": proof["waybill"] = "other"
    elif fault == "time": proof["at"] = NOW
    elif fault == "actor": proof["events"][0]["actor"]["id"] = "other"
    elif fault == "incomplete": proof["history_complete"] = False
    elif fault == "raw-version": source["raw_by_source"]["salla_direct"]["updated_at"] = START
    elif fault == "watermark": source["g47_salla_snapshot"]["component_pending"] = True
    assert fee_total(calculated(data)) == 0


def page(rows, *, current=1, pages=1, total=None):
    return {"success": True, "data": deepcopy(rows), "pagination": {
        "count": len(rows), "currentPage": current, "totalPages": pages,
        "total": len(rows) if total is None else total, "perPage": 100,
    }}


@pytest.mark.parametrize("fault", ["missing", "incomplete", "repeated", "page-number", "total-change", "too-many", "malformed", "false-success"])
def test_incomplete_native_pagination_is_never_proof(monkeypatch, fault):
    responses = [page([{"id": "a"}], pages=2, total=2), page([{"id": "b"}], current=2, pages=2, total=2)]
    if fault == "missing": responses[0].pop("pagination")
    elif fault == "incomplete": responses[1]["data"] = []
    elif fault == "repeated": responses[1]["data"] = [{"id": "a"}]
    elif fault == "page-number": responses[1]["pagination"]["currentPage"] = 1
    elif fault == "total-change": responses[1]["pagination"]["total"] = 3
    elif fault == "too-many": responses[0]["pagination"]["totalPages"] = 21
    elif fault == "malformed": responses[0]["data"] = [None]
    elif fault == "false-success": responses[0]["success"] = False
    async def get(*args, **kwargs):
        return responses.pop(0)
    monkeypatch.setattr(sync, "call_salla", get)
    with pytest.raises(ValueError):
        asyncio.run(sync._complete_pages(None, "owner", "/orders/100/histories", {}))


def native_provider(monkeypatch, snapshots, calls, *, during_read=None):
    async def get(db, owner, method, path, *, params):
        assert owner == "owner" and method == "GET" and params["page"] == 1
        identity = params["order_id"] if path == "/shipments" else path.split("/")[2]
        calls.append((identity, path))
        if during_read:
            await during_read(db)
        shipment, histories = snapshots[identity]
        return page([shipment] if path == "/shipments" else histories)
    monkeypatch.setattr(sync, "call_salla", get)


def test_visible_refresh_recovers_seven_deliveries_with_one_fee_each_and_preserves_financial_state(monkeypatch):
    from operational_balance_service import refresh
    from operational_balance_store import read
    from operational_balance_worker import tick
    from test_operational_balance_integration import run, started
    from test_operational_balance_visible_evidence import visible_refresh

    snapshots, calls = {}, []
    native_provider(monkeypatch, snapshots, calls)
    async def scenario(db):
        await started(db)
        originals = {}
        for index in range(7):
            identity = str(8000 + index)
            data, source, shipment, histories = fixture(identity, str(9000 + index))
            snapshots[identity] = shipment, histories
            originals[identity] = deepcopy(source)
            await db.unified_orders.insert_one(source)
            if index == 0:
                await db.mz2_shipping_setup_v2.insert_many(data["mz2_shipping_setup_v2"])
        preserved = {
            "store_delivery_assignments": {"user_id": "owner", "id": "already-assigned", "order_id": "other-order", "active": False},
            "store_delivery_driver_earnings": {"user_id": "owner", "id": "paid-driver", "amount": "25.00", "status": "paid"},
            "assembly_delivery": {"user_id": "owner", "order_number": "9000", "state": "delivered"},
            "mz2_expense_payments": {"user_id": "owner", "id": "paid-courier", "amount": "80.00"},
        }
        for name, record in preserved.items():
            await db[name].insert_one(deepcopy(record))
        first = await refresh(db, "owner", clock=NOW)
        assert fee_total(first) == 0
        assert sum(row["code"] in sync.ISSUE_CODES for row in first["engine"]["issues"]) == 7
        await tick(db)
        assert await read(db, "owner") == first and calls == []
        for _ in range(3):
            await visible_refresh(db)
        current = await read(db, "owner")
        assert fee_total(current) == 70
        assert len(calls) == 14 and len(set(calls)) == 14
        assert current["openings"] == first["openings"]
        assert current["movements"] == first["movements"]
        assert current["engine"]["order_versions"] == {identity: NOW for identity in originals}
        for identity, original in originals.items():
            saved = await db.unified_orders.find_one({"user_id": "owner", "order_id": identity})
            assert saved["raw_by_source"] == original["raw_by_source"]
            assert saved["g47_salla_snapshot"] == original["g47_salla_snapshot"]
            assert saved[CURRENT_SHIPPING]["status"] == "delivered"
        for name, record in preserved.items():
            assert await db[name].find_one({"user_id": "owner"}, {"_id": 0}) == record
        await tick(db)
        await visible_refresh(db)
        replayed = await read(db, "owner")
        assert replayed["engine"]["obligations"] == current["engine"]["obligations"]
        assert replayed["movements"] == current["movements"]
        assert len(calls) == 14
    run(scenario)


@pytest.mark.parametrize("race", ["raw", "watermark", "waybill", "status", "carrier"])
def test_network_read_cannot_overwrite_a_newer_canonical_source(monkeypatch, race):
    from test_operational_balance_integration import run
    _, source, shipment, histories = fixture()
    patch = {
        "raw": {"raw_by_source.salla_direct.updated_at": "2026-10-08T00:00:00+00:00"},
        "watermark": {"g47_salla_snapshot.revision": 10},
        "waybill": {"tracking_number": "REPLACED"},
        "status": {"order_status_slug": "returned"},
        "carrier": {CURRENT_SHIPPING + ".company_name": "سمسا"},
    }[race]
    async def changed(db):
        await db.unified_orders.update_one({"user_id": "owner"}, {"$set": patch})
    calls = []
    native_provider(monkeypatch, {"100": (shipment, histories)}, calls, during_read=changed)
    async def scenario(db):
        await db.unified_orders.insert_one(deepcopy(source))
        await sync.acquire_missing(db, "owner", [issue()], started_at=START)
        saved = await db.unified_orders.find_one({"user_id": "owner"})
        assert saved[CURRENT_SHIPPING]["status"] == "delivering"
        assert WEBHOOK_PROOF not in saved[CURRENT_SHIPPING]
    run(scenario)


@pytest.mark.parametrize("fault", ["other-owner", "pending", "stale", "cancelled", "requires-refresh", "reopened",
                                  "root-waybill", "root-carrier", "identity", "history-before-start"])
def test_ineligible_existing_source_never_triggers_native_reads(monkeypatch, fault):
    from test_operational_balance_integration import run
    _, source, shipment, histories = fixture()
    if fault == "other-owner": source["user_id"] = "other"
    elif fault == "pending": source["g47_salla_snapshot"]["component_pending"] = True
    elif fault == "stale": source["g47_salla_snapshot"]["source_updated_at"] = START
    elif fault == "cancelled": source["g47_salla_snapshot"]["cancelled"] = True
    elif fault == "requires-refresh": source["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    elif fault == "reopened": source["order_status_slug"] = "in_progress"
    elif fault == "root-waybill": source["tracking_number"] = "different"
    elif fault == "root-carrier": source["shipping_company"] = "سمسا"
    elif fault == "identity": source["order_id"] = "different"
    else:
        source["g47_salla_snapshot"]["source_updated_at"] = "2026-10-04T00:00:00+00:00"
        source["raw_by_source"]["salla_direct"]["updated_at"] = "2026-10-04T00:00:00+00:00"
    calls = []
    native_provider(monkeypatch, {"100": (shipment, histories)}, calls)
    async def scenario(db):
        await db.unified_orders.insert_one(deepcopy(source))
        await sync.acquire_missing(db, "owner", [issue()], started_at=START)
        saved = await db.unified_orders.find_one({}, {"_id": 0})
        saved.pop(sync._RETRY, None)
        assert saved == source and calls == []
    run(scenario)


def test_concurrent_acquisition_is_bounded_owner_scoped_and_has_no_financial_effect(monkeypatch):
    from operational_balance_store import read
    from test_operational_balance_integration import run, started
    snapshots, calls = {}, []
    native_provider(monkeypatch, snapshots, calls)
    async def scenario(db):
        await started(db)
        financial = await read(db, "owner")
        requests, originals = [], {}
        for index in range(6):
            identity, number = str(8100 + index), str(9100 + index)
            _, source, shipment, histories = fixture(identity, number)
            snapshots[identity] = shipment, histories
            requests.append(issue(identity))
            for owner in ("owner", "other"):
                row = {**deepcopy(source), "user_id": owner}
                originals[owner, identity] = deepcopy(row)
                await db.unified_orders.insert_one(row)
        await sync.acquire_missing(db, "owner", requests, started_at=START)
        assert len(calls) == 8
        await asyncio.gather(*(sync.acquire_missing(db, "owner", requests, started_at=START) for _ in range(2)))
        assert len(calls) == len(set(calls)) == 12
        await sync.acquire_missing(db, "owner", requests, started_at=START)
        assert len(calls) == 12 and await read(db, "owner") == financial
        for (owner, identity), original in originals.items():
            saved = await db.unified_orders.find_one({"user_id": owner, "order_id": identity}, {"_id": 0})
            if owner == "other":
                assert saved == original
            else:
                assert saved["raw_by_source"] == original["raw_by_source"]
                assert saved["g47_salla_snapshot"] == original["g47_salla_snapshot"]
                assert saved[CURRENT_SHIPPING]["status"] == "delivered"
    run(scenario)


@pytest.mark.parametrize("fault", [None, "owner", "waybill", "native-order", "source-version", "pending", "reopened"])
def test_order_dto_projects_the_bound_salla_recording_time_with_its_source(fault):
    from order_engine.mapper import map_salla_order
    from order_engine.repository import MongoOrderRepository
    from test_order_engine_repository import FakeDB

    _, source, shipment, histories = fixture()
    accept(source, shipment, histories)
    source["order_date"] = "2026-10-01"
    current = source[CURRENT_SHIPPING]
    proof = current[WEBHOOK_PROOF]
    if fault == "owner": proof["owner"] = "other"
    elif fault == "waybill": source["tracking_number"] = "another-waybill"
    elif fault == "native-order": proof["shipment"]["order_id"] = "another-order"
    elif fault == "source-version": source["g47_salla_snapshot"]["source_updated_at"] = START
    elif fault == "pending": source["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    elif fault == "reopened": source["order_status_slug"] = "returned"
    original = deepcopy(source)
    db = FakeDB([source])
    loaded = asyncio.run(MongoOrderRepository(db).get_salla_order(user_id="owner", order_number="10"))
    dto = map_salla_order(loaded.salla_raw)
    if fault is None:
        assert dto.shipping.status == "delivered"
        assert dto.shipping.delivery_recorded_at == datetime.fromisoformat(DELIVERED)
        assert dto.shipping.delivery_time_source == "salla_carrier_history"
        assert dto.shipping.delivery_history_id == "delivered-100"
    else:
        assert dto.shipping.delivery_recorded_at is None
        assert dto.shipping.delivery_time_source is None
        assert dto.shipping.delivery_history_id is None
    assert dto.shipping.delivered_at is None
    assert source == original and db.unified_orders.write_calls == 0
    assert db.unified_orders.last_projection["user_id"] == 1
