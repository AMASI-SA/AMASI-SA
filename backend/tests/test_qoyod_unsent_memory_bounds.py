import asyncio
import inspect
from datetime import date, datetime, timezone

import pytest

from integrations.qoyod import candidate_orders
from integrations.qoyod.candidate_orders import (
    CandidateAuditScanLimitExceeded,
    CandidateDateRange,
)
from qoyod_auto_unified import queue_api


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.batch_size_value = None
        self.max_time_ms_value = None
        self.limit_value = None
        self.sort_value = None

    def sort(self, *args):
        self.sort_value = args
        return self

    def batch_size(self, value):
        self.batch_size_value = value
        return self

    def max_time_ms(self, value):
        self.max_time_ms_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def __aiter__(self):
        rows = self.rows[: self.limit_value] if self.limit_value else self.rows

        async def iterate():
            for row in rows:
                yield dict(row)

        return iterate()


class FakeCollection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.query = None
        self.projection = None
        self.cursor = None

    def find(self, query, projection):
        self.query = query
        self.projection = projection
        self.cursor = FakeCursor(self.rows)
        return self.cursor


class FakeDB:
    def __init__(self, *, unified=(), inbox=(), invoices=()):
        self.unified_orders = FakeCollection(unified)
        self.integration_inbox = FakeCollection(inbox)
        self.qoyod_invoices = FakeCollection(invoices)


@pytest.fixture(autouse=True)
def reset_query_coordinator():
    queue_api._reset_query_coordinator_for_tests()
    yield
    queue_api._reset_query_coordinator_for_tests()


@pytest.mark.asyncio
async def test_twenty_identical_calls_share_one_heavy_execution(monkeypatch):
    executions = 0
    release = asyncio.Event()

    async def execute(*args, **kwargs):
        nonlocal executions
        executions += 1
        await release.wait()
        return {"ok": True, "orders": []}

    async def original(*args, **kwargs):
        raise AssertionError("coordinator test must use the patched executor")

    monkeypatch.setattr(queue_api, "_execute_list", execute)
    db = object()
    tasks = [
        asyncio.create_task(
            queue_api._list_unsent_orders_with_queue_counts(
                original,
                db,
                user_id="tenant-a",
                from_date="2026-07-01",
                limit=5000,
            )
        )
        for _ in range(20)
    ]
    for _ in range(20):
        if executions:
            break
        await asyncio.sleep(0)
    assert executions == 1
    release.set()
    results = await asyncio.gather(*tasks)
    assert len(results) == 20
    assert all(result["ok"] for result in results)


@pytest.mark.asyncio
async def test_distinct_queries_are_isolated_and_heavy_concurrency_is_two(monkeypatch):
    active = 0
    peak = 0
    started = 0
    release = asyncio.Event()

    async def execute(*args, **kwargs):
        nonlocal active, peak, started
        started += 1
        active += 1
        peak = max(peak, active)
        await release.wait()
        active -= 1
        return {"ok": True, "search": kwargs.get("search"), "orders": []}

    async def original(*args, **kwargs):
        raise AssertionError

    monkeypatch.setattr(queue_api, "_execute_list", execute)
    db = object()
    tasks = [
        asyncio.create_task(
            queue_api._list_unsent_orders_with_queue_counts(
                original,
                db,
                user_id="tenant-a",
                orders_user_id=f"orders-{index}",
                search=f"query-{index}",
            )
        )
        for index in range(5)
    ]
    for _ in range(50):
        if peak == 2:
            break
        await asyncio.sleep(0)
    assert started == 2
    assert peak == 2
    release.set()
    results = await asyncio.gather(*tasks)
    assert {result["search"] for result in results} == {
        f"query-{index}" for index in range(5)
    }


