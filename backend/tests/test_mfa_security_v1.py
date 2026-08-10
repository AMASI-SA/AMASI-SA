"""Focused tests for privileged Owner/Admin MFA rollout."""
from __future__ import annotations

import os
from types import SimpleNamespace

import jwt
import pytest

os.environ.setdefault("JWT_SECRET", "test-mfa-secret-for-unit-tests")

from auth import create_access_token, get_current_user_from_db  # noqa: E402
from mfa_security import (  # noqa: E402
    _decode_challenge_token,
    _normalize_recovery_code,
    _challenge_token,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hotp,
    match_totp_counter,
    provisioning_uri,
    recovery_code_digest,
)


class FakeUsers:
    def __init__(self, user):
        self.user = user

    async def find_one(self, query):
        return dict(self.user) if query.get("id") == self.user.get("id") else None


class FakeDb:
    def __init__(self, user):
        self.users = FakeUsers(user)


class FakeRequest:
    def __init__(self, token):
        self.cookies = {"access_token": token}
        self.headers = {}


def test_hotp_matches_rfc4226_counter_zero_vector():
    # RFC 4226 Appendix D secret: ASCII "12345678901234567890".
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert hotp(secret, 0) == "755224"


def test_totp_matching_accepts_current_and_adjacent_window_only():
    secret = generate_totp_secret()
    timestamp = 1_800_000_000.0
    counter = int(timestamp // 30)
    current = hotp(secret, counter)
    previous = hotp(secret, counter - 1)
    too_old = hotp(secret, counter - 2)

    assert match_totp_counter(secret, current, timestamp=timestamp) == counter
    assert match_totp_counter(secret, previous, timestamp=timestamp) == counter - 1
    assert match_totp_counter(secret, too_old, timestamp=timestamp) is None
    assert match_totp_counter(secret, "abcdef", timestamp=timestamp) is None


def test_totp_secret_is_encrypted_at_rest_and_round_trips():
    secret = generate_totp_secret()
    encrypted = encrypt_totp_secret(secret)
    assert encrypted != secret
    assert secret not in encrypted
    assert decrypt_totp_secret(encrypted) == secret
    assert decrypt_totp_secret("not-a-valid-fernet-token") is None


def test_provisioning_uri_is_local_totp_standard_and_contains_no_password():
    secret = "JBSWY3DPEHPK3PXP"
    uri = provisioning_uri("owner@example.com", secret)
    assert uri.startswith("otpauth://totp/")
    assert "MEZAN" in uri
    assert "owner%40example.com" in uri
    assert f"secret={secret}" in uri
    assert "password" not in uri.lower()


def test_recovery_codes_are_one_time_hashable_without_storing_plaintext():
    codes = generate_recovery_codes(8)
    assert len(codes) == 8
    assert len(set(codes)) == 8
    first = codes[0]
    digest = recovery_code_digest(first)
    assert first not in digest
    assert recovery_code_digest(first.lower().replace("-", "")) == digest
    assert _normalize_recovery_code(first) == first.replace("-", "")


def test_mfa_challenge_token_is_short_lived_typed_and_scoped():
    token = _challenge_token(user_id="owner-1", purpose="login", jti="abc123")
    payload = _decode_challenge_token(token)
    assert payload["sub"] == "owner-1"
    assert payload["purpose"] == "login"
    assert payload["jti"] == "abc123"
    assert payload["type"] == "mfa_challenge"


@pytest.mark.asyncio
async def test_owner_password_only_access_token_is_rejected():
    user = {
        "id": "owner-1",
        "email": "owner@example.com",
        "role": "owner",
        "password_hash": "redacted",
    }
    token = create_access_token(user["id"], user["email"], mfa_verified=False)
    with pytest.raises(Exception) as exc:
        await get_current_user_from_db(FakeRequest(token), FakeDb(user))
    assert getattr(exc.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_owner_mfa_access_token_is_accepted_and_secret_fields_are_not_returned():
    user = {
        "id": "owner-1",
        "email": "owner@example.com",
        "role": "owner",
        "password_hash": "redacted",
    }
    token = create_access_token(user["id"], user["email"], mfa_verified=True)
    result = await get_current_user_from_db(FakeRequest(token), FakeDb(user))
    assert result["id"] == "owner-1"
    assert "password_hash" not in result


def test_non_privileged_token_does_not_require_mfa_claim_shape():
    token = create_access_token("viewer-1", "viewer@example.com")
    payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])
    assert payload["mfa"] is False
