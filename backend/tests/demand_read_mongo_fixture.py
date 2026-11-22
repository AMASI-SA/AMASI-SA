"""Owned local Mongo only; no environment or production database fallback."""
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlsplit
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def read_mongo_uri(tmp_path_factory, request):
    supplied = os.environ.get("DEMAND_READ_TEST_MONGO_URI")
    if supplied:
        parsed = urlsplit(supplied)
        assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port
        assert parsed.username is None and parsed.password is None
        yield supplied
        return
    # This fixture is imported by several explicit --noconftest modules. Pytest
    # creates a FixtureDef per importer; share one owned replica for the session
    # instead of launching duplicate servers while the first is still running.
    existing = getattr(request.config, "_demand_read_owned_mongo_uri", None)
    if existing is not None:
        yield existing
        return
    binary = os.environ.get("DEMAND_READ_TEST_MONGOD")
    if not binary:
        pytest.skip("DEMAND_READ_TEST_MONGO_URI or DEMAND_READ_TEST_MONGOD required; no production fallback")
    assert Path(binary).is_file()
    path = tmp_path_factory.mktemp("demand_read_mongo")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = subprocess.Popen([binary, "--nounixsocket", "--bind_ip", "127.0.0.1", "--port", str(port),
        "--dbpath", str(path), "--replSet", "demandreadtest", "--wiredTigerCacheSizeGB", "0.25",
        "--logpath", str(path / "mongod.log")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    direct = MongoClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", serverSelectionTimeoutMS=250)
    try:
        deadline = time.monotonic() + 15
        while True:
            assert process.poll() is None, "owned test mongod exited"
            try:
                direct.admin.command("ping")
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        direct.admin.command({"replSetInitiate": {"_id": "demandreadtest", "members": [
            {"_id": 0, "host": f"127.0.0.1:{port}"}]}})
        while not direct.admin.command("hello").get("isWritablePrimary"):
            assert time.monotonic() < deadline, "owned test replica did not elect primary"
            time.sleep(.05)
        uri = f"mongodb://127.0.0.1:{port}/?replicaSet=demandreadtest"
        request.config._demand_read_owned_mongo_uri = uri
        yield uri
    finally:
        if hasattr(request.config, "_demand_read_owned_mongo_uri"):
            del request.config._demand_read_owned_mongo_uri
        direct.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest_asyncio.fixture
async def read_db(read_mongo_uri):
    client = AsyncIOMotorClient(read_mongo_uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    assert (await client.admin.command("hello")).get("setName")
    name = "demand_read_test_" + uuid4().hex
    try:
        yield client[name]
    finally:
        assert name.startswith("demand_read_test_")
        await client.drop_database(name)
        client.close()