@pytest.mark.asyncio
async def test_error_is_not_cached_and_inflight_is_cleaned(monkeypatch):
    executions = 0

    async def execute(*args, **kwargs):
        nonlocal executions
        executions += 1
        if executions == 1:
            raise RuntimeError("temporary scan failure")
        return {"ok": True, "orders": []}

    async def original(*args, **kwargs):
        raise AssertionError

    monkeypatch.setattr(queue_api, "_execute_list", execute)
    db = object()
    with pytest.raises(RuntimeError, match="temporary scan failure"):
        await queue_api._list_unsent_orders_with_queue_counts(
            original, db, user_id="tenant-a"
        )
    await asyncio.sleep(0)
    result = await queue_api._list_unsent_orders_with_queue_counts(
        original, db, user_id="tenant-a"
    )
    assert result["ok"] is True
    assert executions == 2


@pytest.mark.asyncio
async def test_consumer_cancellation_does_not_cancel_shared_scan(monkeypatch):
    executions = 0
    release = asyncio.Event()

    async def execute(*args, **kwargs):
        nonlocal executions
        executions += 1
        await release.wait()
        return {"ok": True, "orders": []}

    async def original(*args, **kwargs):
        raise AssertionError

    monkeypatch.setattr(queue_api, "_execute_list", execute)
    db = object()
    survivor = asyncio.create_task(
        queue_api._list_unsent_orders_with_queue_counts(
            original, db, user_id="tenant-a"
        )
    )
    cancelled = asyncio.create_task(
        queue_api._list_unsent_orders_with_queue_counts(
            original, db, user_id="tenant-a"
        )
    )
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not survivor.done()
    release.set()
    assert (await survivor)["ok"] is True
    assert executions == 1


@pytest.mark.asyncio
async def test_queue_counts_reuse_the_original_candidate_audit(monkeypatch):
    audit = {
        "orders": [],
        "eligible_references": set(),
        "scan_limit": 10_000,
    }
    failures = {}
    original_calls = 0
    queue_calls = 0

    async def original(db, **kwargs):
        nonlocal original_calls
        original_calls += 1
        assert kwargs["_include_internal"] is True
        return {
            "ok": True,
            "orders": [],
            "_candidate_audit": audit,
            "_manual_failures": failures,
        }

    async def queue_audit(db, **kwargs):
        nonlocal queue_calls
        queue_calls += 1
        assert kwargs["audit"] is audit
        assert kwargs["failures"] is failures
        return audit, failures, {
            "ready_to_send": 0,
            "quarantined": 0,
            "needs_payment_verification": 0,
            "in_qoyod": 0,
            "retryable_sync": 0,
        }

    monkeypatch.setattr(queue_api, "_queue_audit", queue_audit)
    result = await queue_api._execute_list(
        original, object(), user_id="tenant-a"
    )
    assert original_calls == 1
    assert queue_calls == 1
    assert "_candidate_audit" not in result
    assert "_manual_failures" not in result


@pytest.mark.asyncio
async def test_unified_dashboard_scan_uses_light_projection_and_hard_cap():
    rows = [
        {
            "user_id": "tenant-a",
            "order_number": str(100 + index),
            "order_date": "2026-09-01",
            "order_status": "completed",
            "order_status_slug": "completed",
            "payment_status": "paid",
            "payment_collection_status": "paid",
            "remaining_amount": 0,
            "total_amount": 100,
            "items": [{"huge": True}],
            "products": [{"huge": True}],
            "customer": {"name": "safe", "email": "large@example.invalid"},
        }
        for index in range(3)
    ]
    db = FakeDB(unified=rows)
    result = await candidate_orders.load_unified_candidates(
        db,
        orders_user_id="tenant-a",
        date_range=CandidateDateRange(
            from_date=date(2026, 7, 1),
            to_date=date(2026, 9, 2),
            requested_from_date=date(2026, 7, 1),
        ),
        scan_limit=2,
        lightweight=True,
    )

    projection = db.unified_orders.projection
    cursor = db.unified_orders.cursor
    assert "items" not in projection
    assert "products" not in projection
    assert "customer" not in projection
    assert projection["customer.name"] == 1
    assert projection["customer.phone"] == 1
    assert cursor.batch_size_value == 100
    assert cursor.max_time_ms_value == 8_000
    assert cursor.limit_value == 3
    assert result["scan_truncated"] is True
    assert result["scanned"] == 2
    assert len(result["by_reference"]) == 2


