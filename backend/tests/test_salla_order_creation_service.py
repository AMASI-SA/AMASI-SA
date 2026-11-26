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
        return {"success": True, "data": {
            "id": 90001, "reference_id": 80001,
            "amounts": {"total": {"amount": 200, "currency": "SAR"}},
        }}

    first = asyncio.run(call_create(db, salla_pending_order, salla))
    second = asyncio.run(call_create(db, salla_pending_order, salla))
    assert first == {
        "state": "created", "salla_order_id": 90001,
        "reference_id": 80001, "replayed": False, "amount_confirmed": True,
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


def test_changed_salla_total_records_order_without_authorizing_checkout(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def salla(*args, **kwargs):
        return {"success": True, "data": {
            "id": 90002, "reference_id": 80002,
            "amounts": {"total": {"amount": 210, "currency": "SAR"}},
        }}
    result = asyncio.run(call_create(db, salla_pending_order, salla))
    assert result["state"] == "created_amount_unverified"
    assert result["salla_order_id"] == 90002
    assert result["amount_confirmed"] is False


def test_missing_salla_total_is_not_marked_verified(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def salla(*args, **kwargs):
        return {"success": True, "data": {"id": 90003}}
    result = asyncio.run(call_create(db, salla_pending_order, salla))
    assert result["state"] == "created_amount_unverified"
    assert result["amount_confirmed"] is False


def test_foreign_currency_requires_separate_reconciliation(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def salla(*args, **kwargs):
        return {"success": True, "data": {
            "id": 90004, "amounts": {"total": {"amount": 200, "currency": "KWD"}},
        }}
    result = asyncio.run(call_create(db, salla_pending_order, salla))
    assert result["state"] == "created_amount_unverified"



def test_provider_success_false_with_numeric_id_never_authorizes_checkout(salla_pending_order):
    db = fixture_db(salla_pending_order)
    async def salla(*args, **kwargs):
        return {"success": False, "data": {
            "id": 90006, "amounts": {"total": {"amount": 200, "currency": "SAR"}},
        }}
    result = asyncio.run(call_create(db, salla_pending_order, salla))
    assert result["state"] == "unknown"
    assert result["needs_reconciliation"] is True
    assert result["salla_order_id"] is None


def test_provider_failure_duplicate_intent_cannot_post_again(salla_pending_order):
    db = fixture_db(salla_pending_order)
    sent = []
    async def salla(*args, **kwargs):
        sent.append(1)
        return {"success": False, "data": {"id": 90007}}
    first = asyncio.run(call_create(db, salla_pending_order, salla))
    second = asyncio.run(call_create(db, salla_pending_order, salla))
    assert first["state"] == "unknown"
    assert second["replayed"] is True
    assert len(sent) == 1


def test_mongo_naive_utc_proofs_work_without_local_timezone(salla_pending_order):
    db=fixture_db(salla_pending_order)
    quote=db.salla_ai_order_quotes.rows[0]
    confirmation=db.salla_ai_order_confirmations.rows[0]
    for key in ("verified_at","expires_at"):
        quote[key]=quote[key].replace(tzinfo=None)
    confirmation["confirmed_at"]=confirmation["confirmed_at"].replace(tzinfo=None)
    async def salla(*args,**kwargs):
        return {"success": True, "data": {
            "id": 90111, "amounts": {"total": {"amount": 200, "currency": "SAR"}},
        }}
    result=asyncio.run(call_create(db,salla_pending_order,salla))
    assert result["state"]=="created"
    assert result["amount_confirmed"] is True


POST_CREATE_QUOTE = {
    "source": "mezan_verified_unpaid_order_intent_v1",
    "state": "authorized_for_pending_creation",
    "post_create_salla_total": True,
    "online_only": True,
    "products_verified": True,
    "carrier_eligibility_verified": True,
    "shipping_price_verified": False,
    "pricing_verified": False,
    "payment_methods_verified": True,
    "total_sar": None,
}


def test_post_create_salla_pricing_allows_only_unpaid_online_order(salla_pending_order):
    db = fixture_db(salla_pending_order, quote=POST_CREATE_QUOTE)
    calls = []
    async def salla(*args, **kwargs):
        calls.append(1)
        return {"success": True, "data": {
            "id": 90701, "reference_id": 30102,
            "amounts": {"total": {"amount": 250, "currency": "SAR"}},
        }}
    result = asyncio.run(call_create(db, salla_pending_order, salla))
    assert result["state"] == "created_amount_unverified"
    assert result["amount_confirmed"] is False
    assert result["salla_order_id"] == 90701
    assert calls == [1]
    assert db.salla_ai_order_intents.rows[0]["pricing_mode"] == "salla_post_create"
    assert db.salla_ai_order_intents.rows[0]["inbox_id"] == "inbox-1"


def test_post_create_salla_pricing_denies_cod_bank_before_provider(salla_pending_order):
    for method in ("bank", "cod"):
        order = salla_pending_order.model_copy(deep=True)
        order.accepted_methods = [method]
        db = fixture_db(order, quote=POST_CREATE_QUOTE)
        async def unreachable(*args, **kwargs):
            raise AssertionError("Salla must not be called")
        with pytest.raises(CreationDenied, match="requires_online_payment"):
            asyncio.run(call_create(db, order, unreachable))
        assert db.salla_ai_order_intents.rows == []


@pytest.mark.parametrize("changed", [
    {"source": "model_estimate"},
    {"state": "draft"},
    {"post_create_salla_total": False},
    {"online_only": False},
    {"products_verified": False},
    {"carrier_eligibility_verified": False},
    {"payment_methods_verified": False},
    {"shipping_price_verified": True},
    {"pricing_verified": True},
    {"total_sar": "250.00"},
])
def test_post_create_requires_explicit_server_owned_unpriced_intent(
    salla_pending_order, changed
):
    db=fixture_db(salla_pending_order, quote={**POST_CREATE_QUOTE, **changed})
    async def unreachable(*args, **kwargs):
        raise AssertionError("Salla must not be called")
    with pytest.raises(CreationDenied, match="fresh_server_verified_quote_required"):
        asyncio.run(call_create(db,salla_pending_order,unreachable))
    assert db.salla_ai_order_intents.rows == []
