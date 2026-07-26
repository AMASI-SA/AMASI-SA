"""Regression tests for paid-after-creation Plan-B automatic sends."""
from __future__ import annotations

import asyncio

from qoyod_auto_payment_freshness import sync_authoritative_payment_to_inbox


class _Result:
    matched_count = 1


class _UnifiedOrders:
    def __init__(self, row):
        self.row = row

    async def find_one(self, query, projection=None):
        assert query == {"user_id": "owner-1", "order_number": "274071833"}
        return dict(self.row) if self.row else None


class _Inbox:
    def __init__(self):
        self.update = None

    async def find_one(self, query, projection=None, sort=None):
        assert query == {
            "user_id": "main",
            "salla_order_number": "274071833",
        }
        assert sort == [("received_at", -1)]
        return {"id": "legacy-row-1"}

    async def update_one(self, query, update):
        assert query == {"id": "legacy-row-1", "user_id": "main"}
        self.update = update
        return _Result()


class _DB:
    def __init__(self, unified):
        self.unified_orders = _UnifiedOrders(unified)
        self.integration_inbox = _Inbox()


def test_paid_tamara_replaces_pending_creation_snapshot():
    db = _DB({
        "payment_method": "tamara_installment",
        "payment_status": "paid",
        "paid_amount": 120.96,
        "remaining_amount": 0.0,
        "has_remaining_amount": False,
        "payment_collection_status": "paid",
        "order_status": "تم التنفيذ",
        "order_status_slug": "completed",
    })

    result = asyncio.run(sync_authoritative_payment_to_inbox(
        db,
        orders_user_id="owner-1",
        legacy_user_id="main",
        order_number="274071833",
    ))

    assert result["ok"] is True
    patch = db.integration_inbox.update["$set"]
    assert patch["canonical_payload.payment_method"] == "tamara_installment"
    assert patch["canonical_payload.payment_status"] == "paid"
    assert patch["canonical_payload.paid_amount"] == 120.96
    assert patch["canonical_payload.remaining_amount"] == 0.0
    assert patch["canonical_payload.payment_collection_status"] == "paid"
    assert patch["canonical_payload.order_status_native"] == "تم التنفيذ"
    assert patch["canonical_payload.order_status_slug"] == "completed"


def test_still_pending_payment_fails_closed():
    db = _DB({
        "payment_method": "pending_payment",
        "payment_status": "unpaid",
        "paid_amount": 0.0,
        "remaining_amount": 120.96,
        "payment_collection_status": "unpaid",
        "order_status": "تم التنفيذ",
        "order_status_slug": "completed",
    })

    result = asyncio.run(sync_authoritative_payment_to_inbox(
        db,
        orders_user_id="owner-1",
        legacy_user_id="main",
        order_number="274071833",
    ))

    assert result["ok"] is False
    assert result["code"] == "authoritative_payment_method_still_pending"
    assert db.integration_inbox.update is None