@pytest.mark.asyncio
async def test_inbox_projection_and_cursor_are_bounded_without_all_rows():
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    db = FakeDB(inbox=[
        {
            "user_id": "tenant-a",
            "salla_order_number": "100",
            "received_at": now,
            "pipeline_stage": "NORMALIZED",
            "trace_id": f"trace-{index}",
            "canonical_payload": {"huge": "not projected"},
        }
        for index in range(3)
    ])
    evidence = await candidate_orders.load_inbox_evidence(
        db,
        marker_user_ids=["tenant-a"],
        order_numbers=["100"],
        scan_limit=2,
    )

    projection = db.integration_inbox.projection
    cursor = db.integration_inbox.cursor
    assert "canonical_payload" not in projection
    assert "raw_payload" not in projection
    assert projection["stage_history"] == {"$slice": -6}
    assert cursor.batch_size_value == 100
    assert cursor.max_time_ms_value == 8_000
    assert cursor.limit_value == 3
    assert evidence["scan_truncated"] is True
    assert evidence["scanned_rows"] == 2
    assert evidence["event_counts"] == {"100": 2}
    assert "all_rows" not in evidence


@pytest.mark.asyncio
async def test_invoice_query_is_reference_scoped_and_projection_is_minimal():
    db = FakeDB(invoices=[])
    evidence = await candidate_orders.load_qoyod_reference_evidence(
        db,
        markers_user_id="tenant-a",
        order_numbers=["100", "101"],
        scan_limit=50,
    )
    query = db.qoyod_invoices.query
    projection = db.qoyod_invoices.projection
    assert query["user_id"] == "tenant-a"
    assert {"100", "101"} == set(query["$or"][0]["reference"]["$in"])
    assert "raw_response" not in projection
    assert projection["raw_response.reference"] == 1
    assert db.qoyod_invoices.cursor.limit_value == 51
    assert evidence["scan_truncated"] is False


@pytest.mark.asyncio
async def test_exact_audits_fail_closed_but_dashboard_can_report_truncation(
    monkeypatch,
):
    async def unified(*args, **kwargs):
        return {
            "by_reference": {},
            "references": set(),
            "scanned": 10,
            "scan_truncated": True,
            "excluded": {
                "missing_order_number": 0,
                "missing_or_inferred_order_date": 0,
                "outside_requested_date_range": 0,
                "status_not_eligible": 0,
                "payment_not_eligible": 0,
                "duplicate_unified_reference": 0,
            },
            "excluded_by_status": {},
        }

    async def inbox(*args, **kwargs):
        return {
            "newest": {},
            "markers": {},
            "event_counts": {},
            "owners_by_reference": {},
            "scanned_rows": 0,
            "scan_truncated": False,
        }

    async def invoices(*args, **kwargs):
        return {
            "by_reference": {},
            "references": set(),
            "unreferenced": [],
            "duplicate_references": {},
            "scanned_rows": 0,
            "scan_truncated": False,
        }

    monkeypatch.setattr(candidate_orders, "load_unified_candidates", unified)
    monkeypatch.setattr(candidate_orders, "load_inbox_evidence", inbox)
    monkeypatch.setattr(
        candidate_orders, "load_qoyod_reference_evidence", invoices
    )

    with pytest.raises(CandidateAuditScanLimitExceeded):
        await candidate_orders.build_candidate_audit(
            object(),
            orders_user_id="orders-a",
            markers_user_id="tenant-a",
            scan_limit=10,
        )

    read_only = await candidate_orders.build_candidate_audit(
        object(),
        orders_user_id="orders-a",
        markers_user_id="tenant-a",
        scan_limit=10,
        lightweight=True,
        require_complete=False,
    )
    assert read_only["scan_truncated"] is True
    assert read_only["scanned_rows"]["unified_orders"] == 10


def test_sensitive_callers_keep_fail_closed_default():
    parameter = inspect.signature(
        candidate_orders.build_candidate_audit
    ).parameters["require_complete"]
    assert parameter.default is True
