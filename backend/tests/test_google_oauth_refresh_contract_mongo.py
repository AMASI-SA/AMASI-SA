"""Disposable loopback replica: no real provider or user data."""
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center.google_oauth_generation import RefreshConflict
from integrations_control_center.google_oauth_refresh_contract import EncryptedRefresh, refresh_once

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def database():
    uri = os.getenv("GOOGLE_ADS_TEST_MONGO_URI")
    if not uri:
        pytest.skip("disposable loopback replica required")
    u = urlsplit(uri)
    if (u.scheme != "mongodb" or u.hostname not in ("localhost", "127.0.0.1") or u.port is None
        or u.username or u.password or "," in u.netloc or u.path not in ("", "/") or u.query or u.fragment):
        raise ValueError("loopback only")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000, retryWrites=False)
    db = client["oauth_refresh_wip_" + uuid4().hex]
    try:
        hello = await db.command("hello")
        assert hello.get("setName") and hello.get("isWritablePrimary") and type(hello.get("logicalSessionTimeoutMinutes")) is int
        await db.credentials.insert_one({"user_id": "owner", "grant_generation": "old", "token_revision": 0,
                                         "access_token_ciphertext": "old"})
        yield db
    finally:
        await client.drop_database(db.name)
        client.close()


def args(db, source, send):
    return dict(source=source, operations=db.operations, credentials=db.credentials,
                user_id="owner", grant_generation="old", token_revision=0,
                operation_id=str(uuid4()), attempt_id=str(uuid4()), now=NOW, send=send)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["ads", "ga4", "merchant"])
async def test_relink_during_send_fences_stale_commit(database, source):
    sends = []
    async def send():
        sends.append(1)
        await database.credentials.update_one({"user_id": "owner"}, {"$set": {
            "grant_generation": "new", "token_revision": 0, "access_token_ciphertext": "new"}})
        return EncryptedRefresh("stale", NOW)
    with pytest.raises(RefreshConflict):
        await refresh_once(**args(database, source, send))
    row = await database.credentials.find_one({"user_id": "owner"})
    assert row["grant_generation"] == "new" and row["access_token_ciphertext"] == "new"
    assert sends == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["ads", "ga4", "merchant"])
async def test_lost_commit_ack_does_not_return_or_repeat_send(database, source):
    class LostAck:
        def __init__(self, collection):
            self.collection = collection
            self.database = collection.database
        def with_options(self, **options):
            self.target = self.collection.with_options(**options)
            return self
        async def update_one(self, *a, **kw):
            await self.target.update_one(*a, **kw)
            raise TimeoutError("synthetic lost acknowledgement")
    sends = []
    async def send():
        sends.append(1)
        return EncryptedRefresh("refreshed", NOW)
    options = args(database, source, send)
    options["credentials"] = LostAck(database.credentials)
    with pytest.raises(TimeoutError):
        await refresh_once(**options)
    row = await database.credentials.find_one({"user_id": "owner"})
    assert row["access_token_ciphertext"] == "refreshed" and row["token_revision"] == 1
    assert sends == [1]
