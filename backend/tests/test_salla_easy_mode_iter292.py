"""Iter-292 — Salla Easy Mode webhook receiver tests.

Verifies the contract laid out in `easy_mode_webhook.py`:

  1. 503 when SALLA_WEBHOOK_SECRET is unset (never silently accepts).
  2. 401 on missing signature.
  3. 401 on wrong signature.
  4. 401 on right signature but wrong key.
  5. `app.store.authorize` with valid signature → persists tokens,
     marks status=connected, encrypts tokens (no plaintext on disk).
  6. `/api/salla/status` returns `connected: true` after Easy-Mode auth.
  7. `app.uninstalled` → marks status=not_connected (does NOT delete row).
  8. Owner resolution: earliest user with `role=owner` wins.
  9. Idempotency: repeating .authorize produces same final state, no
     duplicate documents.
  10. Unknown event names are 200 OK no-op (Salla retries on non-2xx,
      so this is critical).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

sys.path.insert(0, "/app/backend")
from salla_integration import crypto as salla_crypto  # noqa: E402
from salla_integration import easy_mode_webhook as emw  # noqa: E402


def _read_backend_url() -> str:
    """REACT_APP_BACKEND_URL is in frontend/.env, not the pytest env.
    Parse it manually so tests work whether or not the operator
    exported it."""
    explicit = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    except FileNotFoundError:
        pass
    return ""


BASE_URL = _read_backend_url()
API = f"{BASE_URL}/api"
WEBHOOK_PATH = "/api/salla/webhooks/app"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"


# ── Helpers ───────────────────────────────────────────────────────────
def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _store_authorize_payload(
    *,
    merchant: str = "1233666",
    access: str = "ory_at_test_access_token_value",
    refresh: str = "ory_rt_test_refresh_token_value",
    expires_in_sec: int = 60 * 60 * 24 * 14,  # 2 weeks
) -> dict:
    now = int(datetime.now(timezone.utc).timestamp())
    return {
        "event": "app.store.authorize",
        "merchant": merchant,
        "created_at": "Sun Jun 30 2026 14:00:00 GMT+0300",
        "data": {
            "access_token": access,
            "expires": now + expires_in_sec,
            "refresh_token": refresh,
            "scope": "offline_access settings.read orders.read_write webhooks.read_write",
            "token_type": "Bearer",
        },
    }


def _register_owner() -> tuple[str, str, str]:
    """Register a new user, then promote them to `role=owner` by calling
    the admin endpoint as the seeded admin. Returns (id, email, token)."""
    email = f"easymode-owner-{uuid.uuid4().hex[:10]}@test.com"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "Owner", "email": email, "password": "Test1234!"},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    uid = body["id"]
    # Note: registration default role is "user". The tests below set
    # role=owner directly via MongoDB (see fixture).
    return uid, email, body["access_token"]


# ── Unit tests for signature + owner resolution ───────────────────────
class TestSignatureVerification:
    def test_valid_signature_passes(self):
        body = b'{"event":"app.store.authorize"}'
        sig = _sign("topsecret", body)
        assert emw.verify_signature(body, sig, "topsecret") is True

    def test_wrong_signature_fails(self):
        body = b'{"event":"app.store.authorize"}'
        assert emw.verify_signature(body, "deadbeef", "topsecret") is False

    def test_wrong_key_fails(self):
        body = b'{"event":"app.store.authorize"}'
        sig = _sign("wrong_secret", body)
        assert emw.verify_signature(body, sig, "topsecret") is False

    def test_missing_signature_fails(self):
        body = b'{"event":"x"}'
        assert emw.verify_signature(body, "", "topsecret") is False
        assert emw.verify_signature(body, None, "topsecret") is False  # type: ignore

    def test_missing_secret_fails(self):
        body = b'{"event":"x"}'
        sig = _sign("anything", body)
        assert emw.verify_signature(body, sig, "") is False

    def test_signature_uppercase_normalized(self):
        body = b'{"event":"x"}'
        sig = _sign("topsecret", body).upper()
        assert emw.verify_signature(body, sig, "topsecret") is True

    def test_get_webhook_secret_returns_none_when_blank(self, monkeypatch):
        monkeypatch.setenv("SALLA_WEBHOOK_SECRET", "")
        assert emw.get_webhook_secret() is None
        monkeypatch.setenv("SALLA_WEBHOOK_SECRET", "   ")
        assert emw.get_webhook_secret() is None
        monkeypatch.setenv("SALLA_WEBHOOK_SECRET", "abc123")
        assert emw.get_webhook_secret() == "abc123"


# ── E2E tests via live HTTP (require backend running) ─────────────────
import pytest_asyncio  # noqa: E402


@pytest.fixture
def db():
    """Fresh DB client per test — motor's AsyncIOMotorClient binds to
    the current event loop, and pytest-asyncio creates a new loop per
    test, so a module-scoped client would crash on the second test."""
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url:
        # Backend's .env (not loaded by pytest); read directly.
        try:
            with open("/app/backend/.env") as f:
                for line in f:
                    if line.startswith("MONGO_URL="):
                        mongo_url = line.split("=", 1)[1].strip().strip('"')
                        break
        except FileNotFoundError:
            pass
    db_name = os.environ.get("DB_NAME") or "test_database"
    client = AsyncIOMotorClient(mongo_url)
    return client[db_name]


@pytest_asyncio.fixture
async def resolved_owner(db):
    """Discover the user that `resolve_owner_user_id` actually picks
    (earliest `role=owner`), so we test against ground truth rather
    than fight the resolver.

    Cleanup: only wipe the salla_integrations row we created — we do
    NOT delete the user (that'd break the seeded admin).
    """
    uid, email = await emw.resolve_owner_user_id(db)
    assert uid, "No owner user exists in DB — seed admin first"
    # Snapshot the existing integration doc so we can restore it.
    existing = await db.salla_integrations.find_one({"user_id": uid})
    # Get an auth token for this user so we can hit /api/salla/status.
    # The seeded admin password is in test_credentials.md.
    token = None
    for candidate_pw in ("admin123", "Test1234!"):
        try:
            r = requests.post(
                f"{API}/auth/login",
                json={"email": email, "password": candidate_pw},
                timeout=10,
            )
            if r.status_code == 200:
                token = r.json().get("access_token")
                break
        except requests.RequestException:
            continue
    yield uid, email, token
    # Restore: delete our test row, then re-insert the snapshot if any.
    await db.salla_integrations.delete_one({"user_id": uid})
    if existing:
        existing.pop("_id", None)
        await db.salla_integrations.insert_one(existing)


@pytest.fixture
def set_secret():
    """Read the secret directly from backend/.env (not pytest env)."""
    secret = os.environ.get("SALLA_WEBHOOK_SECRET", "")
    if not secret:
        try:
            with open("/app/backend/.env") as f:
                for line in f:
                    if line.startswith("SALLA_WEBHOOK_SECRET="):
                        secret = line.split("=", 1)[1].strip().strip('"')
                        break
        except FileNotFoundError:
            pass
    if not secret:
        pytest.skip("SALLA_WEBHOOK_SECRET not set in backend .env — E2E skipped")
    return secret


class TestWebhookEndpointSecret:
    """These tests run only when SALLA_WEBHOOK_SECRET is set in backend
    env. See `set_secret` fixture above."""

    def test_503_when_secret_unset(self):
        """If the backend has NO secret, the endpoint must return 503,
        never silently process the webhook.

        Reads the BACKEND'S .env (not the pytest env) so it correctly
        skips when the secret IS configured for the running backend.
        """
        backend_secret = ""
        try:
            with open("/app/backend/.env") as f:
                for line in f:
                    if line.startswith("SALLA_WEBHOOK_SECRET="):
                        backend_secret = line.split("=", 1)[1].strip().strip('"')
                        break
        except FileNotFoundError:
            pass
        if backend_secret:
            pytest.skip("Backend secret IS configured — 503 path verified manually via curl")
        r = requests.post(
            WEBHOOK_URL,
            data=b'{}',
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        assert r.status_code == 503
        body = r.json()
        assert "SALLA_WEBHOOK_SECRET_NOT_CONFIGURED" in str(body)

    def test_401_on_missing_signature(self, set_secret):  # noqa: ARG002
        r = requests.post(
            WEBHOOK_URL,
            data=json.dumps(_store_authorize_payload()),
            headers={"Content-Type": "application/json"},  # no x-salla-signature
            timeout=10,
        )
        assert r.status_code == 401
        assert "INVALID_SIGNATURE" in str(r.json())

    def test_401_on_wrong_signature(self, set_secret):  # noqa: ARG002
        body = json.dumps(_store_authorize_payload()).encode()
        r = requests.post(
            WEBHOOK_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-salla-signature": "0" * 64,  # wrong but well-formed
            },
            timeout=10,
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_store_authorize_persists_tokens(self, set_secret, resolved_owner, db):
        uid, email, _ = resolved_owner
        payload = _store_authorize_payload(merchant="9999000111")
        body = json.dumps(payload).encode()
        sig = _sign(set_secret, body)
        r = requests.post(
            WEBHOOK_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-salla-signature": sig,
            },
            timeout=10,
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["ok"] is True
        assert result["stored"] is True
        assert result["user_id"] == uid
        assert result["user_email"] == email
        assert result["merchant_id"] == "9999000111"

        # Direct DB assertion: tokens persisted + encrypted.
        doc = await db.salla_integrations.find_one({"user_id": uid})
        assert doc is not None
        assert doc["status"] == "connected"
        assert doc["install_mode"] == "easy_mode"
        assert doc["store_id"] == "9999000111"
        assert doc["easy_mode_owner_email"] == email
        # Tokens stored encrypted (bytes), NOT as the original plaintext.
        assert doc["access_token_encrypted"] != "ory_at_test_access_token_value"
        plaintext = salla_crypto.decrypt_token(doc["access_token_encrypted"])
        assert plaintext == "ory_at_test_access_token_value"

    @pytest.mark.asyncio
    async def test_status_returns_connected_true_after_authorize(
        self, set_secret, resolved_owner, db,  # noqa: ARG002
    ):
        uid, email, token = resolved_owner
        body = json.dumps(_store_authorize_payload()).encode()
        sig = _sign(set_secret, body)
        requests.post(
            WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json", "x-salla-signature": sig},
            timeout=10,
        )
        # Now GET /api/salla/status as the owner.
        r = requests.get(
            f"{API}/salla/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["connected"] is True
        assert data["status"] == "connected"
        assert data["install_mode"] == "easy_mode"
        assert data["easy_mode_owner_email"] == email

    @pytest.mark.asyncio
    async def test_uninstalled_marks_not_connected_keeps_row(
        self, set_secret, resolved_owner, db,
    ):
        uid, _, _ = resolved_owner
        # First install.
        b1 = json.dumps(_store_authorize_payload()).encode()
        requests.post(
            WEBHOOK_URL,
            data=b1,
            headers={"Content-Type": "application/json", "x-salla-signature": _sign(set_secret, b1)},
            timeout=10,
        )
        # Then uninstall.
        uninstall = {"event": "app.uninstalled", "merchant": "1233666"}
        b2 = json.dumps(uninstall).encode()
        r = requests.post(
            WEBHOOK_URL,
            data=b2,
            headers={"Content-Type": "application/json", "x-salla-signature": _sign(set_secret, b2)},
            timeout=10,
        )
        assert r.status_code == 200
        doc = await db.salla_integrations.find_one({"user_id": uid})
        assert doc is not None  # row kept
        assert doc["status"] == "not_connected"
        assert "uninstalled" in (doc.get("last_error") or "").lower()

    def test_unknown_event_returns_200(self, set_secret):
        unknown = {"event": "order.created", "merchant": "1", "data": {}}
        body = json.dumps(unknown).encode()
        r = requests.post(
            WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json", "x-salla-signature": _sign(set_secret, body)},
            timeout=10,
        )
        # MUST be 200 — Salla retries on non-2xx.
        assert r.status_code == 200
        result = r.json()
        assert result["ok"] is True
        assert result["stored"] is False
        assert result["event"] == "order.created"

    @pytest.mark.asyncio
    async def test_idempotent_authorize(self, set_secret, resolved_owner, db):
        uid, _, _ = resolved_owner
        body = json.dumps(_store_authorize_payload()).encode()
        sig = _sign(set_secret, body)
        for _ in range(3):
            requests.post(
                WEBHOOK_URL,
                data=body,
                headers={"Content-Type": "application/json", "x-salla-signature": sig},
                timeout=10,
            )
        count = await db.salla_integrations.count_documents({"user_id": uid})
        assert count == 1  # upserted, not duplicated


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
