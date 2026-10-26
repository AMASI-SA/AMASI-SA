"""A bounded Salla read must not claim complete pagination without an end."""
from unittest.mock import AsyncMock

import pytest

from salla_integration import sync


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pages", "expected_truncated"),
    [
        ([{"data": [{}]}, {"data": [{}]}], True),
        ([{"data": [{}]}, {"data": []}], False),
        ([{"data": [{}], "pagination": {"currentPage": 1, "totalPages": 1}}], False),
        ([{"data": [{}], "pagination": {"currentPage": 1, "totalPages": 2}},
          {"data": [{}], "pagination": {"currentPage": 2, "totalPages": 2}}], False),
        ([{"data": [{}], "pagination": {"currentPage": 1, "totalPages": 4}},
          {"data": [{}], "pagination": {"currentPage": 2, "totalPages": 4}}], True),
        ([{"data": [{}], "pagination": {"currentPage": 1, "totalPages": 2}},
          {"data": []}], True),
        ([{"data": [{}], "pagination": {"currentPage": 2, "totalPages": 2}}], True),
        ([{"data": [], "pagination": {"currentPage": 1, "totalPages": 2}}], True),
        ([{"data": [], "pagination": {"currentPage": 2, "totalPages": 0}}], True),
    ],
)
async def test_bounded_order_sync_only_reports_untruncated_after_proven_end(
    monkeypatch, pages, expected_truncated,
):
    call = AsyncMock(side_effect=pages)
    finish = AsyncMock()
    monkeypatch.setattr(sync, "call_salla", call)
    monkeypatch.setattr(sync, "finish_sync_log", finish)
    monkeypatch.setattr(sync, "MAX_RANGE_PAGES_PER_RUN", 2)
    monkeypatch.setattr(sync, "_enrich_order_receiving_bank", AsyncMock(return_value={}))
    monkeypatch.setattr(sync, "_salla_order_to_doc", lambda _: {})
    monkeypatch.setattr(sync.asyncio, "sleep", AsyncMock())

    result = await sync.run_orders_sync(
        object(), "owner", from_date="2026-10-25", to_date="2026-10-25", log_id="run"
    )

    assert result["truncated"] is expected_truncated
    assert finish.await_args.kwargs["extra"]["truncated"] is expected_truncated


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [{}, {"data": None}, {"data": {}}])
async def test_missing_or_invalid_data_is_failure_not_empty_page(monkeypatch, page):
    finish = AsyncMock()
    monkeypatch.setattr(sync, "call_salla", AsyncMock(return_value=page))
    monkeypatch.setattr(sync, "finish_sync_log", finish)

    with pytest.raises(ValueError, match="data must be a list"):
        await sync.run_orders_sync(object(), "owner", log_id="run")

    assert finish.await_args.args[2] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("pagination", [
    [], {"currentPage": 0}, {"totalPages": -1},
    {"currentPage": True}, {"totalPages": 1, "last_page": 2},
])
async def test_invalid_pagination_metadata_fails_the_sync(monkeypatch, pagination):
    finish = AsyncMock()
    monkeypatch.setattr(sync, "call_salla", AsyncMock(return_value={"data": [], "pagination": pagination}))
    monkeypatch.setattr(sync, "finish_sync_log", finish)
    with pytest.raises(ValueError, match="pagination"):
        await sync.run_orders_sync(object(), "owner", log_id="run")
    assert finish.await_args.args[2] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [
    # A later apparently valid last page cannot repair a wrong earlier page.
    [{"data": [{}], "pagination": {"currentPage": 2, "totalPages": 3}},
     {"data": [{}], "pagination": {"currentPage": 2, "totalPages": 3}},
     {"data": [{}], "pagination": {"currentPage": 3, "totalPages": 3}}],
    # A growing page count may reflect concurrent provider-side changes.
    [{"data": [{}], "pagination": {"currentPage": 1, "totalPages": 2}},
     {"data": [{}], "pagination": {"currentPage": 2, "totalPages": 3}},
     {"data": [{}], "pagination": {"currentPage": 3, "totalPages": 3}}],
    # A shrinking count can hide shifted or skipped records too.
    [{"data": [{}], "pagination": {"currentPage": 1, "totalPages": 3}},
     {"data": [{}], "pagination": {"currentPage": 2, "totalPages": 2}}],
    # Pagination proof cannot silently acquire missing metadata mid-run.
    [{"data": [{}]},
     {"data": [{}], "pagination": {"currentPage": 2, "totalPages": 2}}],
])
async def test_intermediate_pagination_inconsistency_is_never_complete(monkeypatch, pages):
    finish = AsyncMock()
    monkeypatch.setattr(sync, "call_salla", AsyncMock(side_effect=pages))
    monkeypatch.setattr(sync, "finish_sync_log", finish)
    monkeypatch.setattr(sync, "MAX_RANGE_PAGES_PER_RUN", 3)
    monkeypatch.setattr(sync, "_enrich_order_receiving_bank", AsyncMock(return_value={}))
    monkeypatch.setattr(sync, "_salla_order_to_doc", lambda _: {})
    monkeypatch.setattr(sync.asyncio, "sleep", AsyncMock())

    result = await sync.run_orders_sync(
        object(), "owner", from_date="2026-10-25", to_date="2026-10-25", log_id="run"
    )

    assert result["truncated"] is True
    assert finish.await_args.kwargs["extra"]["truncated"] is True
    assert result["pagination_consistent"] is False
    assert finish.await_args.kwargs["extra"]["pagination_consistent"] is False
