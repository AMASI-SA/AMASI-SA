"""Owned profit context reads preserve scope, serial errors and cursor lifetime."""
import asyncio
from copy import deepcopy

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pytest

import operational_profit_sources as source
from operational_demand_refresh import LEASES, demand_context, run_visible_refresh
from operational_payment_evidence import _instant
from order_currency import SALLA_RAW_CURRENCY_PROJECTION
from report_option_recovery import SALLA_REPORT_OPTION_PROJECTION
from review_acceptance_config_guard import AcceptanceConfigDatabase
from test_operational_balance_demand_refresh import DB as LeaseDB, Request, allowed
from test_operational_balance_profit_sources import START, END, order


KINDS = ("settings", "orders", "raw")
SETTINGS_PROJECTION = {"_id": 0, "user_id": 1, "report_included_statuses": 1,
                       "hide_inferred_date_orders": 1, "payment_methods": 1,
                       "shipping_companies": 1}
RAW_PROJECTION = {**SALLA_RAW_CURRENCY_PROJECTION, **SALLA_REPORT_OPTION_PROJECTION,
                  "raw_by_source.salla_direct.payment_method": 1,
                  "raw_by_source.salla_direct.payment.method": 1,
                  "user_id": 1, **{"raw_by_source.salla_direct." + field: 1 for field in (
                      "id", "order_number", "date", "created_at", "order_date",
                      "updated_at", "date_updated")}}


async def reached(event):
    await asyncio.wait_for(event.wait(), 2)


class ContextBoundary:
    """Exact production DB identity with strictly in-memory collection operations."""
    def __init__(self, monkeypatch):
        self.client = AsyncIOMotorClient("mongodb://127.0.0.1:1", connect=False)
        self.db = self.client["profit_context_test"]
        self.other_db = self.client["other_profit_context_test"]
        self.leases = LeaseDB()
        row, raw = order("one", "2026-11-02T10:00:00+03:00")
        self.rows = {"settings": [{"user_id": "owner"}], "orders": [row], "raw": [raw]}
        self.entered = {kind: asyncio.Event() for kind in KINDS}
        self.release = {kind: asyncio.Event() for kind in KINDS}
        self.cancelled = {kind: asyncio.Event() for kind in KINDS}
        self.cleanup = {kind: asyncio.Event() for kind in KINDS}
        self.finished = {kind: asyncio.Event() for kind in KINDS}
        for events in (self.release, self.cleanup):
            for event in events.values():
                event.set()
        self.calls, self.errors, self.cleanup_errors = [], {}, {}
        self.active, self.peak = 0, 0
        self.read_owner, self.context = "owner", None
        self.cursor_overrides = {}
        boundary = self

        def collection(database, name):
            assert database is boundary.db or database is boundary.other_db
            if name == LEASES:
                return boundary.leases[name]
            assert name in {"settings", "unified_orders"}, "unplanned collection read"

            class Collection:
                def find(self, query, projection):
                    kind = ("settings" if name == "settings" else
                            "orders" if projection.get("raw_by_source") == 0 else "raw")
                    expected_query = {"user_id": boundary.read_owner}
                    if kind != "settings":
                        expected_query["order_date"] = {"$gte": "2026-10-31", "$lte": "2026-11-04"}
                    assert query == expected_query
                    assert projection == ({"settings": SETTINGS_PROJECTION,
                                           "orders": {"_id": 0, "raw_by_source": 0},
                                           "raw": RAW_PROJECTION}[kind])
                    if kind in boundary.cursor_overrides:
                        boundary.calls.append((kind, deepcopy(query), deepcopy(projection), None))
                        return boundary.cursor_overrides[kind]

                    class Cursor:
                        async def to_list(self, limit):
                            assert limit == (2 if kind == "settings" else source.MAX_ORDERS + 1)
                            boundary.calls.append((kind, deepcopy(query), deepcopy(projection), limit))
                            boundary.active += 1
                            boundary.peak = max(boundary.peak, boundary.active)
                            boundary.entered[kind].set()
                            try:
                                await boundary.release[kind].wait()
                                if kind in boundary.errors:
                                    raise boundary.errors[kind]
                                return deepcopy(boundary.rows[kind])
                            except asyncio.CancelledError:
                                boundary.cancelled[kind].set()
                                await boundary.cleanup[kind].wait()
                                if kind in boundary.cleanup_errors:
                                    raise boundary.cleanup_errors[kind]
                                raise
                            finally:
                                boundary.active -= 1
                                boundary.finished[kind].set()
                    return Cursor()
            return Collection()

        monkeypatch.setattr(AsyncIOMotorDatabase, "__getitem__", collection)

    async def load(self, *, database=None, owner="owner"):
        return await source._load_context(self.db if database is None else database,
                                          owner, _instant(START), _instant(END))

    async def visible(self, operation, *, database=None, request=None):
        async def run():
            self.context = demand_context()
            return await operation()
        return await run_visible_refresh(request or Request(), self.db if database is None else database,
                                         "owner", "staff", allowed, run)

    async def close_task(self, task):
        for events in (self.release, self.cleanup):
            for event in events.values():
                event.set()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.client.close()

    def settled(self):
        assert self.active == 0
        assert self.leases.data.get(LEASES, {}) == {}
        assert all(name == LEASES for name, _, _ in self.leases.writes)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


