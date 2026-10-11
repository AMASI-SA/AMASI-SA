"""Disposable Mongo and fixed HTTP transport tests. No TikTok/model requests."""
import asyncio
from datetime import timedelta
import json
import os
import uuid
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from fastapi import HTTPException
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from integrations_control_center import tiktok_creator_accounts as accounts
from integrations_control_center import tiktok_creator_api as transport
from integrations_control_center.tiktok_oauth_security import decrypt_tiktok_token, encrypt_tiktok_token


class CreatorProvider:
    def __init__(self):
        self.creator_id = "fixture-creator-id"
        self.scopes = "video.publish,video.upload,video.list,comment.list"
        self.calls = []
        self.behavior = None

    async def handle(self, request):
        assert request.url.host == "business-api.tiktok.com"
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append({"method": request.method, "path": path, "body": body, "params": dict(request.url.params)})
        if path.endswith("/tt_user/token_info/get/"):
            assert request.method == "POST"
            assert body["app_id"] == "fixture-app"
            assert "Access-Token" not in request.headers
            data = {"app_id": "fixture-app", "creator_id": "foreign-creator" if self.behavior == "identity_mismatch" else self.creator_id, "scope": self.scopes}
        elif path.endswith("/tt_user/oauth2/token/") or path.endswith("/tt_user/oauth2/refresh_token/"):
            assert body["client_id"] == "fixture-app" and body["client_secret"] == "fixture-secret"
            assert body["grant_type"] == ("refresh_token" if path.endswith("/refresh_token/") else "authorization_code")
            data = {"access_token": "fixture-access-new", "refresh_token": "fixture-refresh-new", "open_id": self.creator_id, "expires_in": 86400, "refresh_token_expires_in": 31536000, "scope": "unproven_requested_scope"}
            if self.behavior == "refresh_timeout" and path.endswith("/refresh_token/"):
                raise httpx.ReadTimeout("fixture", request=request)
        elif path.endswith("/business/property/list/"):
            assert request.method == "GET" and dict(request.url.params) == {"app_id": "fixture-app", "secret": "fixture-secret"}
            data = {"url_property_info_list": [{"property_type": 1, "url": "media.example.test", "property_status": 1, "signature": "private-signature"}]}
        else:
            raise AssertionError(path)
        if self.behavior == "oversized":
            data["unused"] = "x" * transport.MAX_RESPONSE_BYTES
        return httpx.Response(200, json={"code": 0, "data": data})

    def api(self):
        return transport.TikTokCreatorAPI(transport=httpx.MockTransport(self.handle))


@pytest_asyncio.fixture
async def environment(monkeypatch):
    uri = os.environ.get("TEST_TIKTOK_MONGO_URL")
    if not uri:
        pytest.skip("Explicit disposable TEST_TIKTOK_MONGO_URL required")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000, tz_aware=True)
    await client.admin.command("ping")
    db = client["tiktok_content_test_" + uuid.uuid4().hex]
    for key, value in {"TIKTOK_MARKETING_APP_ID": "fixture-app", "TIKTOK_MARKETING_APP_SECRET": "fixture-secret", "TIKTOK_TOKEN_ENC_KEY": Fernet.generate_key().decode(), "JWT_SECRET": "fixture-state-key", "TIKTOK_MARKETING_REDIRECT_URI": "https://example.test/api/integrations-v2/tiktok/callback", "FRONTEND_URL": "https://example.test"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TIKTOK_CREATOR_REDIRECT_URI", raising=False)
    monkeypatch.delenv("TIKTOK_OAUTH_STATE_SECRET", raising=False)
    monkeypatch.setattr(transport.governor, "peek", lambda: ("normal", None))
    provider = CreatorProvider()
    monkeypatch.setattr(accounts, "TikTokCreatorAPI", provider.api)
    await db.users.insert_one({"id": "owner", "role": "owner"})
    await accounts.ensure_indexes(db)
    try:
        yield db, provider
    finally:
        await client.drop_database(db.name)
        client.close()


async def connect(db, provider, user="owner"):
    result = await accounts.start_creator_connection(db, user, accounts.CreatorStartInput(label="Fixture account"))
    response = await accounts.handle_creator_callback(db, auth_code="fixture-single-use-code", state=result["state"], binding=accounts.browser_binding(result["state"]))
    return result, response


async def seed_creator(db, *, owner="owner", expires=None):
    ref = "a" * 64
    await db[accounts.CREDENTIALS].insert_one({"_id": ref, "user_id": owner, "app_id": "fixture-app", "status": "connected", "label": "Fixture account", "auth_epoch": "epoch-1", "scopes": ["video.publish"], "expires_at": expires or accounts.now() + timedelta(hours=1), "refresh_expires_at": accounts.now() + timedelta(days=30), "access_token_encrypted": encrypt_tiktok_token("fixture-access-old"), "refresh_token_encrypted": encrypt_tiktok_token("fixture-refresh-old"), "creator_id_encrypted": encrypt_tiktok_token("fixture-creator-id"), "created_at": accounts.now()})
    return ref


@pytest.mark.asyncio
async def test_creator_oauth_is_browser_bound_single_use_and_contains_no_owner_id(environment):
    db, provider = environment
    start, response = await connect(db, provider)
    query = parse_qs(urlsplit(start["authorization_url"]).query)
    assert query["disable_auto_auth"] == ["1"]
    assert query["redirect_uri"] == ["https://example.test/api/integrations-v2/tiktok/callback"]
    assert query["scope"] == [",".join(accounts.SCOPES)]
    state_payload = accounts._decode_state(start["state"])
    assert "user_id" not in state_payload
    assert "creator_connected=1" in response.headers["location"]
    row = await db[accounts.CREDENTIALS].find_one({"user_id": "owner"})
    assert row["scopes"] == sorted(provider.scopes.split(","))
    assert decrypt_tiktok_token(row["access_token_encrypted"]) == "fixture-access-new"
    assert row["creator_id_encrypted"] != b"fixture-creator-id"
    assert await db.mezan_tiktok_oauth_credentials_v2.count_documents({}) == 0
    repeated = await accounts.handle_creator_callback(db, auth_code="fixture-single-use-code", state=start["state"], binding=accounts.browser_binding(start["state"]))
    assert "creator_error=authorization_failed" in repeated.headers["location"]
    assert len(provider.calls) == 2
    public = json.dumps(await accounts.list_creators(db, "owner"), default=str)
    for secret in ("fixture-access-new", "fixture-refresh-new", "fixture-creator-id", "encrypted", "auth_epoch"):
        assert secret not in public


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["bad_binding", "expired", "owner_revoked", "identity_mismatch"])
async def test_bad_creator_authorization_never_persists_credentials(environment, behavior):
    db, provider = environment
    start = await accounts.start_creator_connection(db, "owner", accounts.CreatorStartInput())
    binding = accounts.browser_binding(start["state"])
    if behavior == "bad_binding":
        binding = "wrong-browser"
    if behavior == "expired":
        await db[accounts.STATES].update_many({}, {"$set": {"expires_at": accounts.now() - timedelta(seconds=1)}})
    if behavior == "owner_revoked":
        await db.users.update_one({"id": "owner"}, {"$set": {"role": "staff"}})
    provider.behavior = behavior
    response = await accounts.handle_creator_callback(db, auth_code="private-code", state=start["state"], binding=binding)
    assert "creator_error=authorization_failed" in response.headers["location"]
    assert "private-code" not in response.headers["location"]
    assert await db[accounts.CREDENTIALS].count_documents({}) == 0
    if behavior != "identity_mismatch":
        assert not provider.calls


