"""Synthetic auth tests: no real order/customer information or network."""
import hashlib
import hmac
import unittest

from backend.order_engine.whatsapp_stage_service_auth import (
    StageServiceUnauthorized,
    verify_stage_service_request,
)

KEY = b"K" * 32
BODY = b'{"order_ref":"synthetic"}'
TS = "1800000000"
NONCE = "abcdefghijklmnopqrstuvwx"
PATH = "/internal/whatsapp/order-stage/read"
KID = "whatsapp-v1"


def signed(**overrides):
    payload = {
        "method": "POST", "path": PATH, "body": BODY,
        "timestamp": TS, "nonce": NONCE, "key_id": KID,
        "active_keys": {KID: KEY}, "now": 1800000000,
    }
    message = "\n".join((
        "POST", PATH, TS, NONCE, KID, hashlib.sha256(BODY).hexdigest()
    )).encode("ascii")
    payload["signature"] = hmac.new(KEY, message, hashlib.sha256).hexdigest()
    payload.update(overrides)
    return payload


class AuthTests(unittest.TestCase):
    def test_valid_signature_and_atomic_replay(self):
        seen = set()
        def claim(value, expiry):
            self.assertEqual(expiry, 601)
            if value in seen:
                return False
            seen.add(value)
            return True
        args = signed(claim_nonce=claim)
        verify_stage_service_request(**args)
        with self.assertRaises(StageServiceUnauthorized):
            verify_stage_service_request(**args)

    def test_bad_signature_payload_scope_and_clock(self):
        for change in (
            {"body": b"tampered"}, {"method": "GET"},
            {"path": "/orders-v2/1/customer-history"},
            {"now": 1800000301}, {"now": 1799999699},
            {"key_id": "unknown"}, {"active_keys": {}},
            {"nonce": "too-short"}, {"signature": "0" * 64},
            {"body": b"x" * 2049},
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(StageServiceUnauthorized, "^service_unauthorized$"):
                    verify_stage_service_request(**signed(claim_nonce=lambda *_: True, **change))

    def test_missing_replay_store_denied(self):
        with self.assertRaises(StageServiceUnauthorized):
            verify_stage_service_request(**signed(claim_nonce=lambda *_: False))
        def broken(*_):
            raise RuntimeError("cache unavailable")
        with self.assertRaises(StageServiceUnauthorized):
            verify_stage_service_request(**signed(claim_nonce=broken))


if __name__ == "__main__":
    unittest.main()