@pytest.mark.parametrize("first", ["settings", "orders"])
def test_owned_context_overlaps_settings_with_order_chain_but_raw_waits_for_orders(monkeypatch, first):
    async def exercise():
        boundary, task = ContextBoundary(monkeypatch), None
        try:
            for event in boundary.release.values():
                event.clear()
            task = asyncio.create_task(boundary.visible(boundary.load))
            await reached(boundary.entered["settings"])
            await reached(boundary.entered["orders"])
            assert not boundary.entered["raw"].is_set()
            assert boundary.active == boundary.peak == 2
            boundary.release[first].set()
            await reached(boundary.finished[first])
            if first == "settings":
                assert not boundary.entered["raw"].is_set()
                boundary.release["orders"].set()
            await reached(boundary.entered["raw"])
            assert not task.done()
            boundary.release["raw"].set()
            await reached(boundary.finished["raw"])
            if first == "orders":
                assert not boundary.finished["settings"].is_set()
                assert not task.done()
                boundary.release["settings"].set()
            result = await task
            assert result["coverage"]["complete"] and result["coverage"]["orders"] == 1
            assert result["orders"][0]["order_number"] == "one"
            assert [row[0] for row in boundary.calls] == ["settings", "orders", "raw"]
            assert boundary.peak == 2
            boundary.settled()
        finally:
            await boundary.close_task(task)
    asyncio.run(exercise())


@pytest.mark.parametrize("adapter", ["raw", "acceptance"])
@pytest.mark.parametrize("empty", [False, True])
def test_owned_context_matches_serial_queries_results_options_currency_and_empty_raw_skip(monkeypatch, adapter, empty):
    async def exercise():
        boundary = ContextBoundary(monkeypatch)
        try:
            database = boundary.db if adapter == "raw" else AcceptanceConfigDatabase(boundary.db)
            if empty:
                boundary.rows["orders"] = []
                boundary.rows["raw"] = [{"unexpected": "must never be read"}]
            else:
                inside, raw = boundary.rows["orders"][0], boundary.rows["raw"][0]
                inside["products"] = [{"order_item_id": "line", "product_id": "p1", "quantity": 1}]
                raw["raw_by_source"]["salla_direct"]["items"] = [{
                    "id": "line", "product_id": "p1", "quantity": 1,
                    "options": [{"id": "size", "value": "large"}],
                }]
                pairs = [order("boundary", START), order("future", "2026-11-03T12:00:01+03:00"),
                         order("duplicate", "2026-11-02T10:00:00+03:00"),
                         order("duplicate", "2026-11-02T10:00:00+03:00")]
                boundary.rows["orders"].extend(row for row, _ in pairs)
                boundary.rows["raw"].extend(raw for _, raw in pairs)
            before = deepcopy(boundary.rows)
            serial = await boundary.load(database=database)
            serial_calls = deepcopy(boundary.calls)
            boundary.calls.clear()
            parallel = await boundary.visible(lambda: boundary.load(database=database), database=database)
            assert parallel == serial
            assert boundary.calls == serial_calls
            assert boundary.rows == before
            assert [call[0] for call in boundary.calls] == (["settings", "orders"] if empty else list(KINDS))
            assert parallel["coverage"]["orders"] == (0 if empty else 1)
            if not empty:
                assert parallel["coverage"]["pending_orders"] == 2
                selected = parallel["orders"][0]
                assert selected["total_amount_sar"] == 100
                assert selected["products"][0]["_report_options_raw"] == [{"id": "size", "value": "large"}]
            boundary.settled()
        finally:
            await boundary.close_task(None)
    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["no_context", "public_session", "private_session", "slots_session",
                                  "unsupported", "nested_acceptance", "acceptance_snapshot",
                                  "acceptance_subclass", "wrong_db", "wrong_owner"])
