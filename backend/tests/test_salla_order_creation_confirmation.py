"""Synthetic-only tests of signed customer proof persistence (no Salla POST)."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from salla_order_creation.confirmation import (
    ConfirmationDenied,
    SignedCustomerConfirmation,
    persist_signed_confirmation,
    verify_confirmation,
)
from salla_order_creation.signature import SignatureDenied, sign_action

NOW = datetime.fromtimestamp(1_800_000_000, tz=timezone.utc)
STAMP = "1800000000"
DELIVERY = str(UUID("12345678-1234-4234-8234-123456789abc"))
SECRET = b"synthetic-test-secret-please-never-deploy-0001"
DIGEST = "a" * 64


def action(**kwargs):
    values = {
        "action": "salla.confirm_pending_order",
        "account_id": 1, "inbox_id": 2, "conversation_id": 3,
        "intent_id": "intent-1234",
        "quote_ref": "quote.001", "quote_digest": DIGEST,
        "quote_version": 4, "confirmation_message_id": 501,
        "message_created_at": 1_799_999_990, "source_id_sha256": "b" * 64,
    }
    values.update(kwargs)
    return values


def signed(data):
    raw = json.dumps(data, sort_keys=True).encode()
    return verify_confirmation(
        raw, STAMP, DELIVERY,
        sign_action(raw, STAMP, DELIVERY, SECRET),
        SECRET, now=int(NOW.timestamp()),
    )


class FakeRows:
    def __init__(self, data=()):
        self.rows = [dict(x) for x in data]

    async def find_one(self, query):
        for row in self.rows:
            if all(row.get(k) == v for k,v in query.items()):
                return dict(row)
        return None

    async def insert_one(self, doc):
        if any(x["_id"] == doc["_id"] for x in self.rows):
            raise ValueError("duplicate synthetic Mongo _id")
        self.rows.append(dict(doc))


class DB:
    def __init__(self, quote=None):
        if quote is None:
            quote = valid_quote()
        self.salla_ai_order_quotes = FakeRows([quote])
        self.salla_ai_order_confirmations = FakeRows()


def valid_quote(**extra):
    data = {
        "user_id": "owner", "intent_id": "intent-1234",
        "inbox_id": "2", "conversation_id": "3",
        "quote_ref": "quote.001", "payload_digest": DIGEST,
        "version": 4, "state": "verified",
        "source": "mezan_authoritative_quote_v1",
        "verified_at": NOW - timedelta(seconds=60),
        "expires_at": NOW + timedelta(minutes=10),
        "total_sar": "150.00",
        "products_verified": True,
        "shipping_verified": True, "pricing_verified": True,
        "payment_methods_verified": True,
    }
    data.update(extra)
    return data


async def persist(db, a=None, **kwargs):
    return await persist_signed_confirmation(
        db, owner_id="owner",
        expected_account_id=1, expected_inbox_id=2,
        action=a or signed(action()), now=NOW, **kwargs,
    )


def test_signed_customer_proof_is_persisted_without_customer_data():
    db = DB()
    result = asyncio.run(persist(db))
    assert result["state"] == "recorded"
    assert result["replayed"] is False
    doc = db.salla_ai_order_confirmations.rows[0]
    assert doc["confirmed"] is True
    assert doc["source"] == "chatwoot_customer_inbound_v1"
    assert doc["payload_digest"] == DIGEST
    assert doc["message_id"] == "501"
    assert "mobile" not in doc and "text" not in doc
    assert "checkout_url" not in doc and "token" not in doc
    assert doc["confirmed_at"] == NOW


def test_same_confirmation_replay_is_idempotent():
    db = DB()
    first = asyncio.run(persist(db))
    second = asyncio.run(persist(db))
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert len(db.salla_ai_order_confirmations.rows) == 1


def test_changed_message_same_intent_is_rejected():
    db = DB()
    asyncio.run(persist(db))
    with pytest.raises(ConfirmationDenied, match="incompatible_confirmation_replay"):
        asyncio.run(persist(db, signed(action(confirmation_message_id=502))))


@pytest.mark.parametrize("modified", [
    {"quote_digest": "b" * 64},
    {"quote_version": 7},
    {"quote_ref": "quote.other"},
    {"inbox_id": 99},
    {"conversation_id": 44},
    {"account_id": 99},
    {"message_created_at": 1_799_999_880},
    {"message_created_at": 1_800_000_050},
])
def test_wrong_or_old_scoped_proof_fails_closed(modified):
    db = DB()
    with pytest.raises(ConfirmationDenied):
        asyncio.run(persist(db, signed(action(**modified))))
    assert db.salla_ai_order_confirmations.rows == []


@pytest.mark.parametrize("quote_changed", [
    {"state": "draft"},
    {"source": "model"},
    {"pricing_verified": False},
    {"products_verified": False},
    {"shipping_verified": False},
    {"payment_methods_verified": False},
    {"expires_at": NOW - timedelta(seconds=1)},
])
def test_unverified_quote_does_not_grant_consent(quote_changed):
    db = DB(valid_quote(**quote_changed))
    with pytest.raises(ConfirmationDenied):
        asyncio.run(persist(db))
    assert db.salla_ai_order_confirmations.rows == []


@pytest.mark.parametrize("unsafe", [
    {"action": "salla.create_pending_order"},
    {"checkout_url": "https://example.invalid"},
    {"customer": {"mobile": "+966500000000"}},
    {"payment": {"status": "paid"}},
    {"confirmed": True},
])
def test_signed_payload_cannot_contain_model_or_payment_mutations(unsafe):
    with pytest.raises(SignatureDenied):
        signed(action(**unsafe))


def test_tampered_signature_rejected():
    raw = json.dumps(action()).encode()
    signature = sign_action(raw, STAMP, DELIVERY, SECRET)
    with pytest.raises(SignatureDenied):
        verify_confirmation(
            raw.replace(b"quote.001",b"quote.other"), STAMP, DELIVERY,
            signature, SECRET, now=int(NOW.timestamp()),
        )


def test_duplicate_json_field_rejected():
    raw = b'{"action":"salla.confirm_pending_order","action":"salla.mark_paid"}'
    signature = sign_action(raw, STAMP, DELIVERY, SECRET)
    with pytest.raises(SignatureDenied):
        verify_confirmation(
            raw, STAMP, DELIVERY, signature, SECRET,
            now=int(NOW.timestamp()),
        )


def test_bad_tenant_environment_fails_closed():
    db = DB()
    with pytest.raises(ConfirmationDenied):
        asyncio.run(persist_signed_confirmation(
            db, owner_id="", expected_account_id=1,
            expected_inbox_id=2, action=signed(action()), now=NOW,
        ))
