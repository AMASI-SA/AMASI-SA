"""Synthetic Mezan payment-link readback tests; NO real checkout requests."""

import asyncio
import json
from types import SimpleNamespace as Obj
from uuid import UUID

import pytest

from salla_order_creation.checkout_readback import (
    resolve_synced_checkout, validated_salla_checkout_url, verify_checkout_read,
)
from salla_order_creation.service import intent_storage_id
from salla_order_creation.signature import SignatureDenied, sign_action

HOSTS = frozenset({"amasi-sa.com", "checkout.salla.sa"})
SECRET = b"synthetic-readback-secret-only-for-pytest-0001"
STAMP = "1800000000"
UUID_DELIVERY = str(UUID("12345678-1234-4234-8234-123456789abc"))
DIGEST = "a"*64


class Collection:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def find_one(self, query):
        for item in self.rows:
            if all(item.get(k) == v for k, v in query.items()):
                return dict(item)
        return None


class DB:
    def __init__(self, intent=None, quote=None):
        self.salla_ai_order_intents = Collection([intent or {
            "_id": intent_storage_id("owner", "intent-1234"),
            "user_id": "owner", "intent_id": "intent-1234",
            "state": "created", "salla_order_id": 9876,
            "reference_id": 302076044, "payload_digest": DIGEST,
            "quote_version": 4, "confirmation_message_id": "501",
            "inbox_id": "2", "conversation_id": "3",
        }])
        self.salla_ai_order_quotes = Collection([quote or {
            "user_id": "owner", "intent_id": "intent-1234",
            "inbox_id": "2", "conversation_id": "3",
            "quote_ref": "quote.1", "payload_digest": DIGEST,
            "source": "mezan_authoritative_quote_v1",
            "state": "verified", "version": 4, "total_sar": "165.00",
            "products_verified": True, "shipping_verified": True,
            "pricing_verified": True, "payment_methods_verified": True,
        }])


def mezan_order(**overrides):
    o = Obj(
        order_id="9876", order_number="302076044",
        status="waiting", status_native="بانتظار الدفع",
        totals=Obj(currency="SAR", total=165.00),
        payment=Obj(
            status="pending_payment", paid_amount=0.0,
            collection_status="unpaid",
            checkout_url="https://amasi-sa.com/checkout/order-302076044",
        ),
    )
    for k,v in overrides.items():
        setattr(o,k,v)
    return o


def check(db=None, result=None, **scope):
    async def load(owner, ref):
        assert owner == "owner" and ref == "302076044"
        if result is False:
            raise LookupError("not_synced")
        return mezan_order() if result is None else result
    args = dict(
        owner_id="owner", intent_id="intent-1234",
        inbox_id="2", conversation_id="3",
        quote_ref="quote.1", quote_digest=DIGEST,
        quote_version=4, confirmation_message_id=501,
        load_order=load, allowed_hosts=HOSTS,
    )
    args.update(scope)
    return asyncio.run(resolve_synced_checkout(db or DB(), **args))


def test_mezan_exact_order_total_and_link_ready():
    res = check()
    assert res == {
        "state": "ready", "salla_order_id": 9876,
        "reference_id": 302076044,
        "total_sar": "165.00",
        "pricing_mode": "preverified",
        "checkout_url": "https://amasi-sa.com/checkout/order-302076044",
        "amount_verified": True,
    }


def test_mezan_sync_pending_does_not_recreate_order_or_link():
    res = check(result=False)
    assert res["state"] == "waiting_for_mezan_sync"
    assert res["checkout_url"] is None


def test_checkout_not_yet_available_does_not_fabricate_it():
    result = mezan_order()
    result.payment.checkout_url = None
    res = check(result=result)
    assert res["state"] == "waiting_for_verified_mezan_checkout_url"
    assert res["checkout_url"] is None


@pytest.mark.parametrize("total", [165.02, 160.00, 165.50])
def test_total_mismatch_never_exposes_link(total):
    o = mezan_order()
    o.totals.total = total
    res = check(result=o)
    assert res["state"] == "amount_mismatch_needs_customer_review"
    assert res["checkout_url"] is None


def test_tiny_one_halalah_difference_tolerated():
    o = mezan_order()
    o.totals.total = 165.01
    assert check(result=o)["state"] == "ready"


def test_wrong_currency_or_order_id_denied():
    o = mezan_order()
    o.totals.currency = "KWD"
    assert check(result=o)["state"] == "payment_or_currency_not_verified"
    o.totals.currency = "SAR"
    o.order_id = "999999"
    assert check(result=o)["state"] == "synced_order_identity_mismatch"


def test_order_in_human_or_paid_state_never_exposes_link():
    o = mezan_order()
    o.status = "completed"
    o.status_native = "تم التوصيل"
    assert check(result=o)["state"] == "order_not_waiting_for_payment"
    o.status, o.status_native = "waiting", "بانتظار الدفع"
    o.payment.paid_amount = 100
    assert check(result=o)["state"] == "payment_or_currency_not_verified"
    o.payment.paid_amount = 0
    o.payment.collection_status = "partial"
    assert check(result=o)["state"] == "not_an_unpaid_order"