def test_ordinary_session_and_unsupported_contexts_keep_serial_reads_and_omit_product_keyword(monkeypatch, kind):
    async def exercise():
        import dashboard_v2_routes as dashboard
        boundary, task = ContextBoundary(monkeypatch), None
        product_calls = []

        async def products(db, owner, orders, *, include_product_rows):
            # A legacy caller/patch with this exact signature must still work.
            assert db is database and owner == boundary.read_owner and orders is result["orders"]
            assert include_product_rows is False
            product_calls.append(True)
            return {"known": 7}

        class Wrapper:
            def __init__(self, db):
                self.db = db
            def __getitem__(self, name):
                return self.db[name]

        class Slots:
            __slots__ = ("__db", "__session")
            def __init__(self, db):
                self.__db, self.__session = db, object()
            def __getitem__(self, name):
                return self.__db[name]

        class Subclass(AcceptanceConfigDatabase):
            pass

        try:
            database = boundary.db
            if kind in {"public_session", "private_session", "unsupported"}:
                database = Wrapper(database)
                if kind != "unsupported":
                    setattr(database, "session" if kind == "public_session" else "_private_session", object())
            elif kind == "slots_session":
                database = Slots(database)
            elif kind == "nested_acceptance":
                database = AcceptanceConfigDatabase(AcceptanceConfigDatabase(database))
            elif kind == "acceptance_snapshot":
                database = AcceptanceConfigDatabase(Slots(database))
            elif kind == "acceptance_subclass":
                database = Subclass(database)
            elif kind == "wrong_db":
                database = boundary.other_db
            elif kind == "wrong_owner":
                boundary.read_owner = "other-owner"
                for rows in boundary.rows.values():
                    for row in rows:
                        row["user_id"] = boundary.read_owner
            monkeypatch.setattr(dashboard, "build_mezan_v2_product_cost_cooperative", products)
            for event in boundary.release.values():
                event.clear()
            result = None

            async def load():
                nonlocal result
                result = await boundary.load(database=database, owner=boundary.read_owner)
                return await source._component_readers(database, boundary.read_owner, result, None)["products"]()

            task = asyncio.create_task(load() if kind == "no_context" else boundary.visible(
                load, database=boundary.db if kind == "wrong_db" else database))
            await reached(boundary.entered["settings"])
            assert not boundary.entered["orders"].is_set()
            boundary.release["settings"].set()
            await reached(boundary.entered["orders"])
            assert not boundary.entered["raw"].is_set()
            boundary.release["orders"].set()
            await reached(boundary.entered["raw"])
            boundary.release["raw"].set()
            assert await task == {"known": 7}
            assert product_calls == [True] and boundary.peak == 1
            assert [call[0] for call in boundary.calls] == list(KINDS)
            boundary.settled()
        finally:
            await boundary.close_task(task)
    asyncio.run(exercise())


