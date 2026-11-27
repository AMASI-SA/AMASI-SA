"""Offline bank receipt → Salla verified unpaid bank order intent tests."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from salla_order_creation.bank_quote_writer import (
    PrepareBankOrderDenied, prepare_bank_receipt_order_intent,
)
from salla_order_creation.bank_receipt_review import _key
from salla_order_creation.bank_transfer import bank_order_digest
from salla_order_creation.payload import PendingOrder
from salla_order_creation.quote_cipher import decrypt_order_payload

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


class Rows:
    def __init__(self, rows=()):
        self.rows = [dict(r) for r in rows]

    async def find_one(self, query):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                return dict(row)
        return None

    async def insert_one(self, row):
        if any(r["_id"] == row["_id"] for r in self.rows):
            raise ValueError("unique _id conflict")
        self.rows.append(dict(row))


class DB:
    def __init__(self, **receipt):
        self.salla_ai_bank_receipt_preflights = Rows([{
            "_id": _key("owner", "intent-0001"),
            "owner_id": "owner", "intent_id": "intent-0001",
            "inbox_id": 2, "conversation_id": 3, "contact_id": 77,
            "state": "preliminary_pass_for_human_review",
            "human_review_required": True, "bank_credit_confirmed": False,
            "store_bank_id": 123, "claimed_amount_sar": "200.00",
            "image_sha256": "a" * 64, "created_at": NOW,
            **receipt,
        }])
        self.salla_ai_order_quotes = Rows()


def order(methods=None):
    return PendingOrder.model_validate({
        "customer": {"id": 501},
        "receiver": {
            "name": "Synthetic Gift", "phone": "+966500000000",
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
        "accepted_methods": methods or ["bank"],
        "products": [{"identifier_type": "id", "identifier": 400, "quantity": 1}],
    })


async def product(db, owner, pid):
    assert (owner, pid) == ("owner", 400)
    return {
        "product_id": 400, "source": "salla_product_details",
        "can_use_as_final_quote": False,
        "available_quantity": 4, "taxed_base_price_sar": "80.00",
        "options": [], "variants": [],
    }


async def rate(db, owner, **query):
    assert query["country_id"] == 1 and query["city_id"] == 2
    return [{
        "rate_id": 888, "shipping_company_id": 220,
        "destination_country_id": 1, "destination_city_id": 2,
        "estimated_shipping_sar": "17.25",
        "cod_service_found": False, "cod_fee_estimate_sar": None,
        "source": "salla_shipping_rate_estimate",
        "is_final_cart_quote": False,
    }]


async def methods(db, owner):
    return {
        "source": "salla_payment_methods",
        "eligible_create_order_method_candidates": ["bank", "mada"],
        "can_create_order": False,
    }


async def bank(db, owner, bid):
    assert bid == 123
    return {
        "source": "salla_active_store_bank_details",
        "store_bank_id": 123,
        "bank_name": "Synthetic Bank",
        "beneficiary_name": "Synthetic Merchant",
        "active": True, "payment_funds_verified": False,
    }


@pytest.fixture(autouse=True)
def private_key(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_KEY",
        Fernet.generate_key().decode("ascii"),
    )


def prepare(db=None, o=None, bank_reader=bank, methods_reader=methods,
            now=NOW, contact_id=77):
    db = db or DB()
    value = asyncio.run(prepare_bank_receipt_order_intent(
        db, owner_id="owner", intent_id="intent-0001",
        inbox_id="2", conversation_id="3",
        source_contact_id=contact_id, source_message_id=905,
        order=o or order(), selected_rate_id=888, enabled=True,
        product_reader=product, rate_reader=rate,
        methods_reader=methods_reader, bank_reader=bank_reader,
        now=now,
    ))
    return db, value


def test_bank_quote_stores_only_encrypted_order_and_no_false_paid_flag():
    db, outcome = prepare()
    assert outcome["pricing_mode"] == "bank_pending_review"
    assert outcome["bank_credit_confirmed"] is False
    assert outcome["human_review_required"] is True
    assert outcome["verified_total_sar"] is None
    assert outcome["checkout_url"] is None
    quote = db.salla_ai_order_quotes.rows[0]
    assert quote["state"] == "authorized_for_pending_creation"
    assert quote["bank_review_only"] is True
    assert quote["total_sar"] is None
    assert quote["bank_credit_confirmed"] is False
    assert quote["receipt_appearance_checked"] is True
    assert quote["store_bank_id"] == 123
    assert quote["source_contact_id"] == 77
    assert "order_payload" not in quote
    assert b"+966500000000" not in quote["encrypted_order_payload"]
    actual = decrypt_order_payload(
        quote["encrypted_order_payload"], owner_id="owner",
        intent_id="intent-0001",
    )
    assert actual.accepted_methods == ["bank"]
    assert quote["payload_digest"] == bank_order_digest(
        actual, store_bank_id=123,
    )


def test_duplicate_prepared_intent_has_same_quote_identity():
    db = DB()
    _, first = prepare(db=db)
    _, repeated = prepare(db=db)
    assert first == repeated
    assert len(db.salla_ai_order_quotes.rows) == 1


def test_same_intent_changed_contact_is_denied():
    db = DB()
    prepare(db=db)
    with pytest.raises(PrepareBankOrderDenied):
        prepare(db=db, contact_id=99)


@pytest.mark.parametrize("change", [
    {"state": "model_estimated"},
    {"bank_credit_confirmed": True},
    {"store_bank_id": None},
    {"contact_id": 99},
    {"human_review_required": False},
])
def test_unverified_vision_or_other_contact_never_creates_quote(change):
    db = DB(**change)
    with pytest.raises(PrepareBankOrderDenied):
        prepare(db=db)
    assert db.salla_ai_order_quotes.rows == []


@pytest.mark.parametrize("payment", [["mada"], ["cod"], ["bank", "mada"]])
def test_bank_quote_rejects_other_payment_methods(payment):
    with pytest.raises(PrepareBankOrderDenied, match="bank_transfer_order_scope"):
        prepare(o=order(payment))


def test_inactive_or_unverified_salla_receiving_bank_denied():
    async def inactive(*args):
        return {**(await bank(*args)), "active": False}
    with pytest.raises(PrepareBankOrderDenied, match="active_salla_bank_required"):
        prepare(bank_reader=inactive)


def test_bank_method_not_enabled_in_salla_blocks_quote():
    async def disabled(*args):
        return {
            "source": "salla_payment_methods",
            "eligible_create_order_method_candidates": ["mada"],
            "can_create_order": False,
        }
    with pytest.raises(PrepareBankOrderDenied, match="bank_payment_not_enabled"):
        prepare(methods_reader=disabled)


def test_expired_receipt_will_not_create_stale_bank_order():
    from datetime import timedelta
    with pytest.raises(PrepareBankOrderDenied, match="stale_bank_receipt"):
        prepare(now=NOW + timedelta(days=2))


def test_bank_claimed_amount_does_not_become_paid_or_verified_cart_total():
    db, outcome = prepare()
    assert "receipt_claimed_amount_sar" not in outcome
    assert db.salla_ai_order_quotes.rows[0]["receipt_claimed_amount_sar"] == "200.00"
    assert db.salla_ai_order_quotes.rows[0]["total_sar"] is None
    assert "paid" not in str(outcome["state"])