def test_created_without_post_amount_still_can_reconcile_from_mezan():
    db = DB()
    db.salla_ai_order_intents.rows[0]["state"] = "created_amount_unverified"
    assert check(db=db)["state"] == "ready"


@pytest.mark.parametrize("edited", [
    {"state": "unknown"},
    {"reference_id": None},
    {"salla_order_id": 11111},
    {"inbox_id": "9"},
    {"confirmation_message_id": "502"},
    {"quote_version": 7},
])
def test_wrong_or_unknown_intent_never_authorizes_link(edited):
    db = DB()
    db.salla_ai_order_intents.rows[0].update(edited)
    assert check(db=db)["checkout_url"] is None


def test_quote_mutation_or_wrong_conversation_denied():
    db=DB()
    db.salla_ai_order_quotes.rows[0]["total_sar"] = "99.00"
    assert check(db=db)["state"] == "amount_mismatch_needs_customer_review"
    assert check(conversation_id="9")["checkout_url"] is None


@pytest.mark.parametrize("url", [
    "http://amasi-sa.com/checkout/123",
    "https://malicious.invalid/checkout/123",
    "https://amasi-sa.com.evil.invalid/checkout/123",
    "https://user:pass@amasi-sa.com/checkout/123",
    "https://amasi-sa.com:8443/checkout/123",
    "https://amasi-sa.com/admin/orders/123",
    "https://amasi-sa.com/",
    "https://amasi-sa.com/checkout/123?redirect=https://evil.invalid",
    "https://amasi-sa.com/checkout/123\nX-Bad: 1",
])
def test_insecure_payment_urls_not_shared(url):
    assert validated_salla_checkout_url(url, HOSTS) is None
    o = mezan_order()
    o.payment.checkout_url = url
    assert check(result=o)["checkout_url"] is None


def test_explicit_host_allowlist_required():
    assert validated_salla_checkout_url(
        "https://checkout.salla.sa/pay/123", HOSTS,
    ) == "https://checkout.salla.sa/pay/123"
    assert validated_salla_checkout_url(
        "https://checkout.salla.sa/pay/123", frozenset(),
    ) is None


def test_signed_checkout_read_does_not_allow_customer_url_or_creation():
    req = {
        "action": "salla.read_created_order_checkout",
        "account_id": 1, "inbox_id": 2, "conversation_id": 3,
        "intent_id": "intent-1234", "quote_ref": "quote.1",
        "quote_digest": DIGEST, "quote_version": 4,
        "confirmation_message_id": 501,
    }
    raw = json.dumps(req, sort_keys=True).encode()
    signature = sign_action(raw, STAMP, UUID_DELIVERY, SECRET)
    assert verify_checkout_read(
        raw, STAMP, UUID_DELIVERY, signature, SECRET,
        now=1800000000,
    ).intent_id == "intent-1234"
    for changed in (
        {"action": "salla.create_pending_order"},
        {"checkout_url": "https://evil.invalid/checkout"},
        {"salla_order_id": 123},
    ):
        invalid = json.dumps({**req, **changed}).encode()
        with pytest.raises(SignatureDenied):
            verify_checkout_read(
                invalid, STAMP, UUID_DELIVERY,
                sign_action(invalid, STAMP, UUID_DELIVERY, SECRET),
                SECRET, now=1800000000,
            )


def post_create_db():
    db = DB()
    db.salla_ai_order_intents.rows[0]["state"] = "created_amount_unverified"
    q = db.salla_ai_order_quotes.rows[0]
    q.update({
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
    })
    return db


def test_salla_post_create_total_from_mezan_is_authoritative():
    db = post_create_db()
    o = mezan_order()
    o.totals.total = 298.75
    row = check(db=db, result=o)
    assert row["state"] == "ready"
    assert row["amount_verified"] is True
    assert row["total_sar"] == "298.75"
    assert row["pricing_mode"] == "salla_post_create"
    assert row["checkout_url"] == "https://amasi-sa.com/checkout/order-302076044"


def test_salla_post_create_total_missing_is_never_sent():
    db = post_create_db()
    o = mezan_order()
    o.totals.total = 0
    row = check(db=db, result=o)
    assert row["state"] == "salla_final_total_invalid"
    assert row["checkout_url"] is None


def test_salla_post_create_bad_scope_or_price_contract_blocks_checkout():
    db = post_create_db()
    q = db.salla_ai_order_quotes.rows[0]
    q["carrier_eligibility_verified"] = False
    assert check(db=db)["checkout_url"] is None
    assert check(db=db)["state"] == "original_quote_not_verified"


def test_salla_post_create_only_pending_payment_status_can_reveal_link():
    db = post_create_db()
    o = mezan_order()
    o.status = "completed"
    o.status_native = "تم التنفيذ"
    assert check(db=db, result=o)["checkout_url"] is None


def test_salla_post_create_untrusted_non_sar_total_denied():
    db = post_create_db()
    o = mezan_order()
    o.totals.currency = "KWD"
    assert check(db=db, result=o)["state"] == "payment_or_currency_not_verified"
