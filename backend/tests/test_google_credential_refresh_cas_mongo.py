"""Real disposable Mongo races through the actual Google credential helpers.

Provider responses and crypto material are synthetic. All credential matching,
writes and majority readbacks use real Mongo. No live connection is touched.
"""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
import pytest
import pytest_asyncio

from integrations_control_center.google_oauth_security import GOOGLE_CREDENTIALS_COLLECTION
from tests.test_google_credential_refresh_cas import (
    SOURCES, USER, call_refresh, credential, error_code,
)


@pytest.fixture(scope="module")
def refresh_mongo_uri():
    configured = os.getenv("GOOGLE_REFRESH_CAS_TEST_MONGO_URI")
    if configured:
        parsed = urlsplit(configured)
        if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port is None or parsed.username or parsed.password
            or "," in parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Refresh CAS tests require one disposable loopback Mongo server")
        yield configured
        return
    binary = os.getenv("GOOGLE_REFRESH_CAS_TEST_MONGOD")
    if not binary or not Path(binary).is_file():
        pytest.skip("Set GOOGLE_REFRESH_CAS_TEST_MONGO_URI or GOOGLE_REFRESH_CAS_TEST_MONGOD for disposable Mongo")
    # Parent assigned this test-only port; never adopt an existing database path.
    port = 27181
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", port))
    with tempfile.TemporaryDirectory(prefix="google-refresh-cas-") as storage:
        process = subprocess.Popen([binary, "--dbpath", storage, "--port", str(port),
            "--bind_ip", "127.0.0.1", "--replSet", "gRefreshCAS", "--nounixsocket",
            "--logpath", str(Path(storage)/"mongo.log")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        uri = f"mongodb://127.0.0.1:{port}/"
        bootstrap = MongoClient(uri, directConnection=True, serverSelectionTimeoutMS=250)
        try:
            deadline = time.monotonic()+15
            while True:
                try:
                    bootstrap.admin.command("ping")
                    break
                except Exception:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Disposable refresh Mongo failed to start") from None
                    time.sleep(.05)
            bootstrap.admin.command("replSetInitiate", {"_id": "gRefreshCAS", "members": [
                {"_id": 0, "host": f"127.0.0.1:{port}"}]})
            while bootstrap.admin.command("hello").get("isWritablePrimary") is not True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Disposable refresh replica did not elect primary")
                time.sleep(.05)
            yield uri
        finally:
            bootstrap.close()
            process.terminate()
            process.wait(timeout=10)


@pytest_asyncio.fixture
async def refresh_db(refresh_mongo_uri):
    client = AsyncIOMotorClient(refresh_mongo_uri, serverSelectionTimeoutMS=1000, retryWrites=False)
    name = "google_refresh_cas_"+uuid.uuid4().hex
    db = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") and hello.get("isWritablePrimary") is True
        await db[GOOGLE_CREDENTIALS_COLLECTION].create_index("user_id", unique=True)
        await db[GOOGLE_CREDENTIALS_COLLECTION].insert_one(credential())
        yield db
    finally:
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_real_helpers_refresh_with_majority_commit_and_readback(monkeypatch, refresh_db, source):
    collection = refresh_db[GOOGLE_CREDENTIALS_COLLECTION]
    before = await collection.find_one({"user_id": USER}, {"_id": 0})
    result, calls = await call_refresh(source, monkeypatch, refresh_db)
    assert result == "new-access" and calls == ["refresh"]
    after = await collection.find_one({"user_id": USER}, {"_id": 0})
    assert after["access_token_ciphertext"] == "new-access"
    for field in ("refresh_token_ciphertext", "scope", "google_subject", "google_email"):
        assert after[field] == before[field]
    assert await collection.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_real_relink_during_http_wins_against_old_refresh(monkeypatch, refresh_db, source):
    collection = refresh_db[GOOGLE_CREDENTIALS_COLLECTION]
    async def relink():
        await collection.update_one({"user_id": USER}, {"$set": {
            "access_token_ciphertext": "relinked-access", "google_subject": "relinked-subject"}})
    with pytest.raises(Exception) as error:
        await call_refresh(source, monkeypatch, refresh_db, relink)
    assert error_code(error.value) == "google_oauth_refresh_conflict"
    assert (await collection.find_one({"user_id": USER}))["access_token_ciphertext"] == "relinked-access"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("direction", ["missing_to_null", "null_to_missing"])
async def test_real_cas_distinguishes_null_generation_from_absence(monkeypatch, refresh_db, source, direction):
    collection = refresh_db[GOOGLE_CREDENTIALS_COLLECTION]
    if direction == "null_to_missing":
        await collection.update_one({"user_id": USER}, {"$set": {"grant_generation": None}})
    async def relink():
        change = {"$set": {"grant_generation": None}} if direction == "missing_to_null" else {"$unset": {"grant_generation": ""}}
        await collection.update_one({"user_id": USER}, change)
    with pytest.raises(Exception) as error:
        await call_refresh(source, monkeypatch, refresh_db, relink)
    assert error_code(error.value) == "google_oauth_refresh_conflict"
    assert (await collection.find_one({"user_id": USER}))["access_token_ciphertext"] == "old-access"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_real_concurrent_refresh_has_one_winner_no_stale_return(monkeypatch, refresh_db, source):
    barrier = asyncio.Barrier(2)
    calls = []
    replies = await asyncio.wait_for(asyncio.gather(
        call_refresh(source, monkeypatch, refresh_db, barrier.wait, calls=calls),
        call_refresh(source, monkeypatch, refresh_db, barrier.wait, calls=calls), return_exceptions=True), timeout=10)
    successes = [reply for reply in replies if isinstance(reply, tuple)]
    failures = [reply for reply in replies if isinstance(reply, Exception)]
    assert len(successes) == len(failures) == 1
    assert successes[0][0] == "new-access"
    assert calls == ["refresh", "refresh"]  # Both in-flight reads reached the transport once.
    assert error_code(failures[0]) == "google_oauth_refresh_conflict"
    assert (await refresh_db[GOOGLE_CREDENTIALS_COLLECTION].find_one({"user_id": USER}))["access_token_ciphertext"] == "new-access"


class AfterActualWrite:
    """Inject only acknowledgement timing; matching and commit remain Mongo."""
    def __init__(self, collection, effect):
        self.collection, self.effect = collection, effect
    def with_options(self, **options):
        return AfterActualWrite(self.collection.with_options(**options), self.effect)
    async def find_one(self, *args, **kwargs):
        return await self.collection.find_one(*args, **kwargs)
    async def update_one(self, *args, **kwargs):
        result = await self.collection.update_one(*args, **kwargs)
        await self.effect()
        return result


class WrappedDB:
    def __init__(self, db, effect):
        self.db, self.effect = db, effect
    def __getitem__(self, name):
        return AfterActualWrite(self.db[name], self.effect)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("outcome", ["lost_ack", "relink_after_commit"])
async def test_real_commit_does_not_return_token_when_ack_or_readback_unproved(monkeypatch, refresh_db, source, outcome):
    collection = refresh_db[GOOGLE_CREDENTIALS_COLLECTION]
    async def effect():
        if outcome == "lost_ack":
            raise ConnectionError("private-transport-detail")
        await collection.update_one({"user_id": USER}, {"$set": {"access_token_ciphertext": "relinked-access"}})
    wrapped = WrappedDB(refresh_db, effect)
    with pytest.raises(Exception) as error:
        await call_refresh(source, monkeypatch, wrapped)
    assert error_code(error.value) == ("google_oauth_refresh_unconfirmed" if outcome == "lost_ack" else "google_oauth_refresh_conflict")
    assert "private-transport-detail" not in str(error.value)
    assert (await collection.find_one({"user_id": USER}))["access_token_ciphertext"] == (
        "new-access" if outcome == "lost_ack" else "relinked-access")
