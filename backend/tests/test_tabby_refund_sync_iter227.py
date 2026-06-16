"""Iter-227 — Tabby refund detection fix.

Verifies:
  1. `list_payments_since` filters by `max(updated_at, created_at)` so
     a payment created 3 months ago BUT refunded yesterday is INCLUDED.
  2. `sync_tabby_payments` enforces the 90-day minimum lookback so
     refunds on historical payments cannot fall through the cracks.
  3. The stats response surfaces `effective_since` and
     `refund_lookback_days` for operator visibility.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from bnpl.clients.tabby import TabbyClient  # noqa: E402


def _today_minus(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


@pytest.mark.asyncio
async def test_list_payments_since_uses_updated_at():
    """A payment created 100 days ago but updated yesterday MUST be
    returned when since=last_week."""
    client = TabbyClient(
        secret_key="x", merchant_code="m",
        base_url="https://api.tabby.sa",
    )

    old_with_recent_refund = {
        "id": "p_old_refunded",
        "created_at": _today_minus(100) + "T10:00:00Z",
        "updated_at": _today_minus(1) + "T11:00:00Z",   # ← refund applied
        "status": "closed",
        "amount": "500.00",
    }
    fresh_payment = {
        "id": "p_fresh",
        "created_at": _today_minus(3) + "T08:00:00Z",
        "updated_at": _today_minus(3) + "T08:00:00Z",
        "status": "closed",
        "amount": "100.00",
    }
    stale = {
        "id": "p_stale",
        "created_at": _today_minus(200) + "T00:00:00Z",
        "updated_at": _today_minus(200) + "T00:00:00Z",
        "status": "closed",
        "amount": "9.00",
    }

    # Tabby returns newest-first by created_at:
    page_responses = [
        {"payments": [fresh_payment, old_with_recent_refund, stale]},
        {"payments": []},
    ]
    call_count = {"n": 0}

    async def fake_list(limit, offset):
        idx = call_count["n"]
        call_count["n"] += 1
        return page_responses[idx] if idx < len(page_responses) else {"payments": []}

    with patch.object(client, "list_payments", side_effect=fake_list):
        result = await client.list_payments_since(
            _today_minus(7) + "T00:00:00Z",
        )

    ids = {p["id"] for p in result}
    assert "p_fresh" in ids, "fresh payment must be present"
    assert "p_old_refunded" in ids, (
        "old payment with recent refund MUST be present — "
        "this is the Iter-227 fix"
    )
    assert "p_stale" not in ids, "fully stale payment must be excluded"


@pytest.mark.asyncio
async def test_list_payments_since_no_cutoff_returns_all():
    client = TabbyClient(
        secret_key="x", merchant_code="m", base_url="https://api.tabby.sa",
    )
    items = [
        {"id": f"p_{i}", "created_at": _today_minus(i) + "T00:00:00Z",
         "updated_at": _today_minus(i) + "T00:00:00Z"}
        for i in range(5)
    ]
    page_responses = [{"payments": items}, {"payments": []}]
    call_count = {"n": 0}

    async def fake_list(limit, offset):
        idx = call_count["n"]
        call_count["n"] += 1
        return page_responses[idx] if idx < len(page_responses) else {"payments": []}

    with patch.object(client, "list_payments", side_effect=fake_list):
        result = await client.list_payments_since("")  # no cutoff
    assert len(result) == 5


@pytest.mark.asyncio
async def test_sync_widens_to_90_days_min():
    """When the user passes since=last_week, the sync widens the window
    to last 90 days behind the scenes so refunds on old payments are
    captured. The stats response must expose both values."""
    from bnpl import sync_service

    fake_secrets = {
        "secret_key": "sk_x", "merchant_code": "m", "enabled": True,
        "api_base_url": "https://api.tabby.sa",
        "activation_date": _today_minus(5),
    }

    with patch.object(
        sync_service, "get_raw_secrets",
        AsyncMock(return_value=fake_secrets),
    ), patch.object(
        sync_service, "record_sync", AsyncMock(),
    ):
        # Mock the TabbyClient to never actually hit the network.
        with patch.object(
            sync_service.TabbyClient, "list_payments_since",
            AsyncMock(return_value=[]),
        ):
            class _FakeDB:
                payment_transactions = AsyncMock()
                payment_refunds = AsyncMock()
                unified_orders = AsyncMock()
            db = _FakeDB()

            tight_since = _today_minus(7) + "T00:00:00Z"
            res = await sync_service.sync_tabby_payments(
                db, "test_user", since_iso=tight_since,
            )

    stats = res.get("stats") or {}
    assert stats.get("requested_since") == tight_since
    assert stats.get("refund_lookback_days") == 90
    # Effective since must be earlier than the requested (widened).
    eff = stats.get("effective_since", "")
    assert eff < tight_since, (
        f"effective_since {eff} should be EARLIER than requested "
        f"{tight_since} (widened to 90d lookback)"
    )
