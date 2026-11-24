"""Regression contracts for count-only supplier progress reads."""
from unittest.mock import AsyncMock

import pytest

import preparation_progress_counts as counts


@pytest.mark.asyncio
async def test_total_is_unique_and_does_not_load_receipt_history(monkeypatch):
    directory = AsyncMock(return_value={"2026-11-20": {"a", "b"}, "2026-11-21": {"c"}})
    receipts = AsyncMock(side_effect=AssertionError("overview must not read history"))
    monkeypatch.setattr(counts, "_directory", directory)
    monkeypatch.setattr(counts, "_received_by_day", receipts)
    result = await counts.progress_count(None, user_id="owner", employee_id="employee")
    assert result == {"ok": True, "read_only": True, "employee_id": "employee", "remaining": 3}
    directory.assert_awaited_once_with(None, "owner", "employee")
    receipts.assert_not_awaited()


@pytest.mark.asyncio
async def test_day_pages_read_receipts_only_for_visible_dates(monkeypatch):
    keys = [f"2026-11-{i:02}" for i in range(10, 19)] + ["__unknown__"]
    directory = AsyncMock(return_value={key: {"active-" + key} for key in keys})
    async def receipt_page(db, owner, employee, page):
        assert owner == "owner" and employee == "employee"
        assert len(page) <= 7
        return {key: {"active-" + key, "received-" + key} for key in page}
    receipts = AsyncMock(side_effect=receipt_page)
    monkeypatch.setattr(counts, "_directory", directory)
    monkeypatch.setattr(counts, "_received_by_day", receipts)
    first = await counts.progress_day_counts(None, user_id="owner", employee_id="employee")
    assert [row["key"] for row in first["days"]] == keys[:7]
    assert first["next_cursor"] == keys[6]
    assert all(row["assigned"] == 2 and row["received"] == 1 and row["remaining"] == 1
               for row in first["days"])
    receipts.assert_awaited_once_with(None, "owner", "employee", keys[:7])
    second = await counts.progress_day_counts(None, user_id="owner", employee_id="employee",
                                              after=first["next_cursor"])
    assert [row["key"] for row in second["days"]] == keys[7:]
    assert second["next_cursor"] is None
    assert sum(row["remaining"] for row in first["days"] + second["days"]) == 10


@pytest.mark.asyncio
async def test_empty_is_zero_not_unavailable(monkeypatch):
    monkeypatch.setattr(counts, "_directory", AsyncMock(return_value={}))
    receipts = AsyncMock(side_effect=AssertionError("no selected days"))
    monkeypatch.setattr(counts, "_received_by_day", receipts)
    assert (await counts.progress_count(None, user_id="owner", employee_id="employee"))["remaining"] == 0
    result = await counts.progress_day_counts(None, user_id="owner", employee_id="employee")
    assert result["days"] == [] and result["next_cursor"] is None
    receipts.assert_not_awaited()


def test_piece_membership_is_current_and_unique():
    dispatch = {"id": "d", "supplier_id": "s", "piece_ids": ["p", "p"]}
    piece = {"piece_id": "p", "supplier_dispatch_id": "d", "supplier_id": "s",
             "responsible_employee_id": "e"}
    members = counts._member_ids(dispatch)
    assert members == {"p"}
    assert counts._current_member(piece, dispatch, "e", members)
    for key, value in (("supplier_dispatch_id", "old"), ("supplier_id", "other"),
                       ("responsible_employee_id", "foreign"), ("piece_id", "missing")):
        assert not counts._current_member({**piece, key: value}, dispatch, "e", members)
