"""Sampled assembly evidence preserves the real read, mapping and cancellation path."""
import asyncio
from contextvars import ContextVar
from copy import deepcopy
import json
import logging
from types import SimpleNamespace

from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import pytest

import read_path_diagnostics as diagnostics
import order_engine.service as service
from order_engine.repository import MongoOrderRepository, OrderDiscoveryRow
import preparation_piece_operations as pieces
from test_fulfillment_read_performance import seed_queue
from test_order_batch_cpu_fairness import (
    BufferedRepository, CloseableCursor, ControlledCPU, database, discovery_rows,
    raw_rows, traced,
)
from test_order_engine_repository import FakeCollection


@pytest.fixture(autouse=True)
def isolated_sampling(monkeypatch, caplog):
    # Control eligibility only. Production sampling and output are exercised,
    # while rate/cardinality/concurrency belong to the helper's own tests.
    monkeypatch.setattr(diagnostics, "_last", {})
    monkeypatch.setattr(diagnostics, "_active", set())
    monkeypatch.setattr(diagnostics, "_current", ContextVar("assembly_test_trace", default=None))
    caplog.set_level(logging.INFO, logger="mezan.read_path_diagnostics")


def samples(caplog):
    return [json.loads(record.getMessage().split("read_path_sample ", 1)[1])
            for record in caplog.records
            if record.name == "mezan.read_path_diagnostics"
            and record.getMessage().startswith("read_path_sample ")]


class ReadOnlyObservedDB:
    """Real in-memory Mongo behavior, recording selectors without a write API."""
    def __init__(self, db):
        self.db = db
        self.calls = []

    def __getitem__(self, name):
        target = self.db[name]
        calls = self.calls

        class Collection:
            def find(self, *args, **kwargs):
                calls.append((name, "find", deepcopy(args), deepcopy(kwargs)))
                return target.find(*args, **kwargs)

            async def find_one(self, *args, **kwargs):
                calls.append((name, "find_one", deepcopy(args), deepcopy(kwargs)))
                return await target.find_one(*args, **kwargs)

            async def distinct(self, *args, **kwargs):
                calls.append((name, "distinct", deepcopy(args), deepcopy(kwargs)))
                return await target.distinct(*args, **kwargs)

        return Collection()

    def __getattr__(self, name):
        return self[name]


@pytest.mark.parametrize("state", ["in_progress", "completed", "direct_preparation", "supplier_received"])
def test_sampled_board_keeps_queries_complete_output_and_pagination(monkeypatch, caplog, state):
    async def exercise():
        memory = AsyncMongoMockClient()["assembly_diagnostic_parity"]
        source_state = "completed" if state == "completed" else "in_progress"
        numbers = await seed_queue(memory, source_state, 105, items=[{
            "order_item_id": "synthetic-direct", "preparation_route": "direct_assembly", "quantity": 2,
        }])
        await seed_queue(memory, source_state, 3, owner="unrelated-owner")
        before = {name: await memory[name].find({}).to_list(None)
                  for name in await memory.list_collection_names()}
        params = {"user_id": "owner", "state": state, "limit": 7, "offset": 4}
        baseline_db = ReadOnlyObservedDB(memory)
        monkeypatch.setattr(diagnostics, "_last", {"assembly_board": float("inf")})
        baseline = await pieces._assembly_order_board(baseline_db, **params)
        assert not samples(caplog)

        monkeypatch.setattr(diagnostics, "_last", {})
        observed_db = ReadOnlyObservedDB(memory)
        actual = await pieces._assembly_order_board(observed_db, **params)
        assert actual == baseline
        assert observed_db.calls == baseline_db.calls
        assert before == {name: await memory[name].find({}).to_list(None) for name in before}
        if state == "in_progress":
            assert actual["total"] == 105
            assert [row["order_number"] for row in actual["items"]] == numbers[4:11]
            assert all(row["total_count"] == 3 and row["ready_count"] == 1 for row in actual["items"])
        if state in {"direct_preparation", "supplier_received"}:
            # Residual physical custody contradicts this exclusively direct
            # fixture, so neither explicit group may include it.
            assert actual["total"] == 0 and actual["items"] == []
        record, = samples(caplog)
        assert record["scope"] == "assembly_board" and record["mode"] == state
        assert record["counts"]["workflows_acquired"] == record["counts"]["workflows_filtered"] == 105
        assert record["counts"]["physical_rows"] == 105
        assert record["counts"]["canonical_batches"] == 2
        assert record["counts"]["hydrated_rows"] == record["counts"]["mapped_rows"] == 105
        assert record["counts"]["virtual_pieces"] == 210
        assert record["counts"]["result_rows"] == len(actual["items"])
        expected = {"piece_distinct", "workflow_read", "local_proofs", "piece_read",
                    "hydration_cursor", "hydrate_row", "dto_map", "cooperative_yield",
                    "virtual_pieces", "sort_group"}
        assert expected <= record["phases"].keys()
        assert record["phases"]["hydrate_row"]["calls"] == record["phases"]["dto_map"]["calls"] == 105
        assert "sum_thread_cpu_ms" in record["phases"]["dto_map"]
        assert "sum_thread_cpu_ms" not in record["phases"]["workflow_read"]
        assert "Synthetic customer" not in json.dumps(record)
        assert "unrelated-owner" not in json.dumps(record)
    asyncio.run(exercise())


