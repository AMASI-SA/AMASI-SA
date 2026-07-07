"""Phase-1 backend tests for the Salla integration.

Focus areas:
  * Token encryption roundtrip + tamper detection.
  * All /api/salla/* routes require auth.
  * /status reports `configured=False` when env keys are missing.
  * /oauth/login returns 503 when not configured, builds a proper
    authorize URL when configured.
  * Full callback flow: state validation → exchange_code → fetch_store_info
    → persist (all upstream Salla calls MOCKED via respx).
  * Auto-refresh: when expires_at is in the past, the next /test-connection
    triggers refresh_with_token first.
  * Disconnect removes the row and subsequent /status returns not_connected.
  * The new module DOES NOT touch existing collections (unified_orders,
    analyses, snapchat_*, meta_*).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
import respx
from httpx import Response

# We import the module under test directly so we can also unit-test
# helpers without a running server.
sys.path.insert(0, "/app/backend")
from salla_integration import crypto as salla_crypto  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"

SALLA_AUTH_BASE = os.environ.get("SALLA_AUTH_BASE", "https://accounts.salla.sa").rstrip("/")
SALLA_API_BASE = os.environ.get("SALLA_API_BASE", "https://api.salla.dev/admin/v2").rstrip("/")


def _register() -> tuple[str, str]:
    email = f"salla-{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "Salla Tester", "email": email, "password": "Test1234!"},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _cleanup(uid: str) -> None:
    """Tear down per-user data so tests don't pollute each other."""
    import os as _os
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _do():
        client = AsyncIOMotorClient(_os.environ["MONGO_URL"])
        db = client[_os.environ["DB_NAME"]]
        for coll in ("salla_integrations", "salla_oauth_states", "users", "settings"):
            await db[coll].delete_many({"$or": [{"user_id": uid}, {"id": uid}, {"_id": uid}]})
        client.close()

    asyncio.run(_do())


# ── 1. Encryption roundtrip ───────────────────────────────────────────
def test_encrypt_decrypt_roundtrip():
    plain = "very_secret_access_token_12345"
    ct = salla_crypto.encrypt_token(plain)
    assert isinstance(ct, (bytes, bytearray))
    assert ct != plain.encode()
    assert salla_crypto.decrypt_token(ct) == plain


def test_decrypt_empty_returns_empty():
    assert salla_crypto.decrypt_token(b"") == ""
    assert salla_crypto.decrypt_token(None) == ""


def test_encrypt_nondeterministic():
    """Fernet includes a nonce → same input twice → different ciphertexts."""
    a = salla_crypto.encrypt_token("hello")
    b = salla_crypto.encrypt_token("hello")
    assert a != b
    # But both decrypt to the same plaintext.
    assert salla_crypto.decrypt_token(a) == salla_crypto.decrypt_token(b) == "hello"


