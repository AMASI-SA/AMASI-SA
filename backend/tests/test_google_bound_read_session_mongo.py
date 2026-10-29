"""Bound read snapshots against disposable Mongo; all crypto/provider values synthetic."""
import os
from urllib.parse import urlsplit
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.write_concern import WriteConcern
import pytest
import pytest_asyncio

from integrations_control_center import google_ads_reporting as module
from tests.test_google_bound_read_session import NOW, USER, TOKEN, credential, providers, session
from tests.test_google_credential_refresh_cas_mongo import WrappedDB

ACTUAL_CREDENTIAL = module._credential


@pytest_asyncio.fixture
async def bound_db():
    uri = os.getenv("GOOGLE_BOUND_READ_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set a disposable loopback GOOGLE_BOUND_READ_TEST_MONGO_URI")
    parsed = urlsplit(uri)
    if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None or parsed.username or parsed.password or "," in parsed.netloc
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Bound-read tests require one disposable loopback replica")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1000, retryWrites=False)
    name = "google_bound_read_" + uuid.uuid4().hex
    db = client.get_database(name, write_concern=WriteConcern(w="majority", j=True))
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") and hello.get("isWritablePrimary") is True
        await db[module.GOOGLE_CREDENTIALS_COLLECTION].insert_one(credential())
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def actual_credential_providers(monkeypatch, db):
    calls = providers(monkeypatch, db)
    monkeypatch.setattr(module, "_credential", ACTUAL_CREDENTIAL)
    return calls


@pytest.mark.asyncio
async def test_real_majority_snapshot_binds_actual_unexpired_credential(monkeypatch, bound_db):
    calls = actual_credential_providers(monkeypatch, bound_db)
    async with session(bound_db) as bound:
        assert (await bound.account_metadata())["account_id"] == "5052346701"
        assert (await bound.search("SELECT product_link.resource_name FROM product_link"))[1] is True
    assert calls["ads_tokens"] == [TOKEN]
    row = await bound_db[module.GOOGLE_CREDENTIALS_COLLECTION].find_one({"user_id": USER})
    assert row["access_token_ciphertext"] == "synthetic-ciphertext"


@pytest.mark.asyncio
async def test_real_relink_between_token_return_and_bound_snapshot_refuses_old_token(monkeypatch, bound_db):
    calls = actual_credential_providers(monkeypatch, bound_db)
    async def relink_after_token(db, user, now):
        token = await ACTUAL_CREDENTIAL(db, user, now)
        await db[module.GOOGLE_CREDENTIALS_COLLECTION].update_one({"user_id": user}, {"$set": {"access_token_ciphertext": "relinked-ciphertext", "google_subject": "other-subject"}})
        return token
    monkeypatch.setattr(module, "_credential", relink_after_token)
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(bound_db):
            pytest.fail("old token escaped")
    assert calls["ads_tokens"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["scope", "subject", "revoke"])
async def test_real_change_before_result_escape_discards_bound_session(monkeypatch, bound_db, change):
    actual_credential_providers(monkeypatch, bound_db)
    collection = bound_db[module.GOOGLE_CREDENTIALS_COLLECTION]
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(bound_db) as bound:
            await bound.search("SELECT product_link.resource_name FROM product_link")
            if change == "revoke":
                await collection.delete_one({"user_id": USER})
            else:
                patch = {"scope": []} if change == "scope" else {"google_subject": "other-subject"}
                await collection.update_one({"user_id": USER}, {"$set": patch})


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["missing_to_null", "null_to_missing"])
async def test_real_absent_null_credential_revision_is_fenced(monkeypatch, bound_db, direction):
    actual_credential_providers(monkeypatch, bound_db)
    collection = bound_db[module.GOOGLE_CREDENTIALS_COLLECTION]
    if direction == "null_to_missing":
        await collection.update_one({"user_id": USER}, {"$set": {"grant_generation": None}})
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(bound_db):
            patch = {"$set": {"grant_generation": None}} if direction == "missing_to_null" else {"$unset": {"grant_generation": ""}}
            await collection.update_one({"user_id": USER}, patch)


@pytest.mark.asyncio
async def test_real_bson_type_change_cannot_equal_python_numeric_value(monkeypatch, bound_db):
    actual_credential_providers(monkeypatch, bound_db)
    collection = bound_db[module.GOOGLE_CREDENTIALS_COLLECTION]
    await collection.update_one({"user_id": USER}, {"$set": {"token_revision": 1}})
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(bound_db):
            await collection.update_one({"user_id": USER}, {"$set": {"token_revision": True}})


@pytest.mark.asyncio
async def test_real_duplicate_credential_insert_discards_observation(monkeypatch, bound_db):
    actual_credential_providers(monkeypatch, bound_db)
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(bound_db):
            await bound_db[module.GOOGLE_CREDENTIALS_COLLECTION].insert_one(credential())


@pytest.mark.asyncio
async def test_real_refresh_commit_lost_ack_never_opens_read_session_or_retries(monkeypatch, bound_db):
    calls = actual_credential_providers(monkeypatch, bound_db)
    collection = bound_db[module.GOOGLE_CREDENTIALS_COLLECTION]
    await collection.update_one({"user_id": USER}, {"$set": {"expires_at": NOW}})
    monkeypatch.setattr(module, "decrypt_google_token", lambda value: value or "")
    monkeypatch.setattr(module, "encrypt_google_token", lambda value: value)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "synthetic-secret")
    refreshes = []
    class Response:
        status_code = 200
        def json(self):
            return {"access_token": "synthetic-new-access", "expires_in": 3600, "token_type": "Bearer"}
    class OAuth:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            refreshes.append("synthetic-refresh")
            return Response()
    monkeypatch.setattr(module.httpx, "AsyncClient", OAuth)
    async def lost_ack():
        raise ConnectionError("PRIVATE_ACK_SENTINEL")
    with pytest.raises(module.GoogleBoundReadError) as caught:
        async with session(WrappedDB(bound_db, lost_ack)):
            pytest.fail("unknown refresh acknowledgement escaped")
    assert "PRIVATE" not in str(caught.value)
    assert refreshes == ["synthetic-refresh"] and calls["ads_tokens"] == []
    assert (await collection.find_one({"user_id": USER}))["access_token_ciphertext"] == "synthetic-new-access"
