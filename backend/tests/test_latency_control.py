"""Synthetic, disposable Mongo only. Never imports the business server."""
import asyncio
import contextlib
import json
import logging
import os
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import latency_evidence as evidence
from latency_api_timing import LatencyEvidenceMiddleware
from latency_control import Controller, SessionRequest, attach_latency_control, COLLECTION


@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("LATENCY_TEST_MONGO_URI", "")
    assert uri.startswith("mongodb://127.0.0.1:27968/"), "isolated Mongo on approved test port required"
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1000)
    assert (await client.server_info())["version"] == "8.0.12"
    assert (await client.admin.command("hello"))["setName"] == "latencyTest"
    database = client["latency_control_test_" + uuid.uuid4().hex]
    try:
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.fixture(autouse=True)
def reset(monkeypatch, caplog):
    monkeypatch.setattr(evidence, "_control", None)
    monkeypatch.setattr(evidence, "_enabled", False)
    monkeypatch.setattr(evidence, "_configured", False)
    monkeypatch.setattr(evidence, "_last", {})
    monkeypatch.setattr(evidence, "_active", {})
    monkeypatch.setattr(evidence, "_identity", {"release_id": "synthetic", "source_git_sha": "synthetic"})
    for key in ("LATENCY_EVIDENCE_ENABLED", "LATENCY_EVIDENCE_CONTROL_ENABLED",
                "LATENCY_EVIDENCE_CONTROL_TOKEN", "LATENCY_EVIDENCE_EXPECTED_WORKERS",
                "LATENCY_EVIDENCE_PERMIT_FILE", "INTERNAL_DIAGNOSTICS_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    caplog.set_level(logging.INFO, logger="mezan.latency_evidence")


def command(revision, session=None, duration=900):
    return SessionRequest(release_id="synthetic", session_id=session or uuid.uuid4(),
                          expected_revision=revision, duration_seconds=duration)


async def workers(db):
    a, b = [Controller(db[COLLECTION], "synthetic", 2) for _ in range(2)]
    await a.poll_once()
    await b.poll_once()
    return a, b


@pytest.mark.asyncio
async def test_two_workers_start_stop_ack_and_no_business_writes(db):
    await db.orders.insert_one({"_id": "synthetic-order", "value": 42})
    a, b = await workers(db)
    request = command(0)
    result = await a.change(request, True)
    assert result["state"] == "unconfirmed"
    await a.poll_once()
    assert (await a.status())["state"] == "unconfirmed"
    await b.poll_once()
    assert (await b.status())["state"] == "enabled_confirmed"
    assert a.allowed() and b.allowed()
    stopped = await b.change(command(1, request.session_id), False)
    assert stopped["state"] == "unconfirmed"
    await a.poll_once()
    await b.poll_once()
    assert (await a.status())["state"] == "disabled_confirmed"
    assert not a.allowed() and not b.allowed()
    assert await db.orders.find_one({}) == {"_id": "synthetic-order", "value": 42}
    assert set(await db.list_collection_names()) == {COLLECTION, "orders"}


@pytest.mark.asyncio
async def test_concurrent_start_one_winner_and_replay_no_renewal(db):
    a, b = await workers(db)
    x, y = command(0), command(0)
    results = await asyncio.gather(a.change(x, True), b.change(y, True), return_exceptions=True)
    assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1
    doc = await a.document()
    before = doc["expires_at"]
    await a.change(command(0, uuid.UUID(doc["session_id"])), True)
    assert (await a.document())["expires_at"] == before
    assert (await a.document())["revision"] == 1


@pytest.mark.asyncio
async def test_expiry_without_polling_and_no_startup_replay(db):
    a, b = await workers(db)
    await a.change(command(0, duration=1), True)
    await a.poll_once()
    await b.poll_once()
    restarted = Controller(db[COLLECTION], "synthetic", 2)
    await restarted.poll_once()
    assert not restarted.allowed()
    a.monotonic = lambda: a.deadline + 1
    assert not a.allowed()  # No event loop callback needed at emission boundary.


@pytest.mark.asyncio
async def test_missing_worker_never_confirmed_and_release_guard(db):
    a, b = await workers(db)
    request = command(0)
    await a.change(request, True)
    await a.poll_once()
    await b.poll_once()
    await db[COLLECTION].update_one({"_id": "synthetic"},
                                  {"$set": {f"workers.{b.worker_id}.seen_at": 0}})
    assert (await a.status())["state"] == "unconfirmed"
    wrong = command(1, request.session_id)
    wrong.release_id = "another-release"
    with pytest.raises(HTTPException, match="release_identity_mismatch"):
        await a.change(wrong, False)


@pytest.mark.asyncio
async def test_control_failure_disables_and_no_log_after_ack(db, monkeypatch, caplog):
    a, b = await workers(db)
    request = command(0)
    await a.change(request, True)
    await a.poll_once()
    evidence.bind_control(a)
    with evidence.capture("dashboard"):
        evidence.lag_observed(350)
        await a.change(command(1, request.session_id), False)
    assert "stage_sample" not in caplog.text
    assert "event_loop_lag" in caplog.text
    before = caplog.text
    evidence.lag_observed(900)
    assert caplog.text == before
    async def unavailable():
        raise OSError("must-not-be-logged-secret")
    monkeypatch.setattr(a, "document", unavailable)
    await a.poll_once()
    assert not a.allowed() and a.reason == "control_unavailable"
    assert "must-not-be-logged-secret" not in caplog.text


@pytest.mark.asyncio
async def test_readback_after_lost_start_response_cannot_extend_session(db):
    a, b = await workers(db)
    request = command(0)
    await a.change(request, True)  # Simulate caller losing this response.
    snapshot = await a.document()
    await b.change(request, True)
    assert (await b.document()) == snapshot


@pytest.mark.asyncio
async def test_auth_defaults_and_explicit_shared_mode(db, monkeypatch):
    app = FastAPI()
    attach_latency_control(app, db, "synthetic")
    assert evidence._control is None
    assert await db.list_collection_names() == []
    monkeypatch.setenv("LATENCY_EVIDENCE_ENABLED", "1")
    monkeypatch.setenv("LATENCY_EVIDENCE_CONTROL_ENABLED", "1")
    monkeypatch.setenv("LATENCY_EVIDENCE_CONTROL_TOKEN", "x" * 40)
    monkeypatch.setenv("LATENCY_EVIDENCE_EXPECTED_WORKERS", "2")
    monkeypatch.setenv("INTERNAL_DIAGNOSTICS_TOKEN", "y" * 40)
    attach_latency_control(app, db, "synthetic")
    await evidence._control.poll_once()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        for headers in ({}, {"X-Mezan-Diagnostics-Token": "y" * 40},
                        {"X-Mezan-Latency-Control-Token": "y" * 40}):
            assert (await client.get("/api/internal/latency-evidence", headers=headers)).status_code == 403
        headers = {"X-Mezan-Latency-Control-Token": "x" * 40}
        response = await client.get("/api/internal/latency-evidence", headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        body = command(0).model_dump(mode="json")
        assert (await client.post("/api/internal/latency-evidence/start", headers=headers, json=body)).status_code == 409
        body["duration_seconds"] = 901
        assert (await client.post("/api/internal/latency-evidence/start", headers=headers, json=body)).status_code == 422
        assert "x" * 40 not in json.dumps(await db[COLLECTION].find_one({}))


@pytest.mark.asyncio
async def test_api_web_mobile_same_result_privacy_rate_and_disable(caplog, monkeypatch):
    app = FastAPI()
    app.add_middleware(LatencyEvidenceMiddleware)
    @app.get("/api/shipping/{order_number}")
    async def sample(order_number: str):
        return {"ok": True}
    monkeypatch.setattr(evidence, "_enabled", True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        for user_agent in ("synthetic-web", "synthetic-android"):
            for _ in range(10):
                response = await client.get("/api/shipping/private-order?token=private-query",
                                            headers={"User-Agent": user_agent, "Authorization": "private-token"})
                assert response.json() == {"ok": True}
        assert caplog.text.count("api_sample") == 1
        assert "private" not in caplog.text and "synthetic-android" not in caplog.text
        evidence._enabled = False
        before = caplog.text
        assert (await client.get("/api/shipping/another-order")).status_code == 200
        assert caplog.text == before


@pytest.mark.asyncio
async def test_real_loop_stall_expires_control_and_health_recovers(db, caplog):
    import time
    import runtime_diagnostics as runtime
    a, b = await workers(db)
    await a.change(command(0, duration=1), True)
    await a.poll_once()
    evidence.bind_control(a)
    task = asyncio.create_task(runtime._lag_monitor())
    try:
        await asyncio.sleep(.05)
        time.sleep(1.15)  # isolated process only
        await asyncio.sleep(.05)
        assert not a.allowed()
        assert "event_loop_lag" not in caplog.text  # expired permit cannot leak a late sample
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_full_900_second_deadline_and_control_staleness(db):
    a, b = await workers(db)
    now = a.clock()
    mono = [100.0]
    a.clock = lambda: now
    a.monotonic = lambda: mono[0]
    await a.change(command(0), True)
    await a.poll_once()
    assert a.deadline == 1000.0
    mono[0] = 109.9
    assert a.allowed()
    mono[0] = 110.0
    assert not a.allowed()  # no fresh control: fail closed before session TTL
    for seconds in range(5, 900, 5):
        a.clock = lambda seconds=seconds: now + seconds
        mono[0] = 100.0 + seconds
        await a.poll_once()
        assert a.allowed()
        assert a.deadline == 1000.0
    mono[0] = 1000.0
    assert not a.allowed()


@pytest.mark.asyncio
async def test_wrong_session_stop_cannot_stop_new_session(db):
    a, b = await workers(db)
    await a.change(command(0), True)
    with pytest.raises(HTTPException, match="session_mismatch"):
        await b.change(command(1), False)
    assert (await a.document())["enabled"]


def test_invalid_shared_config_cannot_fall_back_to_file(monkeypatch, tmp_path):
    path = tmp_path / "permit"
    path.touch(mode=0o600)
    monkeypatch.setattr(evidence, "_configured", True)
    monkeypatch.setattr(evidence, "_permit", str(path))
    monkeypatch.setenv("LATENCY_EVIDENCE_CONTROL_ENABLED", "1")
    evidence.refresh()
    assert not evidence.enabled()


@pytest.mark.asyncio
async def test_io_timeout_never_escapes_into_api(db, monkeypatch):
    import latency_control
    a, b = await workers(db)
    await a.change(command(0), True)
    await a.poll_once()
    async def hung(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(latency_control, "IO_TIMEOUT", .01)
    monkeypatch.setattr(a.collection, "find_one", hung)
    await a.poll_once()
    assert not a.allowed() and a.reason == "control_unavailable"
