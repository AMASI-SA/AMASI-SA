"""Synthetic one-way WhatsApp buyer proof: no network, secrets or customer data."""
import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.order_engine.whatsapp_stage_attested_route import (
    make_whatsapp_attested_stage_router, derive_buyer_proof,
    VERSION, MAX_PROOF_AGE,
)
from backend.order_engine.whatsapp_stage_service_auth import ATTESTED_PATH

NOW = 1800000000
KID = "synthetic-key"
KEY = b"K" * 32
NONCE = "abcdefghijklmnopqrstuvwx"
ORDER = "301153281"
MOBILE = "966550000001"


def payload(*, phone=MOBILE, issued=NOW - 2, order=ORDER,
            account=1, inbox=2, conversation=10, message=20):
    return {
        "proof_version": VERSION,
        "order_number": order, "account_id": account, "inbox_id": inbox,
        "conversation_id": conversation, "message_id": message,
        "attested_at": issued,
        "buyer_proof": derive_buyer_proof(
            KEY, mobile=phone, account_id=1, inbox_id=2,
            conversation_id=conversation, message_id=message,
            attested_at=issued,
        ),
    }


def encode(data):
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode()


def headers(raw, *, nonce=NONCE, key=KEY, path=ATTESTED_PATH):
    ts = str(NOW)
    material = "\n".join((
        "POST", path, ts, nonce, KID, hashlib.sha256(raw).hexdigest()
    )).encode()
    return {
        "x-amasi-timestamp": ts, "x-amasi-nonce": nonce,
        "x-amasi-key-id": KID,
        "x-amasi-signature": hmac.new(key, material, hashlib.sha256).hexdigest(),
        "content-type": "application/json",
    }


def record(phone="0550000001", state="قيد التنفيذ"):
    return SimpleNamespace(
        order_number=ORDER,
        customer=SimpleNamespace(mobile=phone),
        status_native=state,
        engine_updated_at=datetime.fromtimestamp(NOW-100, timezone.utc),
    )


def client(fresh=True, rate=True):
    seen = set()

    def nonce_claim(digest, ttl):
        if digest in seen:
            return False
        seen.add(digest)
        return True

    async def freshness(_):
        return fresh

    app = FastAPI()
    app.include_router(make_whatsapp_attested_stage_router(
        repository=object(), fixed_owner_id="fixed_owner",
        active_keys={KID: KEY}, claim_nonce=nonce_claim,
        rate_limit=lambda _: rate, source_freshness=freshness,
        now_fn=lambda: NOW,
    ), prefix="/api")
    return TestClient(app)


class AttestedRouteTest(unittest.TestCase):
    def test_valid_buyer_read_and_replay_denied(self):
        web = client()
        raw = encode(payload())
        self.assertNotIn(MOBILE.encode(), raw)
        self.assertNotIn(b"mobile", raw)
        self.assertNotIn(b"phone", raw)
        with patch("backend.order_engine.whatsapp_stage_attested_route.get_order",
                   return_value=record()) as source:
            res = web.post(ATTESTED_PATH, content=raw, headers=headers(raw))
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["stage"], "preparing")
            self.assertNotIn("customer", res.text)
            self.assertNotIn("mobile", res.text)
            self.assertEqual(source.await_args.kwargs["user_id"], "fixed_owner")
            self.assertEqual(
                web.post(ATTESTED_PATH, content=raw, headers=headers(raw)).status_code,
                401,
            )

    def test_wrong_buyer_and_unverified_order_uniformly_denied(self):
        for record_phone in ("0550000002", "", "not-a-phone"):
            with self.subTest(record_phone=record_phone):
                web = client()
                raw = encode(payload())
                with patch("backend.order_engine.whatsapp_stage_attested_route.get_order",
                           return_value=record(phone=record_phone)):
                    res = web.post(ATTESTED_PATH, content=raw, headers=headers(raw))
                self.assertEqual(res.status_code, 404)
                self.assertEqual(res.json()["detail"], "verification_required")

    def test_stale_upstream_and_other_customer_orders_denied(self):
        for fresh in (False, True):
            web = client(fresh=fresh)
            raw = encode(payload(order=ORDER))
            with patch("backend.order_engine.whatsapp_stage_attested_route.get_order",
                       return_value=record(state="غير معروف" if fresh else "قيد التنفيذ")):
                res = web.post(ATTESTED_PATH, content=raw, headers=headers(raw))
                self.assertEqual(res.status_code, 404)

    def test_expired_future_wrong_inbox_and_untrusted_data(self):
        for edit in (
            {"attested_at": NOW-MAX_PROOF_AGE-1},
            {"attested_at": NOW+1},
            {"account_id": 2}, {"inbox_id": 3},
            {"conversation_id": 11}, {"message_id": 21},
            {"buyer_proof": "0"*64},
            {"proof_version": "unsupported"},
            {"unexpected_customer_phone": MOBILE},
        ):
            with self.subTest(edit=edit):
                web = client()
                document = payload()
                document.update(edit)
                raw = encode(document)
                with patch("backend.order_engine.whatsapp_stage_attested_route.get_order",
                           return_value=record()):
                    result = web.post(ATTESTED_PATH, content=raw, headers=headers(raw))
                self.assertEqual(result.status_code, 404)

    def test_signature_tamper_scope_replay_and_limit(self):
        raw = encode(payload())
        for bad in (
            dict(headers(raw), **{"x-amasi-signature": "0"*64}),
            headers(raw, key=b"Z"*32),
            headers(raw, path="/api/internal/whatsapp/catalog/read"),
        ):
            with self.subTest(bad=bad):
                web = client()
                with patch("backend.order_engine.whatsapp_stage_attested_route.get_order") as reader:
                    self.assertEqual(
                        web.post(ATTESTED_PATH, content=raw, headers=bad).status_code, 401
                    )
                    reader.assert_not_called()
        with patch("backend.order_engine.whatsapp_stage_attested_route.get_order") as reader:
            web = client(rate=False)
            self.assertEqual(
                web.post(ATTESTED_PATH, content=raw, headers=headers(raw)).status_code, 429
            )
            reader.assert_not_called()

    def test_duplicate_keys_and_non_json_denied(self):
        for raw in (
            encode(payload())[:-1] + b',"message_id":999}',
            b'{"invalid":NaN}',
            b'\xff',
        ):
            web = client()
            with patch("backend.order_engine.whatsapp_stage_attested_route.get_order") as reader:
                self.assertEqual(
                    web.post(ATTESTED_PATH, content=raw, headers=headers(raw)).status_code, 404
                )
                reader.assert_not_called()

    def test_proof_context_is_bound_and_not_raw_phone(self):
        reference = derive_buyer_proof(
            KEY, mobile=MOBILE, account_id=1, inbox_id=2, conversation_id=10,
            message_id=20, attested_at=NOW-2
        )
        for field in (
            {"mobile": "966550000002"}, {"message_id": 21},
            {"conversation_id": 11}, {"attested_at": NOW-3}
        ):
            args=dict(mobile=MOBILE, account_id=1, inbox_id=2,
                      conversation_id=10, message_id=20, attested_at=NOW-2)
            args.update(field)
            self.assertNotEqual(reference, derive_buyer_proof(KEY, **args))


if __name__ == "__main__":
    unittest.main()