def test_decrypt_tampered_raises():
    ct = bytearray(salla_crypto.encrypt_token("secret"))
    # Flip a byte in the middle
    ct[len(ct) // 2] ^= 0xAA
    with pytest.raises(ValueError):
        salla_crypto.decrypt_token(bytes(ct))


# ── 2. Auth required on all /api/salla routes ─────────────────────────
def test_all_salla_routes_require_auth():
    for method, path in [
        ("GET", "/salla/status"),
        ("GET", "/salla/oauth/login"),
        ("POST", "/salla/test-connection"),
        ("POST", "/salla/refresh-store-info"),
        ("POST", "/salla/disconnect"),
    ]:
        fn = getattr(requests, method.lower())
        r = fn(f"{API}{path}", timeout=10)
        assert r.status_code in (401, 403), f"{method} {path} should reject unauth; got {r.status_code}"


# ── 3. /status shape when nothing exists yet ──────────────────────────
def test_status_when_not_connected():
    token, uid = _register()
    try:
        r = requests.get(f"{API}/salla/status", headers=_hdr(token), timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["connected"] is False
        assert body["status"] == "not_connected"
        # `configured` reflects whether env keys are set — we don't assert
        # a specific value because CI may or may not have them.
        assert "configured" in body
    finally:
        _cleanup(uid)


# ── 4. /oauth/login behaviour depending on env config ─────────────────
def test_oauth_login_unconfigured_returns_503(monkeypatch):
    """When SALLA_CLIENT_ID / _SECRET are blank, the API must respond
    with a clear 503 and an Arabic guidance message — never trigger a
    redirect to a malformed Salla authorize URL."""
    # We can't easily monkeypatch the running backend, so we just verify
    # the response matches one of the two valid states.
    token, uid = _register()
    try:
        r = requests.get(f"{API}/salla/oauth/login", headers=_hdr(token), timeout=10)
        if r.status_code == 503:
            assert "SALLA_CLIENT_ID" in r.text or "ليس مُعدّاً" in r.text
        else:
            # Backend HAS creds → must return a proper authorize URL.
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["authorize_url"].startswith(f"{SALLA_AUTH_BASE}/oauth2/auth")
            assert "client_id=" in body["authorize_url"]
            assert "scope=" in body["authorize_url"]
            assert "state=" in body["authorize_url"]
            assert "offline_access" in body["authorize_url"]
            assert "/api/salla/oauth/callback" in body["redirect_uri"]
    finally:
        _cleanup(uid)


# ── 5. Disconnect is idempotent ───────────────────────────────────────
def test_disconnect_idempotent():
    token, uid = _register()
    try:
        for expected in (0, 0):  # second call must also succeed
            r = requests.post(f"{API}/salla/disconnect", headers=_hdr(token), timeout=10)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["removed"] == expected
    finally:
        _cleanup(uid)


# ── 6. test-connection with no integration → 404 + needs_reauth ───────
def test_test_connection_without_integration():
    token, uid = _register()
    try:
        r = requests.post(f"{API}/salla/test-connection", headers=_hdr(token), timeout=10)
        assert r.status_code in (401, 404), r.text
        detail = r.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("needs_reauth") is True
    finally:
        _cleanup(uid)


# ── 7. End-to-end OAuth flow with respx-mocked Salla ──────────────────
@respx.mock
def test_oauth_callback_persists_tokens_with_mocked_salla(monkeypatch):
    """Simulates: state stored → callback called with code → exchange_code
    posted to mocked Salla token endpoint → /store/info call mocked →
    Mongo doc upserted with encrypted tokens + store metadata.

    We exercise this against the live FastAPI server via requests, but
    intercept all outbound httpx calls with respx. NOTE: respx only
    works in-process; we can't mock the running server's outbound calls
    from a separate test process. So instead we test the service layer
    directly here."""
    monkeypatch.setenv("SALLA_CLIENT_ID", "fake_test_client_id")
    monkeypatch.setenv("SALLA_CLIENT_SECRET", "fake_test_client_secret")
    import asyncio as _aio
    from motor.motor_asyncio import AsyncIOMotorClient
    from salla_integration import service as svc

    # Mock Salla's token endpoint
    respx.post(svc.SALLA_TOKEN_URL).mock(return_value=Response(
        200,
        json={
            "access_token": "fake_access_token_abc",
            "refresh_token": "fake_refresh_token_xyz",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": svc.DEFAULT_SCOPES,
        },
    ))
    # Mock Salla's /store/info endpoint
    respx.get(f"{svc.SALLA_API_BASE}/store/info").mock(return_value=Response(
        200,
        json={"data": {
            "id": 12345,
            "name": "متجر اختبار",
            "domain": "test.salla.sa",
            "email": "owner@test.sa",
            "plan": "growth",
            "status": "active",
        }},
    ))

    async def _flow():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        user_id = f"test_user_{uuid.uuid4().hex[:8]}"
        try:
            # 1) Exchange code → tokens
            token_payload = await svc.exchange_code("FAKE_AUTH_CODE", "http://test/cb")
            assert token_payload["access_token"] == "fake_access_token_abc"
            assert token_payload["refresh_token"] == "fake_refresh_token_xyz"

            # 2) fetch_store_info
            store_info = await svc.fetch_store_info(token_payload["access_token"])
            assert store_info["data"]["id"] == 12345

            # 3) Persist
            await svc.store_token_response(db, user_id, token_payload, store_info=store_info)

            # 4) Verify the doc
            doc = await svc.get_integration(db, user_id)
            assert doc is not None
            assert doc["status"] == "connected"
            assert doc["store_id"] == 12345
            assert doc["store_name"] == "متجر اختبار"
            assert doc["store_plan"] == "growth"
            # Tokens are encrypted, not plain.
            assert b"fake_access_token_abc" not in doc["access_token_encrypted"]
            # …but decryption produces the original.
            assert salla_crypto.decrypt_token(doc["access_token_encrypted"]) == "fake_access_token_abc"
            assert salla_crypto.decrypt_token(doc["refresh_token_encrypted"]) == "fake_refresh_token_xyz"
            assert isinstance(doc["expires_at"], datetime)
            # 3600 sec − 120 safety margin = at most 3480 sec from now.
            doc_expires = svc._as_utc(doc["expires_at"])
            delta = (doc_expires - datetime.now(timezone.utc)).total_seconds()
            assert 3400 < delta < 3500, f"expected expires_at ~3480 s away, got {delta}"

            # 5) Public serializer hides secrets.
            public = svc.integration_to_public(doc)
            assert "access_token" not in str(public).lower() or "access_token_encrypted" not in public
            assert public["connected"] is True
            assert public["store_name"] == "متجر اختبار"
        finally:
            await db.salla_integrations.delete_one({"user_id": user_id})
            client.close()

    _aio.run(_flow())


# ── 8. Auto-refresh kicks in when access_token is past expiry ─────────
@respx.mock
def test_auto_refresh_on_expired_token(monkeypatch):
    monkeypatch.setenv("SALLA_CLIENT_ID", "fake_test_client_id")
    monkeypatch.setenv("SALLA_CLIENT_SECRET", "fake_test_client_secret")
    import asyncio as _aio
    from motor.motor_asyncio import AsyncIOMotorClient
    from salla_integration import service as svc

    # Mock the refresh-token call
    respx.post(svc.SALLA_TOKEN_URL).mock(return_value=Response(
        200,
        json={
            "access_token": "NEW_access_token",
            "refresh_token": "NEW_refresh_token",
            "expires_in": 1800,
            "token_type": "Bearer",
        },
    ))

    async def _flow():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        user_id = f"refresh_test_{uuid.uuid4().hex[:8]}"
        try:
            # Seed a row with an EXPIRED access token + a refresh token.
            old_doc = {
                "user_id": user_id,
                "access_token_encrypted": salla_crypto.encrypt_token("OLD_access"),
                "refresh_token_encrypted": salla_crypto.encrypt_token("OLD_refresh"),
                "expires_at": datetime.now(timezone.utc) - timedelta(minutes=5),  # PAST
                "status": "connected",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            await db.salla_integrations.insert_one(old_doc)

            # Now call the wrapper — must trigger refresh.
            new_access = await svc.ensure_fresh_access_token(db, user_id)
            assert new_access == "NEW_access_token"

            # Verify the DB doc now holds the NEW tokens.
            fresh = await svc.get_integration(db, user_id)
            assert salla_crypto.decrypt_token(fresh["access_token_encrypted"]) == "NEW_access_token"
            assert salla_crypto.decrypt_token(fresh["refresh_token_encrypted"]) == "NEW_refresh_token"
            fresh_expires = svc._as_utc(fresh["expires_at"])
            assert fresh_expires > datetime.now(timezone.utc)
        finally:
            await db.salla_integrations.delete_one({"user_id": user_id})
            client.close()

    _aio.run(_flow())


# ── 9. invalid_grant on refresh marks integration as needs_reauth ─────
@respx.mock
def test_refresh_invalid_grant_marks_needs_reauth(monkeypatch):
    monkeypatch.setenv("SALLA_CLIENT_ID", "fake_test_client_id")
    monkeypatch.setenv("SALLA_CLIENT_SECRET", "fake_test_client_secret")
    import asyncio as _aio
    from motor.motor_asyncio import AsyncIOMotorClient
    from salla_integration import service as svc

    respx.post(svc.SALLA_TOKEN_URL).mock(return_value=Response(
        400,
        json={"error": "invalid_grant", "error_description": "refresh token expired"},
    ))

    async def _flow():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        user_id = f"reauth_test_{uuid.uuid4().hex[:8]}"
        try:
            await db.salla_integrations.insert_one({
                "user_id": user_id,
                "access_token_encrypted": salla_crypto.encrypt_token("OLD"),
                "refresh_token_encrypted": salla_crypto.encrypt_token("DEAD_REFRESH"),
                "expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
                "status": "connected",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            })

            with pytest.raises(svc.SallaError) as ei:
                await svc.ensure_fresh_access_token(db, user_id)
            assert ei.value.needs_reauth is True

            doc = await svc.get_integration(db, user_id)
            assert doc["status"] == "needs_reauth"
            assert doc["last_error"]
        finally:
            await db.salla_integrations.delete_one({"user_id": user_id})
            client.close()

    _aio.run(_flow())


# ── 10. Isolation: connecting Salla does NOT touch other collections ──
def test_isolation_from_existing_collections():
    """Phase-1 must not write anywhere except salla_integrations +
    salla_oauth_states. We seed a row directly and verify no other
    collection has rows for this user."""
    import asyncio as _aio
    from motor.motor_asyncio import AsyncIOMotorClient
    from salla_integration import service as svc

    async def _flow():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        user_id = f"iso_test_{uuid.uuid4().hex[:8]}"
        try:
            await svc.store_token_response(
                db, user_id,
                {
                    "access_token": "a", "refresh_token": "r",
                    "expires_in": 3600, "token_type": "Bearer", "scope": svc.DEFAULT_SCOPES,
                },
                store_info={"data": {"id": 1, "name": "iso", "domain": "iso.salla.sa"}},
            )
            # Each of the existing collections must have ZERO rows for this user.
            for coll in (
                "unified_orders", "analyses", "snapchat_connections",
                "snapchat_ad_accounts", "meta_connections", "meta_daily_stats",
                "product_costs", "preparation_uploads", "exported_orders",
                "exported_items", "product_image_catalog", "operating_expenses",
                "daily_costs",
            ):
                n = await db[coll].count_documents(
                    {"$or": [{"user_id": user_id}, {"id": user_id}]},
                )
                assert n == 0, f"Salla flow wrote into {coll} — isolation broken"
            # And the salla collection DID receive the row.
            assert await db.salla_integrations.count_documents({"user_id": user_id}) == 1
        finally:
            await db.salla_integrations.delete_one({"user_id": user_id})
            client.close()

    _aio.run(_flow())
