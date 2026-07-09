"""Regression — status-return bug (user report, 2026-02):

User scenario:
    1. Order in Salla with status "completed" → shows in Plan B
       Pending "completed" tab.
    2. Merchant CHANGES status to "under_review"      (بانتظار المراجعة)
       → order correctly disappears from the "completed" tab.
    3. Merchant CHANGES it back to "completed"        (تم التنفيذ)
       → order should REAPPEAR in the "completed" tab.
    BUG: the order never reappeared.

Root cause:
    `webhook.py` DuplicateKeyError branch silently returned when Make
    fired the SAME `completed` idempotency key twice. The existing
    "completed" row kept its OLD `received_at`, so the intermediate
    "under_review" row (with newer `received_at`) out-ranked it in
    the `list_pending_orders` aggregation `$group $first` step.

Fix:
    On DuplicateKeyError, bump the existing row's `received_at` to
    `now` and append a DUPLICATE_REPLAY stage-history entry. NOTHING
    else changes: pipeline_stage, canonical_payload, invoice markers,
    all stay untouched. No Qoyod API call.

These tests reproduce the scenario end-to-end (webhook → inbox →
Plan-B pending list) so the fix is proved against the user's
acceptance criteria.
"""
from __future__ import annotations

import os
os.environ.setdefault("QOYOD_WEBHOOK_TOKEN", "test-token")

from datetime import datetime, timezone

import mongomock_motor  # noqa: F401
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient, ASGITransport

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod.webhook import attach_webhook_routes
from integrations.qoyod_manual.pending import list_pending_orders


TENANT = "main"


# ── Fixtures ────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    client = mongomock_motor.AsyncMongoMockClient()
    _db = client["test_status_return_bug"]
    await ensure_qoyod_indexes(_db)
    return _db


@pytest_asyncio.fixture
async def http(db):
    """Minimal FastAPI app with just the webhook route wired to the
    in-memory DB. Token check is disabled by supplying the header."""
    from integrations.qoyod import webhook as wh_mod
    os.environ["QOYOD_WEBHOOK_TOKEN"] = "test-token"
    # `_make_verify_token` reads the env var at request time, so
    # setting it here (after module import) is sufficient.

    router = APIRouter()
    attach_webhook_routes(router, db)
    app = FastAPI()
    app.include_router(router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport,
                           base_url="http://test") as client:
        yield client


HEADERS = {"X-Webhook-Token": "test-token"}


# ── Payload builders ────────────────────────────────────────────────
def _salla_body(order_id: str, status_slug: str,
                status_name: str,
                *, order_date: str = "2026-07-10 10:00:00") -> dict:
    return {
        "event": "order.updated",
        "data": {
            "id": order_id,
            "reference_id": order_id,
            "date": {"date": order_date, "timezone": "Asia/Riyadh"},
            "status": {
                "slug": status_slug, "name": status_name,
                "customized": {"slug": status_slug, "name": status_name},
            },
            "customer": {"first_name": "أحمد", "last_name": "ح.",
                         "mobile": "+966501234567"},
            "amounts": {
                "sub_total": {"amount": "217.39", "currency": "SAR"},
                "tax":       {"amount":  "32.61", "currency": "SAR"},
                "shipping":  {"amount":   "0.00", "currency": "SAR"},
                "total":     {"amount": "250.00", "currency": "SAR"},
            },
            "items": [{
                "sku": "SKU-A", "name": "A", "quantity": 2,
                "product": {"id": "p1", "sku": "SKU-A", "name": "A"},
                "amounts": {
                    "price_without_tax": {"amount": "100.00", "currency": "SAR"},
                    "total": {"amount": "217.39", "currency": "SAR"},
                    "tax": {"percent": 15,
                            "amount": {"amount": "32.61", "currency": "SAR"}},
                },
            }],
        },
    }


async def _post(http, body):
    r = await http.post("/webhook", json=body, headers=HEADERS)
    assert r.status_code in (200, 201), f"{r.status_code} → {r.text}"
    return r.json()


