from __future__ import annotations

import inspect
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from salla_integration import auto_sync
from salla_integration.service import SallaError


def _matches(row: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$lte" and not (actual is not None and actual <= value):
                    return False
                if operator == "$gte" and not (actual is not None and actual >= value):
                    return False
                if operator == "$in" and actual not in value:
                    return False
        elif actual != expected:
            return False
    return True


def _project(row: dict, projection: dict | None) -> dict:
    if not projection:
        return deepcopy(row)
    included = {key for key, value in projection.items() if value and key != "_id"}
    return {key: deepcopy(row[key]) for key in included if key in row}


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, spec):
        for key, direction in reversed(spec):
            self.rows.sort(key=lambda row: (row.get(key) is None, row.get(key)))
            if direction < 0:
                self.rows.reverse()
        return self

    def limit(self, value: int):
        self.rows = self.rows[:value]
        return self

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCollection:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = [deepcopy(row) for row in (rows or [])]
        self.find_calls = 0
        self.update_calls = 0
        self.delete_calls = 0
        self.indexes: list[tuple[object, dict]] = []

    async def find_one(self, query: dict, projection: dict | None = None):
        for row in self.rows:
            if _matches(row, query):
                return _project(row, projection)
        return None

    def find(self, query: dict, projection: dict | None = None):
        self.find_calls += 1
        return FakeCursor(
            [_project(row, projection) for row in self.rows if _matches(row, query)]
        )

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        self.update_calls += 1
        row = next((item for item in self.rows if _matches(item, query)), None)
        inserted = row is None
        if row is None:
            if not upsert:
                return None
            row = deepcopy(query)
            self.rows.append(row)
        if inserted:
            row.update(deepcopy(update.get("$setOnInsert") or {}))
        row.update(deepcopy(update.get("$set") or {}))
        return None

    async def delete_one(self, query: dict):
        self.delete_calls += 1
        self.rows = [row for row in self.rows if not _matches(row, query)]
        return None

    async def create_index(self, spec, **options):
        self.indexes.append((deepcopy(spec), deepcopy(options)))
        return options.get("name")


class FakeDB:
    def __init__(self, unified_orders: list[dict] | None = None):
        self.unified_orders = FakeCollection(unified_orders)
        self.salla_auto_sync_state = FakeCollection()
        self.salla_auto_sync_retry_ledger = FakeCollection()


def _order(number: str, *, internal_id: str | None = None) -> dict:
    return {
        "id": internal_id or f"internal-{number}",
        "reference_id": number,
        "date": {"date": "2026-09-02 10:00:00"},
        "updated_at": "2026-09-02T10:00:00Z",
    }


@pytest.mark.asyncio
async def test_transient_order_failure_is_retried_after_checkpoint_and_restart(
    monkeypatch,
):
    """Regression: the old in-memory checkpoint lost B after the overlap."""
    user_id = "tenant-checkpoint-loss"
    db = FakeDB()
    orders = [_order("A"), _order("B")]
    discoveries = iter([orders, []])
    attempts: list[str] = []
    b_attempts = 0
    clock = {"now": datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)}

    async def discover(_db, _user_id):
        return next(discoveries)

    async def sync_one(_db, _user_id, row):
        nonlocal b_attempts
        order_number = row["reference_id"]
        attempts.append(order_number)
        if order_number == "B":
            b_attempts += 1
            if b_attempts == 1:
                raise auto_sync.OrderSyncFailure(
                    "items_fetch", "provider_timeout", retryable=True
                )
        return True

    async def reconcile(_db, _user_id):
        return 0, 0

    monkeypatch.setattr(auto_sync, "_discover_recent_orders", discover)
    monkeypatch.setattr(auto_sync, "_sync_light_order", sync_one)
    monkeypatch.setattr(auto_sync, "_reconcile_status_pages", reconcile)
    monkeypatch.setattr(auto_sync, "_utcnow", lambda: clock["now"])
    auto_sync._last_success_at.pop(user_id, None)

    first = await auto_sync._run_auto_sync(db, user_id)
    assert first["failed"] == 1
    assert first["retryable"] == 1
    assert first["checkpoint_safe"] is True

    auto_sync._last_success_at.clear()
    clock["now"] += timedelta(minutes=20)
    second = await auto_sync._run_auto_sync(db, user_id)

    assert attempts == ["A", "B", "B"]
    assert second["retry_due"] == 1
    assert db.salla_auto_sync_retry_ledger.rows == []


