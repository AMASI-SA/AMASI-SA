"""Offline synthetic tests: no Salla request, Mongo/Chatwoot or PII required."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from salla_order_creation.payload import PendingOrder
from salla_order_creation.service import (
    CreationDenied, create_confirmed_salla_order, order_digest,
)


class FakeCollection:
    def __init__(self, rows=()):
        self.rows = [dict(r) for r in rows]

    async def find_one(self, query):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                return dict(row)
        return None

    async def insert_one(self, doc):
        if any(row["_id"] == doc["_id"] for row in self.rows):
            raise ValueError("duplicate synthetic _id")
        self.rows.append(dict(doc))

    async def update_one(self, query, update):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                row.update(update.get("$set", {}))
                return
        raise ValueError("synthetic update missing")


class FakeDB:
    def __init__(self, quotes=(), confirmations=()):
        self.salla_ai_order_quotes = FakeCollection(quotes)
        self.salla_ai_order_confirmations = FakeCollection(confirmations)
        self.salla_ai_order_intents = FakeCollection()


@pytest.fixture
def salla_pending_order():
    return PendingOrder.model_validate({
        "customer": {"id": 123},
        "receiver": {
            "name": "Synthetic Customer", "phone": "+966500000000", "country_code": "SA",
        },
        "branch_id": 101, "courier_id": "202",
        "ship_to": {
            "country": 1, "city": 2, "district": 3, "block": None,
            "street_number": "11", "address_line": "Synthetic Address",
            "postal_code": "12345", "short_address": "ABCD1234",
            "building_number": "11", "additional_number": "22",
            "geo_coordinates": {"lat": 24.7, "lng": 46.7},
        },
        "accepted_methods": ["mada"],
        "products": [{"identifier_type": "id", "identifier": 400, "quantity": 1}],
    })


def fixture_db(order, **edits):
    now = datetime.now(timezone.utc)
    common = {
        "user_id": "owner", "intent_id": "intent-0001",
        "inbox_id": "inbox-1", "conversation_id": "chat-1",
        "payload_digest": order_digest(order),
    }
    quote = {
        **common, "source": "mezan_authoritative_quote_v1", "state": "verified",
        "version": 3, "verified_at": now - timedelta(minutes=1),
        "expires_at": now + timedelta(minutes=10), "total_sar": "200.00",
        "products_verified": True, "shipping_verified": True,
        "pricing_verified": True, "payment_methods_verified": True,
    }
    confirm = {
        **common, "source": "chatwoot_customer_inbound_v1",
        "confirmed": True, "quote_version": 3, "message_id": "customer-msg-1",
        "confirmed_at": now - timedelta(seconds=30),
    }
    quote.update(edits.get("quote", {}))
    confirm.update(edits.get("confirm", {}))
    return FakeDB([quote], [confirm])


async def call_create(db, order, provider, *, enabled=True, intent="intent-0001",
                      conversation="chat-1", actor="owner"):
    return await create_confirmed_salla_order(
        db, user_id="owner", actor_id=actor, intent_id=intent,
        inbox_id="inbox-1", conversation_id=conversation,
        order=order, post_salla=provider, enabled=enabled,
    )


def test_exactly_one_post_and_replay(salla_pending_order):
    db = fixture_db(salla_pending_order)
    calls = []

    async def salla(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "data": {"id": 90001, "reference_id": 80001}}

    first = asyncio.run(call_create(db, salla_pending_order, salla))
    second = asyncio.run(call_create(db, salla_pending_order, salla))
    assert first == {
        "state": "created", "salla_order_id": 90001,
        "reference_id": 80001, "replayed": False,
    }
    assert second["replayed"] is True
    assert second["salla_order_id"] == 90001
    assert len(calls) == 1
    assert calls[0][0][2:] == ("POST", "/orders")
    assert calls[0][1]["json"]["payment"]["status"] == "pending_payment"
    assert "checkout_url" not in str(calls[0][1]["json"])
    assert len(db.salla_ai_order_intents.rows) == 1


def test_disabled_by_default_even_with_full_proofs(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def fail(*args, **kwargs):
        raise AssertionError("provider must not be reached")
    with pytest.raises(CreationDenied, match="disabled"):
        asyncio.run(call_create(db, salla_pending_order, fail, enabled=False))
    assert not db.salla_ai_order_intents.rows


@pytest.mark.parametrize("quote_change,confirmation_change", [
    ({"state": "draft"}, {}),
    ({"total_sar": "0"}, {}),
    ({"total_sar": "NaN"}, {}),
    ({"products_verified": False}, {}),
    ({"shipping_verified": False}, {}),
    ({"payment_methods_verified": False}, {}),
    ({"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}, {}),
    ({}, {"confirmed": False}),
    ({}, {"message_id": ""}),
    ({}, {"source": "assistant_message"}),
    ({}, {"quote_version": 2}),
    ({}, {"conversation_id": "wrong-chat"}),
])
def test_missing_independent_proof_never_posts(
    salla_pending_order, quote_change, confirmation_change,
):
    db = fixture_db(salla_pending_order, quote=quote_change, confirm=confirmation_change)
    async def fail(*args, **kwargs):
        raise AssertionError("must not reach Salla")
    with pytest.raises(CreationDenied):
        asyncio.run(call_create(db, salla_pending_order, fail))
    assert db.salla_ai_order_intents.rows == []


def test_incorrect_conversation_never_posts(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def fail(*args, **kwargs):
        raise AssertionError("must not post")
    with pytest.raises(CreationDenied):
        asyncio.run(call_create(db, salla_pending_order, fail, conversation="chat-2"))


def test_provider_timeout_is_unknown_and_not_retried(salla_pending_order):
    db = fixture_db(salla_pending_order)
    calls = []
    async def timeout(*args, **kwargs):
        calls.append("POST")
        raise TimeoutError("synthetic timeout")
    first = asyncio.run(call_create(db, salla_pending_order, timeout))
    second = asyncio.run(call_create(db, salla_pending_order, timeout))
    assert first["state"] == "unknown"
    assert first["needs_reconciliation"] is True
    assert second["state"] == "unknown" and second["replayed"] is True
    assert calls == ["POST"]


def test_missing_salla_order_id_is_unknown(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def salla(*args, **kwargs):
        return {"success": False, "data": {}}
    result = asyncio.run(call_create(db, salla_pending_order, salla))
    assert result["state"] == "unknown"


def test_payload_change_after_confirmation_denied(salla_pending_order):
    db = fixture_db(salla_pending_order)
    altered = salla_pending_order.model_copy(deep=True)
    altered.products[0].quantity = 2
    async def fail(*args, **kwargs):
        raise AssertionError("must not post")
    with pytest.raises(CreationDenied):
        asyncio.run(call_create(db, altered, fail))
    assert not db.salla_ai_order_intents.rows


def test_invalid_actor_rejected(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def fail(*args, **kwargs):
        raise AssertionError("must not post")
    with pytest.raises(CreationDenied, match="authenticated_context"):
        asyncio.run(call_create(db, salla_pending_order, fail, actor=""))


def test_same_intent_cannot_be_reused_with_different_order(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def salla(*args, **kwargs):
        return {"data": {"id": 123}}
    asyncio.run(call_create(db, salla_pending_order, salla))
    altered = salla_pending_order.model_copy(deep=True)
    altered.products[0].quantity = 3
    # This is denied even before duplicate lookup due to quote digest mismatch.
    with pytest.raises(CreationDenied):
        asyncio.run(call_create(db, altered, salla))