@pytest.mark.asyncio
async def test_another_owner_cannot_take_over_creator_binding(environment):
    db, provider = environment
    await connect(db, provider)
    await db.users.insert_one({"id": "other-owner", "role": "owner"})
    _, response = await connect(db, provider, user="other-owner")
    assert "creator_error=authorization_failed" in response.headers["location"]
    assert await db[accounts.CREDENTIALS].count_documents({"user_id": "owner"}) == 1
    assert await db[accounts.CREDENTIALS].count_documents({"user_id": "other-owner"}) == 0


@pytest.mark.asyncio
async def test_expired_access_token_rotates_once_and_verifies_actual_scopes(environment):
    db, provider = environment
    ref = await seed_creator(db, expires=accounts.now() - timedelta(seconds=1))
    async with provider.api() as api:
        row, token, creator_id = await accounts.verified_creator(db, "owner", ref, api, "video.publish")
    assert token == "fixture-access-new" and creator_id == provider.creator_id
    assert row["auth_epoch"] != "epoch-1"
    assert [c["path"].rsplit("/", 2)[-2] for c in provider.calls] == ["refresh_token", "get"]
    stored = await db[accounts.CREDENTIALS].find_one({"_id": ref})
    assert stored["status"] == "connected" and "refresh_claim" not in stored


@pytest.mark.asyncio
async def test_ambiguous_rotating_refresh_is_never_retried(environment):
    db, provider = environment
    ref = await seed_creator(db, expires=accounts.now() - timedelta(seconds=1))
    provider.behavior = "refresh_timeout"
    for _ in range(2):
        with pytest.raises(HTTPException):
            async with provider.api() as api:
                await accounts.verified_creator(db, "owner", ref, api, "video.publish")
    assert len(provider.calls) == 1
    assert (await db[accounts.CREDENTIALS].find_one({"_id": ref}))["status"] == "needs_reauth"


@pytest.mark.asyncio
async def test_foreign_creator_and_missing_scope_block_before_any_publish(environment):
    db, provider = environment
    ref = await seed_creator(db, owner="other-owner")
    async with provider.api() as api:
        with pytest.raises(HTTPException):
            await accounts.verified_creator(db, "owner", ref, api, "video.publish")
    assert not provider.calls
    await db[accounts.CREDENTIALS].update_one({"_id": ref}, {"$set": {"user_id": "owner"}})
    provider.scopes = "video.list"
    async with provider.api() as api:
        with pytest.raises(HTTPException) as caught:
            await accounts.verified_creator(db, "owner", ref, api, "video.publish")
    assert caught.value.detail["code"] == "tiktok_creator_missing_permission"


@pytest.mark.asyncio
async def test_provider_response_memory_and_secret_logging_are_bounded(environment, caplog):
    _, provider = environment
    caplog.set_level("DEBUG")
    async with provider.api() as api:
        await api.call("GET", "/business/property/list/", params={"app_id": "fixture-app", "secret": "fixture-secret"})
    assert "fixture-secret" not in caplog.text and "private-signature" not in caplog.text
    provider.behavior = "oversized"
    with pytest.raises(HTTPException) as caught:
        async with provider.api() as api:
            await api.call("GET", "/business/property/list/", params={"app_id": "fixture-app", "secret": "fixture-secret"})
    assert caught.value.detail["code"] == "tiktok_creator_response_too_large"


@pytest.mark.asyncio
async def test_admission_rejects_pressure_without_queueing(environment, monkeypatch):
    _, provider = environment
    monkeypatch.setattr(transport.governor, "peek", lambda: ("blocked", None))
    with pytest.raises(HTTPException) as caught:
        async with transport.admission():
            raise AssertionError("must not admit")
    assert caught.value.status_code == 503 and not provider.calls