@pytest.mark.asyncio
async def test_discovery_paginates_60_60_72_and_finds_all_192(monkeypatch):
    pages = {
        1: [_order(f"S-{index:03d}") for index in range(1, 61)],
        2: [_order(f"S-{index:03d}") for index in range(61, 121)],
        3: [_order(f"S-{index:03d}") for index in range(121, 193)],
    }
    calls: list[int] = []

    async def call(_db, _user_id, _method, path, *, params, **_kwargs):
        assert path == "/orders"
        calls.append(params["page"])
        return {
            "data": pages[params["page"]],
            "pagination": {"currentPage": params["page"], "totalPages": 3},
        }

    monkeypatch.setattr(auto_sync, "call_salla", call)
    result = await auto_sync._list_light_orders_bounded(
        FakeDB(),
        "tenant",
        filters={"from_date": "2026-09-02", "to_date": "2026-09-02"},
        max_pages=10,
        max_orders=600,
    )

    assert calls == [1, 2, 3]
    assert result["pages_fetched"] == 3
    assert result["provider_calls"] == 3
    assert len(result["orders"]) == 192
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_duplicate_across_pages_has_one_effective_identity(monkeypatch):
    pages = {
        1: [_order("A"), _order("B")],
        2: [_order("B"), _order("C")],
    }

    async def call(_db, _user_id, _method, _path, *, params, **_kwargs):
        return {
            "data": pages[params["page"]],
            "pagination": {"currentPage": params["page"], "totalPages": 2},
        }

    monkeypatch.setattr(auto_sync, "call_salla", call)
    result = await auto_sync._list_light_orders_bounded(
        FakeDB(), "tenant", filters={}, max_pages=5, max_orders=100
    )
    assert [row["reference_id"] for row in result["orders"]] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_conflicting_internal_ids_are_reported_ambiguous(monkeypatch):
    pages = {
        1: [_order("A", internal_id="one")],
        2: [_order("A", internal_id="two")],
    }

    async def call(_db, _user_id, _method, _path, *, params, **_kwargs):
        return {
            "data": pages[params["page"]],
            "pagination": {"currentPage": params["page"], "totalPages": 2},
        }

    monkeypatch.setattr(auto_sync, "call_salla", call)
    result = await auto_sync._list_light_orders_bounded(
        FakeDB(), "tenant", filters={}, max_pages=5, max_orders=100
    )
    assert result["ambiguous_order_numbers"] == ["A"]
    assert result["orders"] == []


@pytest.mark.asyncio
async def test_provider_429_has_four_attempt_ceiling(monkeypatch):
    attempts = 0
    sleeps: list[float] = []

    async def call(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise SallaError("rate limited", status_code=429)

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(auto_sync, "call_salla", call)
    monkeypatch.setattr(auto_sync.asyncio, "sleep", sleep)
    with pytest.raises(SallaError):
        await auto_sync._list_light_orders_bounded(
            FakeDB(), "tenant", filters={}, max_pages=2, max_orders=120
        )

    assert attempts == 4
    assert sleeps == [0.25, 0.5, 1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 503])
