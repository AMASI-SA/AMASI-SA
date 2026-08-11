"""Focused contracts for Owner trusted-device WebAuthn/passkeys."""
from __future__ import annotations

import hashlib
import hmac

import passkey_security as module
from webauthn import base64url_to_bytes


def _signed_device_cookie(secret: str, token: str) -> tuple[str, str]:
    signature = hmac.new(
        secret.encode("utf-8"),
        f"device:{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{token}.{signature}", signature


def test_trusted_device_binding_matches_login_security_digest(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret")
    signed, expected_hash = _signed_device_cookie("unit-test-secret", "browser-token-1")
    scope = {
        "headers": [(b"cookie", f"mezan_device_id={signed}".encode("latin-1"))],
    }

    assert module._trusted_device_hash(scope) == expected_hash


def test_trusted_device_binding_rejects_tampered_cookie(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret")
    signed, _ = _signed_device_cookie("unit-test-secret", "browser-token-1")
    token, signature = signed.rsplit(".", 1)
    tampered = f"{token}.{('0' if signature[0] != '0' else '1')}{signature[1:]}"
    scope = {
        "headers": [(b"cookie", f"mezan_device_id={tampered}".encode("latin-1"))],
    }

    assert module._trusted_device_hash(scope) is None


def test_webauthn_origin_and_rp_are_exact(monkeypatch):
    monkeypatch.setenv("WEBAUTHN_ORIGIN", "https://salla-analytics.emergent.host/")
    monkeypatch.delenv("WEBAUTHN_RP_ID", raising=False)

    assert module._configured_origin() == "https://salla-analytics.emergent.host"
    assert module._rp_id() == "salla-analytics.emergent.host"


def test_webauthn_rp_can_be_explicit(monkeypatch):
    monkeypatch.setenv("WEBAUTHN_ORIGIN", "https://login.example.com")
    monkeypatch.setenv("WEBAUTHN_RP_ID", "example.com")

    assert module._rp_id() == "example.com"


def test_trust_window_defaults_to_30_days(monkeypatch):
    monkeypatch.delenv("AUTH_TRUSTED_DEVICE_DAYS", raising=False)
    assert module._trust_days() == 30


def test_base64url_round_trip():
    raw = b"mezan-passkey-challenge\x00\xff"
    encoded = module._b64u(raw)

    assert "=" not in encoded
    assert base64url_to_bytes(encoded) == raw
