import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import preparation_piece_operations as operations


def index_database():
    async def create_index(*args, **kwargs):
        # Yield as the driver would to exercise simultaneous first requests.
        await asyncio.sleep(0)

    return {
        collection: SimpleNamespace(create_index=AsyncMock(side_effect=create_index))
        for collection in (operations.PIECES, operations.PIECE_EVENTS)
    }


def read_routes(db):
    router = operations.make_preparation_piece_operations_router(db, lambda: {})
    return {route.path.removeprefix("/preparation-work-v1/"): route.endpoint for route in router.routes}


@pytest.mark.asyncio
async def test_receiving_and_assembly_share_setup_but_keep_caller_scope(monkeypatch):
    async def view(db, *, user_id, **kwargs):
        return {"owner": user_id}

    for name in ("_preparation_receiving_custody_view", "_preparation_receipt_search",
                 "_assembly_search", "_assembly_order_board"):
        monkeypatch.setattr(operations, name, view)

    db = index_database()
    routes = read_routes(db)
    calls = [
        ("receiving/custody", {}),
        ("receiving/search", {"q": "barcode"}),
        ("assembly/search", {"q": "order"}),
        ("assembly/orders", {"state": "completed", "limit": 10, "offset": 0, "q": ""}),
    ]
    for owners in (["a", "b", "b", "a"], ["b", "a", "a", "b"]):
        results = await asyncio.gather(*(
            routes[name](user={"id": owner, "role": "owner"}, **kwargs)
            for (name, kwargs), owner in zip(calls, owners)
        ))
        assert [result["owner"] for result in results] == owners
        assert db[operations.PIECES].create_index.await_count == 4
        assert db[operations.PIECE_EVENTS].create_index.await_count == 1


    denied_context = {"actor_id": "employee", "merchant_id": "b", "is_owner": False,
                      "permissions": set(), "responsibilities": set()}
    monkeypatch.setattr(operations, "_actor_context", AsyncMock(return_value=denied_context))
    for name, kwargs in calls:
        with pytest.raises(HTTPException) as denied:
            await routes[name](user={"id": "employee", "role": "employee"}, **kwargs)
        assert denied.value.status_code == 403
    assert db[operations.PIECE_EVENTS].create_index.await_count == 1


@pytest.mark.asyncio
async def test_failed_setup_retries_and_a_separate_database_initializes(monkeypatch):
    view = AsyncMock(return_value={})
    monkeypatch.setattr(operations, "_preparation_receiving_custody_view", view)
    owner = {"id": "test", "role": "owner"}
    db = index_database()
    custody = read_routes(db)["receiving/custody"]
    final_index = db[operations.PIECE_EVENTS].create_index
    final_index.side_effect = [RuntimeError("failed index"), None]
    with pytest.raises(RuntimeError, match="failed index"):
        await custody(user=owner)
    view.assert_not_awaited()
    await custody(user=owner)
    await custody(user=owner)
    assert db[operations.PIECES].create_index.await_count == 8
    assert final_index.await_count == 2

    other = index_database()
    await read_routes(other)["receiving/custody"](user=owner)
    assert other[operations.PIECES].create_index.await_count == 4
    assert other[operations.PIECE_EVENTS].create_index.await_count == 1