async def test_provider_5xx_is_bounded_and_propagated(monkeypatch, status_code):
    attempts = 0

    async def call(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise SallaError("provider unavailable", status_code=status_code)

    async def sleep(_delay):
        return None

    monkeypatch.setattr(auto_sync, "call_salla", call)
    monkeypatch.setattr(auto_sync.asyncio, "sleep", sleep)
    with pytest.raises(SallaError):
        await auto_sync._list_light_orders_bounded(
            FakeDB(), "tenant", filters={}, max_pages=2, max_orders=120
        )
    assert attempts == auto_sync.MAX_PROVIDER_READ_ATTEMPTS


@pytest.mark.asyncio
async def test_items_failure_isolated_with_safe_stage_and_retry_code(monkeypatch):
    db = FakeDB()

    async def fail_items(*_args, **_kwargs):
        raise SallaError("sensitive provider body", status_code=500)

    monkeypatch.setattr(auto_sync, "_fetch_salla_order_items", fail_items)
    with pytest.raises(auto_sync.OrderSyncFailure) as raised:
        await auto_sync._sync_light_order(db, "tenant", _order("MISSING"))

    assert raised.value.stage == "items_fetch"
    assert raised.value.error_code == "provider_unavailable"
    assert raised.value.retryable is True
    assert "sensitive" not in str(raised.value)


@pytest.mark.asyncio
async def test_transient_upsert_failure_is_classified_for_durable_retry(monkeypatch):
    db = FakeDB()

    async def items(*_args, **_kwargs):
        return []

    def normalize(row):
        return {"order_number": row["reference_id"]}

    async def fail_upsert(*_args, **_kwargs):
        raise TimeoutError("temporary database timeout")

    monkeypatch.setattr(auto_sync, "_fetch_salla_order_items", items)
    monkeypatch.setattr(auto_sync, "_salla_order_to_doc", normalize)
    monkeypatch.setattr(auto_sync, "upsert_order", fail_upsert)

    with pytest.raises(auto_sync.OrderSyncFailure) as raised:
        await auto_sync._sync_light_order(db, "tenant", _order("MISSING"))
    assert raised.value.stage == "upsert"
    assert raised.value.error_code == "operation_timeout"
    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_existing_webhook_order_is_not_rewritten_or_items_fetched(monkeypatch):
    db = FakeDB(
        [{"user_id": "tenant", "order_number": "282459326", "source": "webhook"}]
    )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("existing order must not fetch items or write")

    monkeypatch.setattr(auto_sync, "_fetch_salla_order_items", forbidden)
    monkeypatch.setattr(auto_sync, "upsert_order", forbidden)

    assert await auto_sync._sync_light_order(
        db, "tenant", _order("282459326")
    ) is True
    assert db.unified_orders.update_calls == 0


@pytest.mark.asyncio
async def test_equal_timestamp_boundary_is_kept_inside_overlap(monkeypatch):
    checkpoint = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    db = FakeDB()
    db.salla_auto_sync_state.rows.append(
        {"user_id": "tenant", "checkpoint_at": checkpoint}
    )
    observed_filters: list[dict] = []

    async def listing(_db, _user_id, *, filters, **_kwargs):
        observed_filters.append(filters)
        return {
            "orders": [],
            "pages_fetched": 1,
            "provider_calls": 1,
            "source_total_pages": 1,
            "truncated": False,
            "ambiguous_order_numbers": [],
        }

    monkeypatch.setattr(auto_sync, "_list_light_orders_bounded", listing)
    await auto_sync._discover_recent_orders(db, "tenant")
    assert observed_filters == [{"updated_at_gt": "2026-09-02 11:50:00"}]


@pytest.mark.asyncio
async def test_192_vs_190_recovers_only_the_two_missing_orders(monkeypatch):
    example = "282459326"
    existing = [example, *[f"S-{index:03d}" for index in range(1, 190)]]
    missing = ["MISSING-191", "MISSING-192"]
    provider_numbers = [*existing, *missing]
    db = FakeDB(
        [
            {
                "user_id": "tenant",
                "order_number": number,
                "order_date": "2026-09-02",
            }
            for number in existing
        ]
    )
    sync_calls: list[str] = []

    async def listing(*_args, **_kwargs):
        return {
            "orders": [_order(number) for number in provider_numbers],
            "pages_fetched": 4,
            "provider_calls": 4,
            "source_total_pages": 4,
            "truncated": False,
            "ambiguous_order_numbers": [],
        }

    async def sync_one(_db, user_id, row):
        number = row["reference_id"]
        sync_calls.append(number)
        _db.unified_orders.rows.append(
            {"user_id": user_id, "order_number": number, "order_date": "2026-09-02"}
        )
        return True

    monkeypatch.setattr(auto_sync, "_list_light_orders_bounded", listing)
    monkeypatch.setattr(auto_sync, "_sync_light_order", sync_one)

    dry_run = await auto_sync.reconcile_salla_order_gaps(
        db,
        "tenant",
        date_from="2026-09-02",
        date_to="2026-09-02",
    )
    assert dry_run["salla_count"] == 192
    assert dry_run["expected_count"] == 192
    assert dry_run["mezan_count"] == 190
    assert dry_run["missing_count"] == 2
    assert dry_run["missing_order_numbers"] == missing
    assert sync_calls == []

    recovered = await auto_sync.reconcile_salla_order_gaps(
        db,
        "tenant",
        date_from="2026-09-02",
        date_to="2026-09-02",
        recover_missing=True,
    )
    assert sync_calls == missing
    assert recovered["recovered_count"] == 2
    assert recovered["missing_count"] == 0
    assert recovered["mezan_count"] == 192
    assert db.unified_orders.update_calls == 0


def test_gap_reconciliation_rejects_more_than_48_hours():
    with pytest.raises(ValueError, match="limited to 48 hours"):
        auto_sync._validated_gap_window("2026-09-01", "2026-09-03")


@pytest.mark.asyncio
async def test_truncated_discovery_does_not_advance_checkpoint(monkeypatch):
    db = FakeDB()

    async def discover(*_args, **_kwargs):
        return {
            "orders": [_order("A")],
            "pages_fetched": auto_sync.MAX_DISCOVERY_PAGES,
            "truncated": True,
        }

    async def sync_one(*_args, **_kwargs):
        return True

    async def reconcile(*_args, **_kwargs):
        return 0, 0

    monkeypatch.setattr(auto_sync, "_discover_recent_orders", discover)
    monkeypatch.setattr(auto_sync, "_sync_light_order", sync_one)
    monkeypatch.setattr(auto_sync, "_reconcile_status_pages", reconcile)
    result = await auto_sync._run_auto_sync(db, "tenant")

    assert result["status"] == "partial"
    assert result["checkpoint_safe"] is False
    assert "checkpoint_at" not in db.salla_auto_sync_state.rows[0]


@pytest.mark.asyncio
async def test_discovery_page_ceiling_sets_truncated(monkeypatch):
    calls = 0

    async def call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "data": [_order(f"{calls}-{index}") for index in range(60)],
            "pagination": {"currentPage": calls, "totalPages": 99},
        }

    monkeypatch.setattr(auto_sync, "call_salla", call)
    result = await auto_sync._list_light_orders_bounded(
        FakeDB(), "tenant", filters={}, max_pages=3, max_orders=500
    )
    assert calls == 3
    assert len(result["orders"]) == 180
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_retry_backoff_is_bounded_and_sixth_failure_is_exhausted():
    db = FakeDB()
    attempted_at = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    result = None
    for attempt in range(1, auto_sync.RETRY_MAX_ATTEMPTS + 1):
        result = await auto_sync._record_retry_failure(
            db,
            "tenant",
            _order("A"),
            stage="upsert",
            error_code="transient_order_failure",
            retryable=True,
            attempted_at=attempted_at,
        )
        assert result["attempt_count"] == attempt
    assert result is not None
    assert result["retryable"] is False
    assert result["next_retry_at"] is None
    assert db.salla_auto_sync_retry_ledger.rows[0]["status"] == "exhausted"


