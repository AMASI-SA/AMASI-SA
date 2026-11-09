"""A delivery lease must not hide an already completed provider label."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import assembly_manual_label_refresh as manual
from test_manual_courier_label_refresh_race import scenario


@pytest.mark.asyncio
async def test_completed_external_label_is_readable_while_delivery_owns_lease(monkeypatch):
    db, calls = await scenario(monkeypatch, status="completed", external=True)
    key = {"user_id": "owner", "order_number": "3001"}
    delivery = {"version": 2, "state": "pending", "claim": "worker",
        "lease_until": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
        "order_confirmed": False, "status_attempted": True, "attempts": 1}
    await db.order_review_workflows.update_one(key, {"$set": {"assembly_delivery": delivery}})
    # The provider has completed, but the local source observation is behind.
    await db.unified_orders.update_one(key, {"$set": {
        "order_status": "in_progress", "raw_by_source.salla_direct.status.slug": "in_progress"}})
    before = await db.order_review_workflows.find_one(key)

    result = await manual.complete_and_refresh(db, "owner", "3001", AsyncMock())

    assert result["ready"] and result["order_status_completed"]
    assert result["contract_version"] == 1 and result["print_state"] == "ready"
    assert result["order_number"] == "3001" and result["shipment_id"] == "99"
    assert result["label_url"] == "https://files.example/aramex-synthetic.pdf"
    assert [call[:2] for call in calls] == [("GET", "/orders/123")]
    assert await db.order_review_workflows.find_one(key) == before
