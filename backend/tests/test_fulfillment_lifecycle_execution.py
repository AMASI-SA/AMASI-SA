from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import fulfillment_lifecycle as lifecycle
from fulfillment_lifecycle_execution import guarded_execution


@pytest.mark.asyncio
async def test_disabled_adapter_preserves_original_result_without_target_lookup(monkeypatch):
    monkeypatch.setattr(lifecycle, "guarded_owner", AsyncMock(return_value=False))

    @guarded_execution("piece")
    async def operation(db, *, user_id, piece_id):
        return {"piece_id": piece_id}

    assert await operation(object(), user_id="owner", piece_id="piece") == {"piece_id": "piece"}


@pytest.mark.asyncio
async def test_physical_piece_forwards_caller_fence_instead_of_current_snapshot(monkeypatch):
    monkeypatch.setattr(lifecycle, "guarded_owner", AsyncMock(return_value=True))
    collection = type("Collection", (), {"find_one": AsyncMock(return_value={
        "piece_id": "piece", "order_number": "42", "order_item_id": "line", "revision": 9,
        "generation": 3})})()
    observed = []

    @asynccontextmanager
    async def scope(db, **kwargs):
        observed.append(kwargs["targets"])
        raise HTTPException(409, detail={"code": "fulfillment_piece_generation_conflict"})
        yield

    monkeypatch.setattr(lifecycle, "execution_scope", scope)
    called = AsyncMock()

    @guarded_execution("piece")
    async def operation(db, *, user_id, piece_id, expected_revision=None, expected_generation=None):
        await called()

    with pytest.raises(HTTPException) as error:
        await operation({lifecycle.PIECES: collection}, user_id="owner", piece_id="piece",
                        expected_revision=2, expected_generation="stale-generation")
    assert error.value.detail["code"] == "fulfillment_piece_generation_conflict"
    assert observed[0][0]["expected_revision"] == 2
    assert observed[0][0]["expected_generation"] == "stale-generation"
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_callback_runs_inside_claim_and_releases_afterward(monkeypatch):
    monkeypatch.setattr(lifecycle, "guarded_owner", AsyncMock(return_value=True))
    events = []

    @asynccontextmanager
    async def scope(db, **kwargs):
        assert kwargs["targets"] == [{"order_number": "42"}]
        events.append("claim")
        try:
            yield
        finally:
            events.append("release")

    monkeypatch.setattr(lifecycle, "execution_scope", scope)

    @guarded_execution("order")
    async def provider_operation(db, user_id, order_number):
        events.append("provider")

    await provider_operation(object(), "owner", "42")
    assert events == ["claim", "provider", "release"]


@pytest.mark.asyncio
async def test_auto_route_defers_hold_but_preserves_other_failure(monkeypatch):
    monkeypatch.setattr(lifecycle, "guarded_owner", AsyncMock(return_value=True))
    code = "fulfillment_lifecycle_held"

    @asynccontextmanager
    async def scope(db, **kwargs):
        raise HTTPException(409, detail={"code": code})
        yield

    monkeypatch.setattr(lifecycle, "execution_scope", scope)

    @guarded_execution("auto_route", defer=True)
    async def route(db, *, user_id, order):
        raise AssertionError("held order must not advance")

    order = type("Order", (), {"order_number": "42"})()
    assert (await route(object(), user_id="owner", order=order))["blocked"] is True
    code = "fulfillment_execution_in_flight"
    with pytest.raises(HTTPException) as error:
        await route(object(), user_id="owner", order=order)
    assert error.value.detail["code"] == code


@pytest.mark.asyncio
async def test_assignment_guard_resolves_unmaterialized_lines_and_existing_piece_fences(monkeypatch):
    from fulfillment_lifecycle_execution import _targets
    from preparation_piece_operations import BATCHES, PIECES
    piece = {"piece_id": "piece", "order_number": "42", "order_item_id": "line",
             "revision": 4, "generation": 8, "unit_index": 1}
    batch = {"id": "batch", "lines": [
        {"order_number": "42", "order_item_id": "line"},
        {"order_number": "43", "order_item_id": "new-line"}]}
    batches = type("Collection", (), {"find_one": AsyncMock(return_value=batch)})()
    cursor = type("Cursor", (), {"to_list": AsyncMock(return_value=[piece])})()
    pieces = type("Pieces", (), {"find": lambda self, query: cursor})()
    selected = await _targets({BATCHES: batches, PIECES: pieces}, "owner",
                              {"client_request_id": "request"}, "assignment")
    assert {(row["order_number"], row["order_item_id"]) for row in selected} == {
        ("42", "line"), ("43", "new-line")}
    physical = next(row for row in selected if row["piece_id"])
    assert physical["expected_revision"] == 4
    assert physical["expected_generation"] == lifecycle.piece_generation(piece)
    assert batches.find_one.await_args_list[0].args[0] == {
        "user_id": "owner", "client_request_id": "request"}


@pytest.mark.asyncio
async def test_batch_guard_checks_every_order_before_execution(monkeypatch):
    monkeypatch.setattr(lifecycle, "guarded_owner", AsyncMock(return_value=True))
    seen = []

    @asynccontextmanager
    async def scope(db, **kwargs):
        seen.extend(kwargs["targets"])
        raise HTTPException(409, detail={"code": "fulfillment_lifecycle_held"})
        yield

    monkeypatch.setattr(lifecycle, "execution_scope", scope)

    @guarded_execution("batch")
    async def pack(db, *, user_id, batch):
        raise AssertionError("held batch must not execute")

    with pytest.raises(HTTPException):
        await pack(object(), user_id="owner", batch={"order_numbers": ["42", "43"]})
    assert seen == [{"order_number": "42"}, {"order_number": "43"}]
