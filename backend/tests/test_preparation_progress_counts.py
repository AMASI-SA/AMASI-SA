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


@pytest.mark.asyncio
async def test_real_mongo_counts_include_received_files_and_exclude_old_scope():
    import os
    import uuid
    from datetime import datetime, timezone
    from motor.motor_asyncio import AsyncIOMotorClient
    from preparation_supplier_dispatch import DISPATCHES, PIECES

    uri = os.environ.get("TEST_PROGRESS_MONGO_URI")
    if not uri:
        pytest.skip("real Mongo fixture not configured")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = client["test_progress_" + uuid.uuid4().hex]
    try:
        await client.admin.command("ping")
        for day in range(10, 18):
            key = str(day)
            await db[DISPATCHES].insert_one({
                "user_id": "owner", "sent_by_id": "employee", "id": "d" + key,
                "supplier_id": "s", "status": "sent",
                "sent_at": datetime(2026, 11, day, 9, tzinfo=timezone.utc),
                "piece_ids": ["p" + key, "p" + key],
            })
            await db[PIECES].insert_one({
                "user_id": "owner", "responsible_employee_id": "employee",
                "piece_id": "p" + key, "supplier_dispatch_id": "d" + key,
                "supplier_id": "s", "status": "in_progress",
                "supplier_dispatch_status": "sent",
                "order_number": "o" + key, "image_b64": "not-needed",
            })
        # A separate file was fully received on a date that remains active.
        # Custody moved to the receiving employee, but the file sender owns
        # the historical receipt count.
        await db[DISPATCHES].insert_one({
            "user_id": "owner", "sent_by_id": "employee", "id": "received-file",
            "supplier_id": "s", "status": "received",
            "sent_at": datetime(2026, 11, 10, 10, tzinfo=timezone.utc),
            "piece_ids": ["received"],
        })
        await db[PIECES].insert_one({
            "user_id": "owner", "responsible_employee_id": "receiver",
            "piece_id": "received", "supplier_dispatch_id": "received-file",
            "supplier_id": "s", "status": "received",
            "supplier_dispatch_status": "received",
        })
        await db[PIECES].insert_many([
            {"user_id": "foreign", "responsible_employee_id": "employee",
             "piece_id": "p10", "supplier_dispatch_id": "d10",
             "supplier_id": "s", "status": "in_progress", "supplier_dispatch_status": "sent"},
            {"user_id": "owner", "responsible_employee_id": "employee",
             "piece_id": "not-a-member", "supplier_dispatch_id": "d10",
             "supplier_id": "s", "status": "in_progress", "supplier_dispatch_status": "sent"},
        ])
        total = await counts.progress_count(db, user_id="owner", employee_id="employee")
        assert total["remaining"] == 8
        first = await counts.progress_day_counts(db, user_id="owner", employee_id="employee")
        assert len(first["days"]) == 7
        assert first["days"][0] == {"key": "2026-11-10", "assigned": 2, "received": 1, "remaining": 1}
        second = await counts.progress_day_counts(db, user_id="owner", employee_id="employee",
                                                   after=first["next_cursor"])
        assert second["days"] == [{"key": "2026-11-17", "assigned": 1, "received": 0, "remaining": 1}]
        assert second["next_cursor"] is None
        assert (await counts.progress_count(db, user_id="owner", employee_id="receiver"))["remaining"] == 0
    finally:
        await client.drop_database(db.name)
        client.close()
