"""Regression — no deprecated `expanded` param on Salla /orders calls.

Salla started returning 400 in Feb 2026:
    "Please use Remove expanded from the request params,
     the expanded usage is deprecated"

This test locks in the fix: `run_orders_sync` and `resync_single_order`
must NEVER pass `expanded` to `call_salla`.
"""
from __future__ import annotations

from unittest.mock import patch, AsyncMock

import mongomock_motor  # noqa: F401
import pytest
import pytest_asyncio

from salla_integration import sync as sync_mod


TENANT = "main"


@pytest_asyncio.fixture
async def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_salla_no_expanded"]


@pytest.mark.asyncio
async def test_run_orders_sync_never_sends_expanded_param(db):
    """Every /orders call from the sync loop must be free of
    `expanded` in its params (Salla deprecated it, returns 400)."""
    captured: list[dict] = []

    async def _fake_call_salla(_db, _uid, method, path, *, params=None, **_kw):
        captured.append({"method": method, "path": path,
                         "params": dict(params) if params else {}})
        # Return an empty page → loop exits on `not data`.
        return {"data": [], "pagination": {"totalPages": 1}}

    with patch.object(sync_mod, "call_salla",
                      new=AsyncMock(side_effect=_fake_call_salla)):
        await sync_mod.run_orders_sync(
            db, TENANT, from_date="2026-07-01", to_date="2026-07-15")

    assert captured, "sync loop should have called Salla at least once"
    for call in captured:
        assert call["path"] == "/orders"
        assert "expanded" not in call["params"], (
            f"call_salla still shipped a deprecated `expanded` param: "
            f"{call['params']}"
        )
        # Only page-based pagination + optional date filters expected.
        allowed = {"page", "per_page", "from_date", "to_date",
                   "updated_at_gt"}
        assert set(call["params"]).issubset(allowed), (
            f"Unexpected params on /orders: {set(call['params']) - allowed}"
        )


@pytest.mark.asyncio
async def test_resync_single_order_never_sends_expanded_param(db):
    """Single-order re-check must ALSO drop `expanded`."""
    captured: list[dict] = []

    async def _fake_call_salla(_db, _uid, method, path, *, params=None, **_kw):
        captured.append({"method": method, "path": path,
                         "params": dict(params) if params else {}})
        return {"data": []}   # not_found_in_salla path — no upsert side-effects

    with patch.object(sync_mod, "call_salla",
                      new=AsyncMock(side_effect=_fake_call_salla)):
        await sync_mod.resync_single_order(db, TENANT, "271257282")

    assert captured, "resync_single_order should have called Salla"
    for call in captured:
        assert "expanded" not in call["params"], (
            f"resync_single_order still shipped `expanded`: {call['params']}"
        )
        allowed = {"keyword", "per_page"}
        assert set(call["params"]).issubset(allowed), (
            f"Unexpected params on /orders: {set(call['params']) - allowed}"
        )
