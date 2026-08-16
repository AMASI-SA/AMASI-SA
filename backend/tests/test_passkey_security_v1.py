"""Focused contracts for Owner trusted-device WebAuthn/passkeys."""
from __future__ import annotations

import asyncio
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


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, limit):
        return self.rows[:limit]


class _FakeCredentials:
    def __init__(self, rows):
        self.rows = rows
        self.last_query = None

    def find(self, query, projection):
        self.last_query = query
        matches = [
            row for row in self.rows
            if row.get("user_id") == query.get("user_id")
            and (
                "device_hash" not in query
                or row.get("device_hash") == query.get("device_hash")
            )
            and "revoked_at" not in row
        ]
        return _FakeCursor(matches)


class _FakeDb:
    def __init__(self, rows):
        self.auth_passkey_credentials = _FakeCredentials(rows)
        self.auth_passkey_challenges = object()
        self.auth_security_events = object()


def test_existing_user_passkey_can_be_rebound_after_browser_id_rotation():
    """Same-browser user switching must not attempt duplicate registration."""
    existing = {
        "credential_id_b64": "existing-owner-passkey",
        "user_id": "owner-1",
        "device_hash": "old-browser-cookie",
    }
    store = module.PasskeyStore(_FakeDb([existing]))

    current_device = asyncio.run(
        store.reusable_for_device("owner-1", "new-browser-cookie")
    )
    same_user = asyncio.run(store.reusable_for_user("owner-1"))

    assert current_device == []
    assert same_user == [existing]
    assert same_user[0]["credential_id_b64"] == "existing-owner-passkey"
    assert module._trust_ceremony(current_device, same_user) == "rebind"


def test_trust_ceremony_never_registers_when_user_has_existing_passkey():
    credential = {"credential_id_b64": "owner-passkey"}

    assert module._trust_ceremony([credential], [credential]) == "renew"
    assert module._trust_ceremony([], [credential]) == "rebind"
    assert module._trust_ceremony([], []) == "register"


def test_frontend_rebind_uses_authentication_not_duplicate_registration():
    source = (
        module.__file__.replace("backend/passkey_security.py", "frontend/src/pages/Login.jsx")
    )
    with open(source, encoding="utf-8") as handle:
        login_source = handle.read()

    assert 'ceremony === "renew" || ceremony === "rebind"' in login_source