def test_sampled_hydration_preserves_invalid_discovery_rows_and_cursor_protocol(caplog):
    async def exercise():
        source = raw_rows(8)
        source.append({**deepcopy(source[0]), "order_date": ""})
        db = database(source)
        result = await diagnostics.sample("assembly_board", MongoOrderRepository(db).get_salla_orders(
            user_id="owner", order_numbers=[str(index) for index in range(8)]), mode="in_progress")
        assert len(result) == 8
        assert result[4].salla_raw["timeline"] == [{"event": "synthetic-history"}]
        assert db.unified_orders.rows == source and db.unified_orders.write_calls == 0
        assert db.unified_orders.cursor.advances == 9
        assert db.unified_orders.cursor.close_calls == 0
        record, = samples(caplog)
        assert record["counts"]["hydrated_rows"] == 8
        assert record["counts"]["invalid_discovery_rows"] == 1
        assert record["phases"]["hydrate_row"]["calls"] == 9
    asyncio.run(exercise())


def test_sampled_dto_mapping_keeps_duplicate_and_invalid_row_rules(caplog):
    async def exercise():
        source = discovery_rows(raw_rows(8))
        replacement = deepcopy(source[0].salla_raw)
        replacement["customer"]["full_name"] = "Private synthetic replacement"
        source.extend([OrderDiscoveryRow("0", "2026-11-22", replacement),
                       OrderDiscoveryRow("0", "2026-11-22", {}),
                       OrderDiscoveryRow("invalid", "2026-11-22", {})])
        repository = BufferedRepository(source)
        before = deepcopy(source)
        result = await diagnostics.sample("assembly_board", service.get_orders(
            repository, user_id="private-owner", order_numbers=[row.order_number for row in source]))
        assert len(result) == 8 and "invalid" not in result
        assert result["0"].customer.name == "Private synthetic replacement"
        assert source == before and len(repository.calls) == 1
        record, = samples(caplog)
        assert record["counts"]["mapped_rows"] == 9
        assert record["counts"]["invalid_mapping_rows"] == 2
        assert record["phases"]["dto_map"]["calls"] == 11
        assert "Private synthetic replacement" not in json.dumps(record)
        assert "private-owner" not in json.dumps(record)
    asyncio.run(exercise())


def test_other_read_scope_does_not_acquire_assembly_counts(caplog):
    async def exercise():
        db = database(raw_rows(8))
        result = await diagnostics.sample("operational_context", service.get_orders(
            MongoOrderRepository(db), user_id="owner", order_numbers=["0"]), mode="home")
        assert list(result) == ["0"]
        record, = samples(caplog)
        assert record["scope"] == "operational_context" and record["mode"] == "home"
        assert record["counts"] == {} and record["phases"] == {}
    asyncio.run(exercise())