# ── Tests ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_status_return_completed_reappears_in_pending(db, http):
    """The exact user-reported scenario (2026-02):
    completed → under_review → completed  ⇒  visible in Plan B again."""
    order_id = "271438176"

    # 1) First `completed` webhook  → row A inserted.
    r1 = await _post(http, _salla_body(order_id, "completed", "تم التنفيذ"))
    assert r1["ok"] is True
    assert r1.get("duplicate") is not True

    # Sanity: shows in Plan B completed tab.
    p1 = await list_pending_orders(db, user_id=TENANT, days=365,
                                   status="completed")
    assert any(o["order_number"] == order_id for o in p1["orders"])

    # 2) Status flips to `under_review`  → row C inserted.
    r2 = await _post(http, _salla_body(order_id, "under_review",
                                       "بانتظار المراجعة"))
    assert r2["ok"] is True
    assert r2.get("duplicate") is not True

    # Correctly disappears from completed tab.
    p2 = await list_pending_orders(db, user_id=TENANT, days=365,
                                   status="completed")
    assert not any(o["order_number"] == order_id for o in p2["orders"])

    # 3) Status returns to `completed`  → hits DuplicateKeyError,
    #    fix must bump received_at on row A.
    r3 = await _post(http, _salla_body(order_id, "completed", "تم التنفيذ"))
    assert r3["ok"] is True
    assert r3["duplicate"] is True   # confirms we exercised the fix branch

    # 4) MUST reappear in Plan B completed tab (user acceptance #3).
    p3 = await list_pending_orders(db, user_id=TENANT, days=365,
                                   status="completed")
    matches = [o for o in p3["orders"] if o["order_number"] == order_id]
    assert len(matches) == 1, (
        f"Order {order_id} did NOT reappear after status returned to "
        f"completed. Pending orders: {[o['order_number'] for o in p3['orders']]}"
    )

    # Not duplicated (user acceptance #4).
    completed_count = sum(1 for o in p3["orders"] if o["order_number"] == order_id)
    assert completed_count == 1


@pytest.mark.asyncio
async def test_duplicate_replay_updates_received_at_only(db, http):
    """The fix must ONLY touch `received_at` + append a stage-history
    entry. It must NEVER touch invoice markers, pipeline_stage, or
    canonical_payload — regression guard against future creep."""
    order_id = "271438177"

    # First completed webhook.
    await _post(http, _salla_body(order_id, "completed", "تم التنفيذ"))

    # Simulate that Plan B already sent this order to قيود.
    await db.integration_inbox.update_one(
        {"salla_order_number": order_id},
        {"$set": {
            "manual_qoyod_invoice_id": "999123",
            "qoyod_invoice_id":         "999123",
            "pipeline_stage":           "COMPLETED",
        }},
    )
    before = await db.integration_inbox.find_one(
        {"salla_order_number": order_id})
    before_stage = before["pipeline_stage"]
    before_invoice = before["manual_qoyod_invoice_id"]
    before_received = before["received_at"]
    before_canonical_status = (before.get("canonical_payload")
                               or {}).get("order_status")

    # A duplicate completed webhook fires (Salla replay).
    r = await _post(http, _salla_body(order_id, "completed", "تم التنفيذ"))
    assert r["duplicate"] is True

    after = await db.integration_inbox.find_one(
        {"salla_order_number": order_id})

    # received_at bumped.
    assert after["received_at"] > before_received

    # Nothing else changed.
    assert after["pipeline_stage"] == before_stage == "COMPLETED"
    assert after["manual_qoyod_invoice_id"] == before_invoice == "999123"
    assert after["qoyod_invoice_id"] == "999123"
    assert (after.get("canonical_payload")
            or {}).get("order_status") == before_canonical_status

    # stage_history grew with exactly one DUPLICATE_REPLAY entry.
    latest = after["stage_history"][-1]
    assert latest["stage"] == "DUPLICATE_REPLAY"
    assert latest["actor"] == "webhook"


@pytest.mark.asyncio
async def test_no_new_row_on_duplicate(db, http):
    """User acceptance #4: no duplication.

    Even though we `$set` `received_at` on duplicate, we NEVER create a
    second row for the same idempotency key.
    """
    order_id = "271438178"
    body = _salla_body(order_id, "completed", "تم التنفيذ")

    await _post(http, body)
    await _post(http, body)     # duplicate replay
    await _post(http, body)     # another replay

    count = await db.integration_inbox.count_documents(
        {"salla_order_number": order_id})
    assert count == 1
