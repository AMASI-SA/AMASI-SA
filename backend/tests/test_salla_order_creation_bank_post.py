"""Synthetic bank Salla create: one unpaid order + one pending human review."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from salla_order_creation.bank_receipt_review import _key
from salla_order_creation.bank_transfer import bank_order_digest
from salla_order_creation.payload import PendingOrder
from salla_order_creation.service import (
    CreationDenied, create_confirmed_salla_order,
)

NOW = datetime.now(timezone.utc)


class Rows:
    def __init__(self, rows=()):
        self.rows = [dict(x) for x in rows]

    async def find_one(self, query):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                return dict(row)
        return None

    async def insert_one(self, doc):
        if any(x["_id"] == doc["_id"] for x in self.rows):
            raise ValueError("Mongo unique synthetic _id")
        self.rows.append(dict(doc))

    async def update_one(self, query, updates):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                row.update(updates["$set"])
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class DB:
    def __init__(self, order, **changes):
        digest = bank_order_digest(order, store_bank_id=123)
        q = {
            "user_id": "owner", "intent_id": "intent-0001",
            "inbox_id": "2", "conversation_id": "3",
            "source_contact_id": 77, "quote_ref": "quote.0001",
            "version": 1, "payload_digest": digest,
            "source": "mezan_bank_receipt_pending_review_v1",
            "state": "authorized_for_pending_creation",
            "bank_review_only": True, "bank_credit_confirmed": False,
            "receipt_appearance_checked": True,
            "post_create_salla_total": True, "online_only": False,
            "pricing_verified": False, "shipping_price_verified": False,
            "total_sar": None,
            "store_bank_id": 123,
            "receipt_preflight_id": _key("owner", "intent-0001"),
            "receipt_claimed_amount_sar": "200.00",
            "receipt_image_sha256": "b" * 64,
            "products_verified": True,
            "carrier_eligibility_verified": True,
            "payment_methods_verified": True,
            "verified_at": NOW - timedelta(seconds=50),
            "expires_at": NOW + timedelta(minutes=10),
        }
        q.update(changes.get("quote", {}))
        c = {
            "user_id": "owner", "intent_id": "intent-0001",
            "inbox_id": "2", "conversation_id": "3",
            "payload_digest": digest,
            "quote_version": 1, "source": "chatwoot_customer_inbound_v1",
            "source_contact_id": 77, "confirmed": True,
            "message_id": "501",
            "confirmed_at": NOW - timedelta(seconds=10),
        }
        c.update(changes.get("confirmation", {}))
        self.salla_ai_order_quotes = Rows([q])
        self.salla_ai_order_confirmations = Rows([c])
        self.salla_ai_order_intents = Rows()
        self.salla_ai_bank_receipt_preflights = Rows([{
            "_id": _key("owner", "intent-0001"),
            "owner_id": "owner", "intent_id": "intent-0001",
            "store_bank_id": 123, "contact_id": 77,
            "state": "preliminary_pass_for_human_review",
            "bank_credit_confirmed": False,
            "claimed_amount_sar": "200.00",
            "image_sha256": "b" * 64,
            "account_id": 1, "inbox_id": 2, "conversation_id": 3,
            "source_message_id": 500, "attachment_id": 900,
            "reference_sha256": "a" * 64,
        }])
        self.salla_ai_bank_receipt_reviews = Rows()


def order():
    return PendingOrder.model_validate({
        "customer": {"id": 501},
        "receiver": {
            "name": "Synthetic Recipient", "phone": "+966500000000",
            "country_code": "SA",
        },
        "courier_id": "220",
        "ship_to": {
            "country": 1, "city": 2, "district": 3, "block": None,
            "street_number": "11", "address_line": "Synthetic street",
            "postal_code": "12345", "short_address": "RHMA3184",
            "building_number": "11", "additional_number": "22",
            "geo_coordinates": {"lat": 24.7, "lng": 46.7},
        },
        "accepted_methods": ["bank"],
        "products": [{"identifier_type": "id", "identifier": 400, "quantity": 1}],
    })


def execute(db=None, provider=None):
    db = db or DB(order())

    async def fake_post(db_arg, user, method, path, **kwargs):
        return {"success": True, "data": {
            "id": 90001, "reference_id": 301234,
            "status": {"slug": "under_review"},
            "payment_method": "bank",
            "amounts": {"total": {"amount": 210, "currency": "SAR"}},
        }}

    return db, asyncio.run(create_confirmed_salla_order(
        db, user_id="owner", actor_id="signed-customer-service",
        intent_id="intent-0001", inbox_id="2", conversation_id="3",
        order=order(), post_salla=provider or fake_post, enabled=True,
    ))


def test_one_bank_pending_order_and_human_review_no_gl():
    calls = []

    async def salla(db, owner, method, path, **kwargs):
        calls.append((method, path, kwargs["json"]))
        return {"success": True, "data": {
            "id": 90001, "reference_id": 301234,
            "status": {"slug": "under_review"},
            "payment_method": "bank",
            "amounts": {"total": {"amount": 210, "currency": "SAR"}},
        }}

    db, result = execute(provider=salla)
    assert result["state"] == "created_amount_unverified"
    assert result["pricing_mode"] == "bank_pending_review"
    assert result["bank_review_state"] == "awaiting_human_bank_review"
    assert result["bank_credit_confirmed"] is False
    assert result["human_review_required"] is True
    assert len(calls) == 1
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/orders")
    assert body["payment"] == {
        "status": "pending_payment", "method": "bank",
        "store_bank_id": 123, "accepted_methods": ["bank"],
    }
    assert "receipt_image_path" not in body["payment"]
    assert "checkout_url" not in str(body)
    assert len(db.salla_ai_bank_receipt_reviews.rows) == 1
    review = db.salla_ai_bank_receipt_reviews.rows[0]
    assert review["bank_credit_confirmed"] is False
    assert review["gl_posted"] is False
    assert review["payment_marked_paid"] is False
    assert review["fulfillment_released"] is False
    assert not hasattr(db, "general_ledger")


def test_created_bank_order_replay_never_posts_again():
    db, first = execute()
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("second Salla POST forbidden")
    _, replay = execute(db=db, provider=forbidden)
    assert first["salla_order_id"] == replay["salla_order_id"] == 90001
    assert replay["replayed"] is True
    assert replay["bank_review_state"] == "awaiting_human_bank_review"
    assert calls == []


def test_customer_receipt_different_contact_denied_before_salla():
    db = DB(order(), confirmation={"source_contact_id": 99})
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not POST")
    with pytest.raises(CreationDenied, match="independent_customer_confirmation_required"):
        execute(db=db, provider=forbidden)
    assert db.salla_ai_order_intents.rows == []


def test_receipt_preflight_forged_paid_claim_denied_before_salla():
    db = DB(order())
    db.salla_ai_bank_receipt_preflights.rows[0]["bank_credit_confirmed"] = True
    with pytest.raises(CreationDenied, match="preliminary_bank_receipt_not_verified"):
        execute(db=db)


def test_salla_post_unknown_is_not_retried_or_marked_paid():
    async def timeout(*args, **kwargs):
        raise TimeoutError("provider might have committed")
    db, result = execute(provider=timeout)
    assert result["state"] == "unknown"
    assert result["needs_reconciliation"] is True
    assert db.salla_ai_bank_receipt_reviews.rows == []
    assert db.salla_ai_order_intents.rows[0]["state"] == "unknown"


def test_bank_review_ledger_failure_keeps_order_on_hold():
    db = DB(order())
    async def failing_insert(doc):
        raise ConnectionError("review collection unavailable")
    db.salla_ai_bank_receipt_reviews.insert_one = failing_insert
    _, result = execute(db=db)
    assert result["state"] == "created_bank_review_unavailable"
    assert result["salla_order_id"] == 90001
    assert result["bank_credit_confirmed"] is False
    assert result["needs_reconciliation"] is True


def test_unconfirmed_bank_order_does_not_create_anything():
    db = DB(order(), confirmation={"confirmed": False})
    with pytest.raises(CreationDenied):
        execute(db=db)
    assert db.salla_ai_order_intents.rows == []
    assert db.salla_ai_bank_receipt_reviews.rows == []


def test_same_order_receipt_but_changed_bank_id_digest_denied():
    db = DB(order(), quote={"store_bank_id": 124})
    with pytest.raises(CreationDenied, match="fresh_server_verified_quote_required"):
        execute(db=db)
