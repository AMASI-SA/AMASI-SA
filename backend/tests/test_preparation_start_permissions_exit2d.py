from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
import preparation_piece_execution_guard as guard
import preparation_piece_operations as base


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    async def to_list(self, limit):
        return self.rows[:limit]


class Collection:
    def __init__(self, rows):
        self.rows = rows
    def find(self, query, projection):
        return Cursor(self.rows)


def database(remaining):
    return {base.PIECES: Collection([{"order_number": "EXIT2D-PERMISSION"}]),
        base.WORKFLOWS: Collection([{"order_number": "EXIT2D-PERMISSION",
            "stage": "reviewed", "preparation_assignment_status": "assigned",
            "preparation_progress": {"remaining_quantity": remaining}}])}


@pytest.mark.asyncio
async def test_existing_permissions_are_forwarded_without_replacing_authority(monkeypatch):
    original = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(guard, "_ORIGINAL_START_FILE", original)
    permissions = {"preparation.execute"}
    result = await guard._start_file_after_full_assignment(database(0), user_id="synthetic-owner",
        registry={"batch_id": "synthetic-batch"}, actor={"id": "synthetic-employee"},
        note=None, permissions=permissions)
    assert result == {"ok": True}
    assert original.await_count == 1
    assert original.await_args.kwargs["permissions"] is permissions


@pytest.mark.asyncio
async def test_permissions_never_bypass_incomplete_assignment_denial(monkeypatch):
    original = AsyncMock()
    monkeypatch.setattr(guard, "_ORIGINAL_START_FILE", original)
    with pytest.raises(HTTPException) as error:
        await guard._start_file_after_full_assignment(database(1), user_id="synthetic-owner",
            registry={"batch_id": "synthetic-batch"}, actor={"id": "synthetic-employee"},
            note=None, permissions={"*"})
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "preparation_file_start_requires_full_assignment"
    original.assert_not_awaited()
