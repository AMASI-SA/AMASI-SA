"""Visible courier recovery must progress without loosening evidence or work limits."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from operational_balance_store import STATES
from operational_courier_transitions import WEBHOOK_PROOF
from operational_demand_refresh import LEASES, run_visible_refresh
import operational_visible_evidence as acquisition
from salla_integration import bank_payment_sync, courier_delivery_sync as sync
from salla_shipping import CURRENT_SHIPPING
from test_operational_balance_courier_acquisition import native_provider
from test_operational_balance_courier_waybill_acquisition import binding_gap, resolve, seed, visible
from test_operational_balance_delivery_confirmation_acquisition import LocalProofDB
from test_operational_balance_delivery_cutover import DELIVERED, NOW
from test_operational_balance_demand_refresh import Request, allowed
from test_operational_balance_integration import run as run_real_mongo


def freeze_acquisition_clock(monkeypatch):
    class Clock(datetime):
        instant = datetime.fromisoformat(NOW)

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)

    monkeypatch.setattr(sync, "datetime", Clock)
    return Clock


def test_expired_invalid_prefix_cannot_starve_a_later_native_delivery(monkeypatch):
    clock = freeze_acquisition_clock(monkeypatch)
    snapshots, calls = {}, []
    native_provider(monkeypatch, snapshots, calls)

    async def scenario(sources):
        db = LocalProofDB()
        db.sources = sources
        originals = {}
        for index in range(5):
            identity = str(8400 + index)
            data, source, shipment, histories = binding_gap(identity, str(9400 + index))
            if index < 4:
                source["g47_salla_snapshot"]["component_pending"] = True
            else:
                valid_data = data
            originals[identity] = deepcopy(source)
            snapshots[identity] = shipment, histories
            await seed(db, source)
        before = deepcopy(db.data[STATES])

        await visible(db)
        clock.instant += timedelta(minutes=16)
        await visible(db)

        recovered = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": "8404"})
        assert WEBHOOK_PROOF in recovered[CURRENT_SHIPPING], (
            "A never-attempted eligible delivery remains behind an expired invalid prefix"
        )
        assert resolve(valid_data, recovered)["delivered_at"] == DELIVERED
        assert calls == [("8404", "/orders/8404/histories"), ("8404", "/shipments")]
        for identity, original in originals.items():
            saved = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": identity})
            assert saved["raw_by_source"] == original["raw_by_source"]
            assert saved["g47_salla_snapshot"] == original["g47_salla_snapshot"]
            if identity != "8404":
                assert WEBHOOK_PROOF not in saved[CURRENT_SHIPPING]
        assert db.data[STATES] == before and db.data[LEASES] == {}

    run_real_mongo(scenario)


def test_oldest_due_deliveries_progress_with_cap_cooldown_and_owner_isolation(monkeypatch):
    clock = freeze_acquisition_clock(monkeypatch)
    snapshots, calls = {}, []
    native_provider(monkeypatch, snapshots, calls)

    async def scenario(sources):
        db, foreign = LocalProofDB(), []
        # Sorting and claim updates must use the same document atomically.
        # mongomock selects a sorted projected row but updates natural order
        # when projection excludes _id; only real Mongo proves this contract.
        db.sources = sources
        # Natural insertion order puts the most recently attempted rows first.
        # All are due, but the four longest waiting orders should go first.
        for index, age in enumerate((30, 31, 60, 61, 62, 63)):
            identity = str(8500 + index)
            _, source, shipment, histories = binding_gap(identity, str(9500 + index))
            source[sync._RETRY] = clock.instant - timedelta(minutes=age)
            snapshots[identity] = shipment, histories
            await seed(db, source)
            other = {**deepcopy(source), "user_id": "other"}
            foreign.append(other)
            await seed(db, other, issue=False)
        before = deepcopy(db.data[STATES])

        await visible(db)
        first_ids = {identity for identity, _ in calls}
        assert first_ids == {"8502", "8503", "8504", "8505"}, (
            "Due retries must prefer the longest waiting deliveries rather than insertion order"
        )
        assert len(calls) == 8  # One complete history and shipment list per selected order.
        await visible(db)
        assert len(calls) == len(set(calls)) == 12
        await visible(db)
        assert len(calls) == 12  # No order is selected again during its cooldown.

        owner_rows = await db.sources.unified_orders.find({"user_id": "owner"}).to_list(None)
        assert len(owner_rows) == 6
        assert all(WEBHOOK_PROOF in row[CURRENT_SHIPPING] for row in owner_rows)
        expected_retry = (clock.instant + timedelta(minutes=15)).replace(tzinfo=None)
        assert all(row[sync._RETRY] == expected_retry for row in owner_rows)
        for original in foreign:
            saved = await db.sources.unified_orders.find_one(
                {"user_id": "other", "order_id": original["order_id"]}, {"_id": 0})
            # Mongo stores UTC datetimes without tzinfo by default.
            expected = deepcopy(original)
            expected[sync._RETRY] = expected[sync._RETRY].astimezone(timezone.utc).replace(tzinfo=None)
            assert saved == expected
        assert db.data[STATES] == before and db.data[LEASES] == {}

    run_real_mongo(scenario)


@pytest.mark.parametrize("error, reason", [
    pytest.param(sync.SallaError("synthetic sensitive provider body", status_code=403),
                 "native_evidence_access_denied", id="access-denied"),
    pytest.param(httpx.ReadTimeout("synthetic sensitive provider body"),
                 "native_evidence_request_timeout", id="request-timeout"),
    pytest.param(sync.SallaError("synthetic permission timeout body", status_code=502),
                 "native_evidence_unavailable", id="provider-unavailable"),
    pytest.param(sync.SallaError("synthetic sensitive provider body", status_code=403, needs_reauth=True),
                 "native_evidence_unavailable", id="reauth-forbidden"),
    pytest.param(sync.SallaError("synthetic sensitive provider body", status_code=401, needs_reauth=True),
                 "native_evidence_unavailable", id="reauth-required"),
    pytest.param(httpx.ConnectError("synthetic permission timeout body"),
                 "native_evidence_unavailable", id="network-unavailable"),
])
@pytest.mark.parametrize("failed_source", ["histories", "shipments"])
def test_native_read_failure_reason_preserves_remaining_four_order_allowance(monkeypatch, error, reason, failed_source):
    clock = freeze_acquisition_clock(monkeypatch)
    snapshots, calls = {}, []
    native_provider(monkeypatch, snapshots, calls)
    successful_native = sync.call_salla

    async def native(db, owner, method, path, *, params):
        if ((failed_source == "histories" and path == "/orders/8600/histories")
                or (failed_source == "shipments" and path == "/shipments" and params["order_id"] == "8600")):
            calls.append(("8600", path))
            raise error
        return await successful_native(db, owner, method, path, params=params)

    monkeypatch.setattr(sync, "call_salla", native)

    async def scenario():
        db = LocalProofDB()
        for index in range(5):
            identity = str(8600 + index)
            _, source, shipment, histories = binding_gap(identity, str(9600 + index))
            snapshots[identity] = shipment, histories
            await seed(db, source)
        before = deepcopy(db.data[STATES])

        result = await visible(db)

        attempted = {identity for identity, _ in calls}
        assert attempted == {"8600", "8601", "8602", "8603"}, (
            "One order's native read failure must not discard the rest of the bounded batch"
        )
        assert result["courier"] == {
            "status": "deferred", "selected": 4, "verified": 3, "saved": 3,
            "unchanged": 0, "blocked": 0, "conflicted": 0, "unavailable": 1,
            "outcomes": [{"order_id": "8600", "status": "unavailable", "reason": reason},
                         *({"order_id": identity, "status": "saved"} for identity in ("8601", "8602", "8603"))],
        }
        assert "synthetic" not in repr(result)
        for identity in ("8601", "8602", "8603"):
            saved = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": identity})
            assert WEBHOOK_PROOF in saved[CURRENT_SHIPPING]
        failed = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": "8600"})
        untouched = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": "8604"})
        assert WEBHOOK_PROOF not in failed[CURRENT_SHIPPING]
        assert failed[sync._RETRY] == (clock.instant + timedelta(minutes=15)).replace(tzinfo=None)
        assert sync._RETRY not in untouched
        assert db.data[STATES] == before and db.data[LEASES] == {}

    asyncio.run(scenario())


def test_native_read_cancellation_joins_without_returning_unavailable_or_claiming_next_order(monkeypatch):
    clock = freeze_acquisition_clock(monkeypatch)

    async def scenario():
        db = LocalProofDB()
        for index in range(2):
            _, source, _, _ = binding_gap(str(8700 + index), str(9700 + index))
            await seed(db, source)
        before = deepcopy(db.data[STATES])
        reading, joined = asyncio.Event(), asyncio.Event()
        calls = []

        async def native(db, owner, method, path, *, params):
            calls.append(path)
            reading.set()
            try:
                await asyncio.Event().wait()
            finally:
                joined.set()

        monkeypatch.setattr(sync, "call_salla", native)
        task = asyncio.create_task(visible(db))
        await reading.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert joined.is_set()
        assert calls == ["/orders/8700/histories"]
        selected = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": "8700"})
        untouched = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": "8701"})
        assert selected[sync._RETRY] == (clock.instant + timedelta(minutes=15)).replace(tzinfo=None)
        assert sync._RETRY not in untouched
        assert all(WEBHOOK_PROOF not in row[CURRENT_SHIPPING] for row in (selected, untouched))
        assert db.data[STATES] == before and db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())


def test_bank_timeout_is_joined_and_leaves_courier_time_in_the_same_visible_budget(monkeypatch):
    monkeypatch.setattr(acquisition, "ACQUISITION_SECONDS", 0.2)

    async def scenario():
        db, events = LocalProofDB(), []
        db.data[STATES]["owner"]["engine"] = {"issues": [
            {"code": "operational_order_bank_binding_incomplete", "source_id": "synthetic-bank"},
            {"code": "operational_delivery_order_waybill_unproven", "source_id": "synthetic-courier"},
        ]}
        before = deepcopy(db.data[STATES])
        loop = asyncio.get_running_loop()
        began = None

        async def bank(*args, **kwargs):
            nonlocal began
            assert kwargs["limit"] == 4
            began = loop.time()
            events.append("bank_started")
            try:
                await asyncio.Event().wait()
            finally:
                events.append("bank_joined")

        async def courier(*args, **kwargs):
            assert kwargs["limit"] == 4
            assert events == ["bank_started", "bank_joined"]
            assert began is not None
            assert loop.time() - began < acquisition.ACQUISITION_SECONDS
            events.append("courier_started")

        monkeypatch.setattr(bank_payment_sync, "acquire_missing", bank)
        monkeypatch.setattr(sync, "acquire_missing", courier)
        result = await run_visible_refresh(Request(), db, "owner", "owner", allowed,
            lambda: acquisition.acquire_visible_evidence(db, "owner"))

        assert events == ["bank_started", "bank_joined", "courier_started"], (
            "A slow bank source consumed the courier's entire visible acquisition opportunity"
        )
        assert result["status"] == "deferred"
        assert db.data[STATES] == before and db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())
