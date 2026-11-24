"""Synthetic regressions for bounded Salla source recovery, not live balances."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from mongomock_motor import AsyncMongoMockClient
import pytest

from resource_governor import governor, ResourcePressure
from salla_integration import paid_sar_sync, sync


NOW = datetime(2030, 4, 24, 1, tzinfo=timezone.utc)
OWNER = "synthetic-window-owner"
REFERENCE = "synthetic-reference"
IDENTITY = "synthetic-native"


async def harness(monkeypatch, *, details=None, known=True):
    db = AsyncMongoMockClient(tz_aware=True).db
    if known:
        await db.unified_orders.insert_one({"user_id": OWNER, "order_number": REFERENCE})
    light = {"id": IDENTITY, "reference_id": REFERENCE,
             "date": {"date": "2030-04-23 15:00:00", "timezone": "Asia/Riyadh"}}
    native = {**light, "updated_at": (NOW-timedelta(hours=1)).isoformat()}
    if details is not None:
        native = details
    requests = []

    async def provider(db_, owner, method, path, **kwargs):
        assert db_ is db and owner == OWNER and method == "GET"
        requests.append((path, kwargs))
        if path == "/orders":
            return {"data": [light], "pagination": {"currentPage": 1, "totalPages": 1}}
        if path == f"/orders/{IDENTITY}":
            return {"data": native}
        if path == "/orders/items":
            assert kwargs["params"]["order_id"] == IDENTITY
            return {"data": [{"id": "synthetic-line", "quantity": 1}]}
        raise AssertionError(path)

    persist = AsyncMock(return_value={"created": False})
    status = AsyncMock()
    monkeypatch.setattr(sync, "call_salla", provider)
    monkeypatch.setattr(sync, "_now", lambda: NOW)
    monkeypatch.setattr(sync, "_persist_sync_component_snapshot", persist)
    monkeypatch.setattr(sync, "_salla_order_to_doc", lambda raw: {"order_number": raw["reference_id"]})
    monkeypatch.setattr(sync, "_enrich_order_receiving_bank", AsyncMock(side_effect=lambda db, owner, raw: raw))
    monkeypatch.setattr(sync, "_refresh_plan_b_status_snapshot", status)
    async def complete(db_, owner, reference, result):
        return await status(db_, owner, reference, {}, allow_handoff_transition=False)
    monkeypatch.setattr(sync, "_complete_status_snapshot", complete)
    monkeypatch.setattr(paid_sar_sync, "observe_list_evidence", AsyncMock())
    monkeypatch.setattr(governor, "peek", lambda: ("allowed", None))
    monkeypatch.setattr(sync.asyncio, "sleep", AsyncMock())
    return db, requests, persist, status


async def run(db, mode="scheduled"):
    if mode == "scheduled":
        return await sync.run_orders_sync(db, OWNER, refresh_window=(NOW-timedelta(days=1), NOW), existing_only=True)
    return await sync.run_orders_sync(db, OWNER, from_date="2030-04-20", to_date="2030-04-24",
                                     recover_missing_order_clock=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["scheduled", "manual"])
async def test_missing_light_clock_uses_exact_native_details_then_complete_items(monkeypatch, mode):
    db, requests, persist, status = await harness(monkeypatch)
    result = await run(db, mode)
    assert result["updated"] == 1 and result["created"] == 0
    assert result["incomplete_count"] == 0 and result["details_fetched"] == 1
    assert [path for path, _ in requests] == ["/orders", f"/orders/{IDENTITY}", "/orders/items"]
    raw = persist.await_args.args[3]
    assert raw["updated_at"] == "2030-04-24T00:00:00+00:00"
    assert raw["items"] == [{"id": "synthetic-line", "quantity": 1}]
    assert persist.await_args.kwargs["existing_only"] is True
    assert status.await_args.kwargs["allow_handoff_transition"] is False
    assert result["coverage_reason"] == ("provider_modified_since_filter_unavailable"
                                         if mode == "scheduled" else "creation_window_only")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,known", [("scheduled", True), ("manual", True), ("manual", False)])
@pytest.mark.parametrize("changed", [None, "2030-04-24 00:00:00", "bad-clock"])
async def test_details_without_aware_order_clock_remain_incomplete(monkeypatch, mode, known, changed):
    details = {"id": IDENTITY, "reference_id": REFERENCE, "updated_at": changed,
               "customer": {"updated_at": NOW.isoformat()}, "date": NOW.isoformat()}
    db, requests, persist, _ = await harness(monkeypatch, details=details, known=known)
    result = await run(db, mode)
    assert result["updated"] == 0 and result["incomplete_count"] == 1
    assert result["skipped"] == 1
    persist.assert_not_awaited()
    assert [path for path, _ in requests] == ["/orders", f"/orders/{IDENTITY}"]
    log = await db.salla_sync_logs.find_one({"id": result["log_id"]})
    assert log["status"] == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,known", [("scheduled", True), ("manual", True), ("manual", False)])
@pytest.mark.parametrize("wrong", ["id", "reference_id"])
async def test_mismatched_native_detail_never_reaches_items_or_writer(monkeypatch, mode, known, wrong):
    details = {"id": IDENTITY, "reference_id": REFERENCE, "updated_at": NOW.isoformat(), wrong: "different"}
    db, requests, persist, _ = await harness(monkeypatch, details=details, known=known)
    result = await run(db, mode)
    assert result["updated"] == 0 and result["errors_count"] == 1
    persist.assert_not_awaited()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_unknown_owner_order_never_fetches_details_or_writes_in_daily_update_only(monkeypatch):
    db, requests, persist, _ = await harness(monkeypatch, known=False)
    await db.unified_orders.insert_one({"user_id": "other-owner", "order_number": REFERENCE})
    result = await run(db)
    assert result["updated"] == 0
    persist.assert_not_awaited()
    assert [path for path, _ in requests] == ["/orders"]


@pytest.mark.asyncio
async def test_manual_creation_window_can_import_new_verified_native_order(monkeypatch):
    db, requests, persist, status = await harness(monkeypatch, known=False)
    await db.unified_orders.insert_one({"user_id": "other-owner", "order_number": REFERENCE,
                                       "manual_note": "preserve other owner's record"})
    before = await db.unified_orders.find_one({"user_id": "other-owner"})
    persist.return_value = {"created": True}
    result = await run(db, "manual")
    assert result["created"] == 1 and result["updated"] == 0 and result["incomplete_count"] == 0
    assert [path for path, _ in requests] == ["/orders", f"/orders/{IDENTITY}", "/orders/items"]
    assert persist.await_args.args[1] == OWNER
    assert not persist.await_args.kwargs.get("existing_only", False)
    assert status.await_args.kwargs["allow_handoff_transition"] is False
    assert await db.unified_orders.find_one({"user_id": "other-owner"}) == before


@pytest.mark.asyncio
async def test_old_detail_clock_does_not_admit_scheduled_write(monkeypatch):
    details = {"id": IDENTITY, "reference_id": REFERENCE, "updated_at": (NOW-timedelta(days=2)).isoformat()}
    db, requests, persist, _ = await harness(monkeypatch, details=details)
    result = await run(db)
    assert result["skipped"] == 1 and result["incomplete_count"] == 0
    assert len(requests) == 2
    persist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("date", [None, "2029-12-01T00:00:00Z", "2030-04-25T00:00:00Z"])
async def test_detail_creation_date_must_match_selected_manual_window(monkeypatch, date):
    details = {"id": IDENTITY, "reference_id": REFERENCE, "updated_at": NOW.isoformat(), "date": date}
    db, requests, persist, _ = await harness(monkeypatch, details=details)
    result = await run(db, "manual")
    assert result["incomplete_count"] == 1 and result["creation_window_unproven_count"] == 1
    assert len(requests) == 2
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_future_native_detail_clock_is_not_accepted(monkeypatch):
    details = {"id": IDENTITY, "reference_id": REFERENCE,
               "updated_at": (NOW+timedelta(hours=1)).isoformat(), "date": NOW.isoformat()}
    db, _, persist, _ = await harness(monkeypatch, details=details)
    result = await run(db, "manual")
    assert result["incomplete_count"] == 1 and result["source_clock_missing_count"] == 1
    persist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["scheduled", "manual"])
async def test_persisted_source_with_pending_component_acceptance_is_explicitly_partial(monkeypatch, mode):
    db, _, persist, _ = await harness(monkeypatch)
    persist.return_value = {"created": False, "component_revalidation": {
        "accepted": False, "retry_required": True, "reason": "component_reconciliation_required"}}
    result = await run(db, mode)
    assert result["updated"] == 1 and result["pending_component_count"] == 1
    assert result["incomplete_count"] == 1 and result["coverage_complete"] is False
    assert (await db.salla_sync_logs.find_one({"id": result["log_id"]}))["status"] == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["scheduled", "manual"])
async def test_exhausted_detail_budget_is_partial_and_never_sends_request(monkeypatch, mode):
    db, requests, persist, _ = await harness(monkeypatch)
    monkeypatch.setattr(sync, "MAX_ORDER_CLOCK_DETAILS_PER_RUN", 0)
    result = await run(db, mode)
    assert result["incomplete_count"] == 1 and result["details_fetched"] == 0
    persist.assert_not_awaited()
    assert len(requests) == 1
    assert (await db.salla_sync_logs.find_one({"id": result["log_id"]}))["status"] == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["scheduled", "manual"])
@pytest.mark.parametrize("cancel", ["task", "resource"])
async def test_cancellation_after_details_stops_before_items_and_writes(monkeypatch, mode, cancel):
    db, requests, persist, _ = await harness(monkeypatch)
    provider = sync.call_salla
    blocked = False

    async def request(*args, **kwargs):
        nonlocal blocked
        result = await provider(*args, **kwargs)
        if args[3] == f"/orders/{IDENTITY}":
            if cancel == "task":
                raise asyncio.CancelledError()
            blocked = True
        return result

    monkeypatch.setattr(sync, "call_salla", request)
    monkeypatch.setattr(governor, "peek", lambda: ("cancel" if blocked else "allowed", None))
    with pytest.raises(asyncio.CancelledError if cancel == "task" else ResourcePressure):
        await run(db, mode)
    persist.assert_not_awaited()
    assert [path for path, _ in requests] == ["/orders", f"/orders/{IDENTITY}"]


@pytest.mark.asyncio
async def test_legacy_last_30_days_normalizes_supported_riyadh_creation_dates(monkeypatch):
    db, _, _, _ = await harness(monkeypatch)
    call = AsyncMock(return_value={"data": []})
    monkeypatch.setattr(sync, "call_salla", call)
    result = await sync.run_orders_sync(db, OWNER, updated_since_hours=720)
    params = call.await_args.kwargs["params"]
    assert params["from_date"] == "2030-03-26" and params["to_date"] == "2030-04-24"
    assert "updated_at_gt" not in params
    assert result["window_clock"] == "provider_order_created_at"
    assert result["coverage_reason"] == "creation_window_only"


@pytest.mark.asyncio
async def test_40_of_many_pages_is_partial_even_for_legacy_manual_sync(monkeypatch):
    db, _, _, _ = await harness(monkeypatch)
    async def provider(*args, **kwargs):
        page = kwargs["params"]["page"]
        return {"data": [{"id": f"synthetic-{page}"}],
                "pagination": {"currentPage": page, "totalPages": 5000}}
    monkeypatch.setattr(sync, "call_salla", provider)
    monkeypatch.setattr(sync, "_salla_order_to_doc", lambda _: {})
    result = await sync.run_orders_sync(db, OWNER)
    log = await db.salla_sync_logs.find_one({"id": result["log_id"]})
    assert result["pages_fetched"] == 40 and result["truncated"] is True
    assert result["source_total_pages"] == 5000
    assert log["status"] == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"from_date": "2030-04-24", "to_date": "2030-04-20"},
    {"from_date": "2030-03-01", "to_date": "2030-04-24", "recover_missing_order_clock": True},
    {"from_date": "not-date", "to_date": "2030-04-24"},
    {"from_date": "2030-04-24", "to_date": "2030-04-25", "recover_missing_order_clock": True},
    {"updated_since_hours": True}, {"updated_since_hours": 0}, {"updated_since_hours": 745},
    {"updated_since_hours": 24, "from_date": "2030-04-24", "to_date": "2030-04-24"},
    {"from_date": "2030-04-24", "to_date": "2030-04-24", "recover_missing_order_clock": True,
     "recover_marketing_attribution": True},
])
async def test_invalid_or_unbounded_recovery_filter_fails_before_provider(monkeypatch, kwargs):
    db, requests, _, _ = await harness(monkeypatch)
    with pytest.raises(ValueError):
        await sync.run_orders_sync(db, OWNER, **kwargs)
    assert requests == []


@pytest.mark.parametrize("start,end", [("2020-04-01", None), (None, "2020-04-24")])
def test_legacy_single_creation_bound_remains_supported(start, end):
    assert sync.normalize_order_sync_filters(start, end) == (start, end)
