"""Opt-in destructive fault injection ONLY on a disposable loopback replica set.

OBS_TEST_MONGO_URI=mongodb://127.0.0.1:27851/?replicaSet=obsPhase1
Run mongod 8.0.12 with --setParameter enableTestCommands=1. Never production.
"""
import os
import multiprocessing
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, parse_qs

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure, WaitQueueTimeoutError

from mongo_observability import MongoMetrics
from observability_metrics import Metrics


@pytest.fixture
def local_mongo(monkeypatch):
    uri = os.environ.get("OBS_TEST_MONGO_URI")
    if not uri:
        pytest.skip("explicit disposable OBS_TEST_MONGO_URI not provided")
    parts = urlsplit(uri)
    assert parts.scheme == "mongodb" and parts.hostname == "127.0.0.1"
    assert parts.port == 27851 and parts.username is None and parts.password is None
    assert parse_qs(parts.query).get("replicaSet") == ["obsPhase1"]
    admin = MongoClient(uri, serverSelectionTimeoutMS=5000)
    assert admin.admin.command("buildInfo")["version"] == "8.0.12"
    assert admin.admin.command("hello")["setName"] == "obsPhase1"
    recorder = Metrics(True)
    monkeypatch.setattr("mongo_observability.metrics", recorder)
    listener = MongoMetrics()
    app = "obs-phase1-test-" + uuid.uuid4().hex
    client = MongoClient(uri, appname=app, maxPoolSize=1,
                         waitQueueTimeoutMS=150, event_listeners=[listener])
    dbname = "obs_phase1_" + uuid.uuid4().hex
    db = client[dbname]
    db.synthetic.insert_one({"fixture": True})

    def block(command, milliseconds):
        try:
            admin.admin.command({"configureFailPoint": "failCommand", "mode": {"times": 1},
                "data": {"failCommands": [command], "appName": app,
                         "blockConnection": True, "blockTimeMS": milliseconds}})
        except OperationFailure as exc:
            if exc.code in (59, 72, 115):
                pytest.skip("local failCommand/blockConnection unavailable")
            raise
    try:
        yield client, db, recorder, listener, block
    finally:
        admin.admin.command({"configureFailPoint": "failCommand", "mode": "off"})
        client.drop_database(dbname)
        client.close()
        admin.close()


def test_real_pool_saturation_is_observed(local_mongo):
    client, db, recorder, listener, block = local_mongo
    started = threading.Event()
    original = listener.started
    def notified(event):
        original(event)
        if event.command_name == "find":
            started.set()
    listener.started = notified
    block("find", 650)
    with ThreadPoolExecutor(max_workers=1) as executor:
        held = executor.submit(db.synthetic.find_one, {"fixture": True})
        assert started.wait(5)
        with pytest.raises(WaitQueueTimeoutError):
            db.synthetic.find_one({"fixture": True})
        assert held.result(timeout=5)["fixture"] is True
    hist = recorder.snapshot()["histograms"]
    assert hist["mongo.pool.wait.error"]["sum"] >= .1
    assert hist["mongo.command.find.ok"]["sum"] >= .5
    assert listener.snapshot()["checkout_timeouts"] >= 1


def test_real_slow_commit_observed_without_altering_result(local_mongo):
    client, db, recorder, listener, block = local_mongo
    with client.start_session() as session:
        session.start_transaction()
        db.synthetic.insert_one({"transaction_fixture": True}, session=session)
        block("commitTransaction", 400)
        start = time.monotonic()
        session.commit_transaction()
        elapsed = time.monotonic() - start
    assert elapsed >= .35
    assert db.synthetic.count_documents({"transaction_fixture": True}) == 1
    row = recorder.snapshot()["histograms"]["mongo.command.commitTransaction.ok"]
    assert row["count"] == 1 and row["sum"] >= .35


def _mongo_worker(pipe, validated_uri, dbname, marker):
    """Child receives only the loopback URI already checked by local_mongo."""
    import observability_metrics as registry
    import mongo_observability as mongo

    registry.metrics.__init__(True)
    mongo.metrics = registry.metrics
    before = registry.metrics.snapshot()
    listener = mongo.MongoMetrics()
    client = MongoClient(validated_uri, appname="obs-phase1-child-" + marker,
                         serverSelectionTimeoutMS=5000, socketTimeoutMS=5000,
                         maxPoolSize=1, waitQueueTimeoutMS=1000,
                         event_listeners=[listener])
    try:
        db = client[dbname]
        assert db.synthetic.find_one({"fixture": True})["fixture"] is True
        with client.start_session() as session:
            session.start_transaction()
            db.synthetic.insert_one({"child_marker": marker}, session=session)
            session.commit_transaction()
        pipe.send({"before": before, "after": registry.metrics.snapshot()})
    finally:
        client.close()
        pipe.close()


def test_real_mongo_metrics_are_independent_per_spawned_worker(local_mongo):
    _, db, _, _, _ = local_mongo
    context = multiprocessing.get_context("spawn")
    children = []
    results = []
    try:
        for marker in ("worker-a", "worker-b"):
            receive, send = context.Pipe(duplex=False)
            child = context.Process(target=_mongo_worker,
                                    args=(send, os.environ["OBS_TEST_MONGO_URI"], db.name, marker))
            child.start()
            send.close()
            children.append((child, receive))
        for child, receive in children:
            assert receive.poll(12), "Mongo child did not report within deadline"
            results.append(receive.recv())
            child.join(2)
            assert child.exitcode == 0
        assert len({row["after"]["worker"]["pid"] for row in results}) == 2
        for result in results:
            assert result["before"]["histograms"] == {}
            hist = result["after"]["histograms"]
            assert hist["mongo.command.commitTransaction.ok"]["count"] == 1
            assert hist["mongo.command.find.ok"]["count"] >= 1
            assert hist["mongo.pool.wait.ok"]["count"] > 0
            assert hist["mongo.pool.wait.ok"]["sum"] > 0
        assert db.synthetic.count_documents({"child_marker": {"$in": ["worker-a", "worker-b"]}}) == 2
    finally:
        for child, receive in children:
            if child.is_alive():
                child.terminate()
            child.join(2)
            receive.close()
