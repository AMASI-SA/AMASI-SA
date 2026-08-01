from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from integrations_control_center import snapchat_capi_purchases as capi


NOW = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)


class _Outbox:
    def __init__(self):
        self.query = None
        self.update = None

    async def update_many(self, query, update):
        self.query = query
        self.update = update
        return SimpleNamespace(modified_count=1)


class _SallaIntegrations:
    def __init__(self, user_id="owner-1"):
        self.user_id = user_id
        self.query = None

    async def find_one(self, query, projection, **kwargs):
        self.query = query
        return {"user_id": self.user_id}


class _Db:
    def __init__(self):
        self.outbox = _Outbox()
        self.salla = _SallaIntegrations()

    def __getitem__(self, name):
        if name == capi.OUTBOX_COLLECTION:
            return self.outbox
        if name == "salla_integrations":
            return self.salla
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_cancel_pending_event_is_user_and_order_scoped():
    db = _Db()
    result = await capi.cancel_pending_snapchat_purchase_event(
        db,
        user_id="owner-1",
        event_id="280001234",
        now=NOW,
    )

    assert result == {
        "queued": False,
        "cancelled_pending": 1,
        "event_id": "280001234",
        "reason": "order_ineligible",
    }
    assert db.outbox.query == {
        "user_id": "owner-1",
        "event_id": "280001234",
        "status": {"$in": ["pending", "retry"]},
    }
    patch = db.outbox.update["$set"]
    assert patch["status"] == "cancelled"
    assert patch["payload"] is None
    assert patch["payload_redacted_after_cancel"] is True
    assert patch["last_error"]["code"] == "source_order_cancelled_before_delivery"


@pytest.mark.asyncio
async def test_cancelled_salla_order_cancels_outbox_instead_of_enqueuing(monkeypatch):
    db = _Db()
    event = {
        "event": "order.updated",
        "merchant": "1233666",
        "data": {
            "id": 99,
            "reference_id": "280001234",
            "created_at": "2026-08-01T15:30:00+03:00",
            "status": {"slug": "cancelled", "name": "ملغي"},
            "customer": {"mobile": "0555123456"},
            "amounts": {"total": {"amount": 250, "currency": "SAR"}},
        },
    }

    result = await capi.enqueue_snapchat_purchase_from_salla_event(db, event)

    assert result["queued"] is False
    assert result["cancelled_pending"] == 1
    assert result["reason"] == "order_ineligible"
    assert db.salla.query == {"store_id": {"$in": ["1233666", 1233666]}}
    assert db.outbox.query["event_id"] == "280001234"