@pytest.mark.parametrize("adapter", ["raw", "acceptance"])
def test_products_opt_in_uses_same_positive_request_database_and_owner(monkeypatch, adapter):
    async def exercise():
        import dashboard_v2_routes as dashboard
        boundary = ContextBoundary(monkeypatch)
        try:
            database = boundary.db if adapter == "raw" else AcceptanceConfigDatabase(boundary.db)
            orders = boundary.rows["orders"]
            seen = []

            async def products(db, owner, selected, *, include_product_rows, parallel_order_cost_reads=False):
                assert db is database and owner == "owner" and selected is orders
                seen.append((include_product_rows, parallel_order_cost_reads))
                return {"known": 7}

            monkeypatch.setattr(dashboard, "build_mezan_v2_product_cost_cooperative", products)
            async def load():
                return await source._component_readers(database, "owner", {"orders": orders, "settings": {}}, None)["products"]()
            assert await boundary.visible(load, database=database) == {"known": 7}
            assert seen == [(False, True)]
            assert boundary.calls == []
            boundary.settled()
        finally:
            await boundary.close_task(None)
    asyncio.run(exercise())


@pytest.mark.parametrize("setting_failure", ["duplicate", "foreign_owner", "read_error"])
def test_settings_failure_wins_even_if_order_read_failed_first(monkeypatch, setting_failure):
    async def exercise():
        boundary, task = ContextBoundary(monkeypatch), None
        try:
            settings_error = ValueError("settings-read-failed")
            if setting_failure == "duplicate":
                boundary.rows["settings"] *= 2
            elif setting_failure == "foreign_owner":
                boundary.rows["settings"] = [{"user_id": "other-owner"}]
            else:
                boundary.errors["settings"] = settings_error
            boundary.errors["orders"] = ValueError("later-order-read-failed")
            boundary.release["settings"].clear()
            task = asyncio.create_task(boundary.visible(boundary.load))
            await reached(boundary.finished["orders"])
            assert not task.done() and not boundary.entered["raw"].is_set()
            boundary.release["settings"].set()
            with pytest.raises(ValueError) as failed:
                await task
            if setting_failure == "read_error":
                assert failed.value is settings_error
            else:
                assert str(failed.value) == "profit_settings_ambiguous"
            boundary.settled()
        finally:
            await boundary.close_task(task)
    asyncio.run(exercise())


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("too_many", ["orders", "raw"])
def test_context_caps_keep_same_error_and_never_read_raw_after_order_overflow(monkeypatch, owned, too_many):
    async def exercise():
        boundary = ContextBoundary(monkeypatch)
        try:
            monkeypatch.setattr(source, "MAX_ORDERS", 1)
            boundary.rows[too_many] *= 2
            with pytest.raises(ValueError, match="^profit_order_scope_too_large$"):
                await (boundary.visible(boundary.load) if owned else boundary.load())
            assert [call[0] for call in boundary.calls] == (["settings", "orders"] if too_many == "orders" else list(KINDS))
            boundary.settled()
        finally:
            await boundary.close_task(None)
    asyncio.run(exercise())


@pytest.mark.parametrize("pending", ["orders", "raw"])
def test_settings_error_cancels_and_drains_order_chain_before_return_even_when_cleanup_fails(monkeypatch, pending):
    async def exercise():
        boundary, task = ContextBoundary(monkeypatch), None
        try:
            original = ValueError("settings-read-failed")
            boundary.errors["settings"] = original
            boundary.release["settings"].clear()
            boundary.release[pending].clear()
            boundary.cleanup[pending].clear()
            boundary.cleanup_errors[pending] = RuntimeError("secondary-cleanup-failed")
            task = asyncio.create_task(boundary.visible(boundary.load))
            await reached(boundary.entered[pending])
            boundary.release["settings"].set()
            await reached(boundary.cancelled[pending])
            assert not task.done() and boundary.leases.data[LEASES]
            boundary.cleanup[pending].set()
            with pytest.raises(ValueError) as failed:
                await task
            assert failed.value is original
            if pending == "orders":
                assert not boundary.entered["raw"].is_set()
            boundary.settled()
        finally:
            await boundary.close_task(task)
    asyncio.run(exercise())