@pytest.mark.asyncio
async def test_retry_diagnostic_exposes_no_raw_payload_or_error_message():
    db = FakeDB()
    db.salla_auto_sync_retry_ledger.rows.append(
        {
            "user_id": "tenant",
            "order_number": "A",
            "status": "retryable",
            "retryable": True,
            "attempt_count": 1,
            "last_error_code": "provider_timeout",
            "last_failure_stage": "items_fetch",
            "first_discovered_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
            "next_retry_at": datetime(2026, 9, 2, 0, 1, tzinfo=timezone.utc),
            "light_order": {"customer": {"email": "must-not-leak@example.com"}},
            "raw_error": "must-not-leak",
        }
    )
    diagnostic = await auto_sync._retry_diagnostics(db, "tenant")
    rendered = repr(diagnostic)
    assert "must-not-leak" not in rendered
    assert "light_order" not in rendered
    assert diagnostic["oldest_unresolved_failure"]["error_code"] == "provider_timeout"


@pytest.mark.asyncio
async def test_auto_sync_indexes_are_tenant_scoped_and_idempotent():
    db = FakeDB()
    await auto_sync.ensure_salla_auto_sync_indexes(db)
    await auto_sync.ensure_salla_auto_sync_indexes(db)
    assert db.salla_auto_sync_state.indexes[0] == (
        [("user_id", 1)],
        {"unique": True, "name": "salla_auto_sync_state_user_unique"},
    )
    assert db.salla_auto_sync_retry_ledger.indexes[0][0] == [
        ("user_id", 1),
        ("order_number", 1),
    ]


@pytest.mark.asyncio
async def test_retry_ledger_survives_restart_with_mongo_semantics():
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()["task4-restart"]
    attempted_at = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    await auto_sync.ensure_salla_auto_sync_indexes(db)
    await auto_sync._record_retry_failure(
        db,
        "tenant",
        _order("A"),
        stage="items_fetch",
        error_code="provider_unavailable",
        retryable=True,
        attempted_at=attempted_at,
    )

    auto_sync._last_success_at.clear()
    due = await auto_sync._load_due_retry_orders(
        db,
        "tenant",
        now=attempted_at + timedelta(minutes=20),
    )
    assert [row["reference_id"] for row in due] == ["A"]


def test_background_paths_never_open_salla_order_details():
    source = inspect.getsource(auto_sync)
    assert 'f"/orders/{' not in source
    assert '"/orders/items"' not in source
    assert "_fetch_salla_order_items" in source


def test_public_gap_diagnostic_is_get_only_and_cannot_enable_recovery():
    source = (
        Path(__file__).resolve().parents[1] / "salla_integration" / "routes.py"
    ).read_text(encoding="utf-8")
    assert '@router.get("/orders-gap-diagnostic")' in source
    assert "recover_missing=False" in source
    assert '@router.post("/orders-gap-diagnostic")' not in source
