import pytest
from fastapi import HTTPException

import mobile_reviewed_preparation_routes as mobile_reviewed


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, _limit):
        return self

    async def to_list(self, _limit):
        return self.rows


class _Pieces:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, _projection):
        assert query == {"user_id": "owner-1", "batch_id": "batch-1"}
        return _Cursor(self.rows)


class _DB:
    def __init__(self, rows):
        self.pieces = _Pieces(rows)

    def __getitem__(self, collection):
        assert collection == mobile_reviewed.PIECES
        return self.pieces


@pytest.mark.asyncio
async def test_new_mobile_file_materializes_before_success(monkeypatch):
    calls = []

    async def materialize(_db, *, user_id, registry):
        calls.append((user_id, registry["batch_id"]))

    monkeypatch.setattr(mobile_reviewed, "materialize_preparation_pieces", materialize)
    await mobile_reviewed._ensure_assigned_pieces(
        _DB([]), user_id="owner-1",
        batch={"id": "batch-1", "allocated_quantity": 2},
        registry={"batch_id": "batch-1", "responsible_employee_id": "login-new"},
    )
    assert calls == [("owner-1", "batch-1")]


@pytest.mark.asyncio
async def test_ready_replay_never_overwrites_progressed_pieces(monkeypatch):
    async def materialize(*_args, **_kwargs):
        raise AssertionError("must not reconcile a progressed piece")

    monkeypatch.setattr(mobile_reviewed, "materialize_preparation_pieces", materialize)
    ready = {"batch_id": "batch-1", "responsible_employee_id": "login-new",
             "piece_registry_status": "ready"}
    batch = {"id": "batch-1", "allocated_quantity": 1}
    await mobile_reviewed._ensure_assigned_pieces(
        _DB([{"responsible_employee_id": "another-account", "status": "received"}]),
        user_id="owner-1", batch=batch, registry=ready,
    )
    with pytest.raises(HTTPException) as error:
        await mobile_reviewed._ensure_assigned_pieces(
            _DB([{"responsible_employee_id": "another-account", "status": "received"}]),
            user_id="owner-1", batch=batch,
            registry={**ready, "piece_registry_status": ""},
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "preparation_piece_recovery_requires_review"