@pytest.mark.parametrize("pending", ["orders", "raw"])
def test_repeated_cancellation_drains_both_context_chains_before_request_lease_release(monkeypatch, pending):
    async def exercise():
        boundary, task = ContextBoundary(monkeypatch), None
        try:
            for kind in ("settings", pending):
                boundary.release[kind].clear()
                boundary.cleanup[kind].clear()
            task = asyncio.create_task(boundary.visible(boundary.load))
            await reached(boundary.entered["settings"])
            await reached(boundary.entered[pending])
            task.cancel()
            await reached(boundary.cancelled["settings"])
            await reached(boundary.cancelled[pending])
            assert not task.done() and boundary.leases.data[LEASES]
            boundary.context.work.cancel()
            boundary.cleanup["settings"].set()
            await reached(boundary.finished["settings"])
            assert not task.done() and boundary.leases.data[LEASES]
            assert not boundary.finished[pending].is_set()
            boundary.cleanup[pending].set()
            with pytest.raises(asyncio.CancelledError):
                await task
            boundary.settled()
        finally:
            await boundary.close_task(task)
    asyncio.run(exercise())


def test_ordinary_context_keeps_future_returning_to_list_call_shape(monkeypatch):
    async def exercise():
        boundary = ContextBoundary(monkeypatch)
        try:
            limits = []
            class FutureCursor:
                def __init__(self, kind):
                    self.kind = kind
                def to_list(self, limit):
                    limits.append((self.kind, limit))
                    future = asyncio.get_running_loop().create_future()
                    future.set_result(deepcopy(boundary.rows[self.kind]))
                    return future
            boundary.cursor_overrides = {kind: FutureCursor(kind) for kind in KINDS}
            result = await boundary.load()
            assert result["coverage"]["complete"] and result["coverage"]["orders"] == 1
            assert limits == [("settings", 2), ("orders", 100001), ("raw", 100001)]
            boundary.settled()
        finally:
            await boundary.close_task(None)
    asyncio.run(exercise())


@pytest.mark.parametrize("pending", ["orders", "raw"])
def test_owned_context_joins_motor_workers_and_sequential_close_on_repeated_cancellation(monkeypatch, pending):
    from test_operational_read_runtime import ThreadedMotorCursor

    async def exercise():
        boundary, task = ContextBoundary(monkeypatch), None
        cursors = {kind: ThreadedMotorCursor([deepcopy(boundary.rows[kind])], block_close=True)
                   for kind in ("settings", pending)}
        try:
            boundary.cursor_overrides = cursors
            task = asyncio.create_task(boundary.visible(boundary.load))
            await reached(cursors["settings"].entered)
            await reached(cursors[pending].entered)
            task.cancel()
            # Yield cancellation callbacks; no wall-clock scheduling assertion.
            for _ in range(12):
                await asyncio.sleep(0)
            assert not task.done() and boundary.leases.data[LEASES]
            for cursor in cursors.values():
                assert not cursor.worker_finished.is_set() and cursor.close_calls == 0
                cursor.release.set()
            for cursor in cursors.values():
                await reached(cursor.close_entered)
                assert cursor.worker_finished.is_set()
                assert not cursor.closed_during_read and cursor.close_calls == 1
            boundary.context.work.cancel()
            assert not task.done() and boundary.leases.data[LEASES]
            for cursor in cursors.values():
                cursor.close_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            for cursor in cursors.values():
                assert len(cursor.operations) == 1
                assert all(operation.done() for operation in cursor.operations)
                assert cursor.close_calls == 1 and not cursor.closed_during_read
            if pending == "orders":
                assert not boundary.entered["raw"].is_set()
            boundary.settled()
        finally:
            for cursor in cursors.values():
                cursor.release.set()
                cursor.close_release.set()
            await boundary.close_task(task)
            for cursor in cursors.values():
                await asyncio.gather(*cursor.operations, return_exceptions=True)
    asyncio.run(exercise())