def test_sampled_checkpoint_cancellation_keeps_original_joined_close(monkeypatch, caplog):
    async def exercise():
        db = database(raw_rows(), gated_close=True)
        calls, heartbeat = [], []
        clock = ControlledCPU(monkeypatch)
        traced(monkeypatch, MongoOrderRepository, "_to_discovery_row", clock,
               calls, heartbeat, static=True, cancel=True)
        task = asyncio.create_task(diagnostics.sample("assembly_board", MongoOrderRepository(db).get_salla_orders(
            user_id="owner", order_numbers=[str(index) for index in range(100)])))
        await asyncio.sleep(0)
        cursor = db.unified_orders.cursor
        started = asyncio.create_task(cursor.close_started.wait())
        try:
            await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
            assert cursor.close_started.is_set()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not cursor.closed
            cursor.close_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cursor.closed and cursor.close_calls == cursor.advances == len(calls) == 1
            assert db.unified_orders.write_calls == 0
            record, = samples(caplog)
            assert record["phases"]["hydration_cursor"]["calls"] == 1
            assert record["phases"]["cooperative_yield"]["calls"] == 1
        finally:
            cursor.close_release.set()
            started.cancel()
            await asyncio.gather(started, task, return_exceptions=True)
    asyncio.run(exercise())


def test_cancelled_pending_cursor_advance_is_not_closed_or_retried(caplog):
    async def exercise():
        class PendingCursor(CloseableCursor):
            def __init__(self, rows):
                super().__init__(rows)
                self.advance_started = asyncio.Event()
                self.advance_release = asyncio.Event()
                self.attempts = 0

            async def __anext__(self):
                self.attempts += 1
                self.advance_active = True
                self.advance_started.set()
                try:
                    await self.advance_release.wait()
                    return await super().__anext__()
                finally:
                    self.advance_active = False

        class PendingCollection(FakeCollection):
            def find(self, *args, **kwargs):
                selected = super().find(*args, **kwargs)
                self.cursor = PendingCursor(selected.rows)
                return self.cursor

        db = SimpleNamespace(unified_orders=PendingCollection(raw_rows()))
        task = asyncio.create_task(diagnostics.sample("assembly_board", MongoOrderRepository(db).get_salla_orders(
            user_id="owner", order_numbers=["0"])))
        await asyncio.sleep(0)
        cursor = db.unified_orders.cursor
        await cursor.advance_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cursor.attempts == 1 and cursor.advances == cursor.close_calls == 0
        assert not cursor.advance_active
        record, = samples(caplog)
        assert record["phases"]["hydration_cursor"]["calls"] == 1
        assert "hydrate_row" not in record["phases"]
    asyncio.run(exercise())


def test_unexpected_mapper_failure_remains_the_same_exception_without_logging_payload(monkeypatch, caplog):
    async def exercise():
        failure = RuntimeError("private customer data from the failed mapper")
        def broken(*args, **kwargs):
            raise failure
        monkeypatch.setattr(service, "_map_row", broken)
        repository = BufferedRepository(discovery_rows(raw_rows(8)))
        with pytest.raises(RuntimeError) as raised:
            await diagnostics.sample("assembly_board", service.get_orders(
                repository, user_id="private-owner", order_numbers=["0"]))
        assert raised.value is failure
        record, = samples(caplog)
        assert record["phases"]["dto_map"]["calls"] == 1
        assert "private customer data" not in json.dumps(record)
        assert "private-owner" not in json.dumps(record)
    asyncio.run(exercise())


def test_invalid_board_state_keeps_error_and_performs_no_reads(caplog):
    async def exercise():
        db = ReadOnlyObservedDB(AsyncMongoMockClient().invalid_board_diagnostic)
        with pytest.raises(HTTPException) as raised:
            await pieces._assembly_order_board(db, user_id="private-owner", state="private-invalid-state",
                                               limit=1, offset=0)
        assert raised.value.status_code == 422
        assert raised.value.detail == {"code": "assembly_board_state_invalid"}
        assert not db.calls
        record, = samples(caplog)
        assert "private-invalid-state" not in json.dumps(record)
        assert "private-owner" not in json.dumps(record)
    asyncio.run(exercise())
