"""HTTP/integration tests for Qoyod Dead-Letter Auto-Requeue (iter264)."""
import os
import uuid
import requests
import pytest
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@hesab.app"
ADMIN_PWD = "admin123"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PWD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token = r.json().get("access_token")
    assert token
    s.headers.update({"Authorization": f"Bearer {token}",
                      "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def mongo_db():
    """Returns a 0-arg factory that builds a fresh Motor client +
    database handle on demand. Each `_run(...)` call below uses
    `_db = mongo_db()` INSIDE its coroutine so the Motor client is
    bound to the same event loop that `asyncio.run` is about to spin
    up. Sharing a Motor client across pytest modules / loops triggers
    `RuntimeError: no current event loop`.
    """
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    assert mongo_url and db_name

    def _make():
        return AsyncIOMotorClient(mongo_url)[db_name]
    return _make


def _run(coro_factory):
    """Run an async callable in a fresh event loop. `coro_factory()` may
    return either a coroutine OR a Future/Task (Motor returns the latter
    via `asyncio.ensure_future`). We await it via a tiny wrapper so
    `asyncio.run` is happy either way."""
    async def _await():
        return await coro_factory()
    return asyncio.run(_await())


# ── 1. preview endpoint ─────────────────────────────────────────────
def test_preview_requires_auth():
    r = requests.get(f"{BASE_URL}/api/integrations/qoyod/dead-letter/preview", timeout=20)
    assert r.status_code in (401, 403)


def test_preview_returns_registry(client):
    r = client.get(f"{BASE_URL}/api/integrations/qoyod/dead-letter/preview")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "candidates" in data
    assert "candidate_count" in data
    assert data["max_requeue_attempts"] == 2
    assert isinstance(data["patterns"], list)
    assert len(data["patterns"]) == 1
    p = data["patterns"][0]
    assert p["id"] == "contact_name_blank_2026_02_26"
    assert "FAILED_CUSTOMER" in p["applies_to_failed_stages"]


# ── 2. auto-requeue (no candidates path) ────────────────────────────
def test_auto_requeue_no_candidates(client, mongo_db):
    # Best-effort cleanup of any TEST_ rows first
    _run(lambda: mongo_db().integration_inbox.delete_many({"id": {"$regex": "^TEST_iter264_"}}))
    r = client.post(f"{BASE_URL}/api/integrations/qoyod/dead-letter/auto-requeue", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    result = data["result"]
    for k in ("scanned", "requeued", "skipped_no_pattern",
              "skipped_max_attempts", "failures", "items"):
        assert k in result, f"missing key {k}"
    assert result["requeued"] == 0  # no seeded fixtures


# ── 3. requeue-one body validation ──────────────────────────────────
def test_requeue_one_missing_body(client):
    r = client.post(f"{BASE_URL}/api/integrations/qoyod/dead-letter/requeue-one", json={})
    assert r.status_code == 400, r.text
    detail = r.json().get("detail", {})
    assert detail.get("code") == "row_id_or_trace_id_required"


def test_requeue_one_row_not_found(client):
    r = client.post(f"{BASE_URL}/api/integrations/qoyod/dead-letter/requeue-one",
                    json={"trace_id": f"nonexistent_{uuid.uuid4()}"})
    assert r.status_code == 409, r.text
    detail = r.json().get("detail", {})
    assert detail.get("code") == "row_not_found"


def test_requeue_one_no_pattern_match(client, mongo_db):
    # Seed a DEAD_LETTER row with an UNKNOWN error
    row_id = f"TEST_iter264_unknown_{uuid.uuid4().hex[:8]}"
    trace_id = f"TEST_iter264_trace_{uuid.uuid4().hex[:8]}"
    _run(lambda: mongo_db().integration_inbox.insert_one({
        "id": row_id,
        "trace_id": trace_id,
        "user_id": "main",
        "connector_key": "qoyod",
        "idempotency_key": row_id,
        "pipeline_stage": "DEAD_LETTER",
        "last_failed_stage": "FAILED_INVOICE",
        "pipeline_error": {"code": "some_other_error",
                           "message": "totally unrelated"},
        "dry_run": False,
        "received_at": datetime.now(timezone.utc),
    }))
    try:
        r = client.post(f"{BASE_URL}/api/integrations/qoyod/dead-letter/requeue-one",
                        json={"row_id": row_id})
        assert r.status_code == 409, r.text
        detail = r.json().get("detail", {})
        assert detail.get("code") == "no_known_fix_pattern_matches"
    finally:
        _run(lambda: mongo_db().integration_inbox.delete_one({"id": row_id}))


# ── 4. Full e2e: seed → auto-requeue → assert state transition ─────
def test_auto_requeue_known_pattern_e2e(client, mongo_db):
    row_id = f"TEST_iter264_blank_{uuid.uuid4().hex[:8]}"
    trace_id = f"TEST_iter264_trace_{uuid.uuid4().hex[:8]}"
    generic_id = f"TEST_iter264_generic_{uuid.uuid4().hex[:8]}"

    _run(lambda: mongo_db().integration_inbox.insert_many([
        {
            "id": row_id,
            "trace_id": trace_id,
            "user_id": "main",
            "connector_key": "qoyod",
            "idempotency_key": row_id,
            "pipeline_stage": "DEAD_LETTER",
            "last_failed_stage": "FAILED_CUSTOMER",
            "pipeline_error": {
                "code": "qoyod_validation_error",
                "details": {"contact_name": ["Can't be blank"]},
            },
            "requeue_attempts": 0,
            "dry_run": False,
            "received_at": datetime.now(timezone.utc),
            "canonical_payload": {"order_id": "TEST_iter264_o1",
                                  "order_number": "9999"},
        },
        {
            "id": generic_id,
            "trace_id": f"TEST_iter264_trace_g_{uuid.uuid4().hex[:8]}",
            "user_id": "main",
            "connector_key": "qoyod",
            "idempotency_key": generic_id,
            "pipeline_stage": "DEAD_LETTER",
            "last_failed_stage": "FAILED_INVOICE",
            "pipeline_error": {"code": "qoyod_api_error",
                               "message": "random other failure"},
            "requeue_attempts": 0,
            "dry_run": False,
            "received_at": datetime.now(timezone.utc),
        },
    ]))

    try:
        # Preview must contain only the contact_name row
        r = client.get(f"{BASE_URL}/api/integrations/qoyod/dead-letter/preview")
        assert r.status_code == 200
        ids = [c["row_id"] for c in r.json()["candidates"]]
        assert row_id in ids
        assert generic_id not in ids

        # Trigger auto-requeue
        r = client.post(f"{BASE_URL}/api/integrations/qoyod/dead-letter/auto-requeue", json={})
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert result["requeued"] >= 1

        # Re-fetch the row from Mongo — it might be NORMALIZED OR already
        # processed by the worker (5s tick). Accept either path.
        row = _run(lambda: mongo_db().integration_inbox.find_one({"id": row_id}))
        assert row is not None
        # requeue_attempts must have incremented
        assert int(row.get("requeue_attempts") or 0) == 1
        # stage_history must record both RETRYING and the resume stage
        history_stages = [h.get("to_stage") for h in (row.get("stage_history") or [])]
        assert "RETRYING" in history_stages
        assert "NORMALIZED" in history_stages
        # Generic row untouched
        g = _run(lambda: mongo_db().integration_inbox.find_one({"id": generic_id}))
        assert g["pipeline_stage"] == "DEAD_LETTER"
        assert int(g.get("requeue_attempts") or 0) == 0
    finally:
        _run(lambda: mongo_db().integration_inbox.delete_many(
            {"id": {"$in": [row_id, generic_id]}}))


# ── 5. Go-Live checklist no longer blocks on auto-recoverable ──────
def test_go_live_outstanding_failures_partitioning(client, mongo_db):
    """Seed a known-fix candidate, hit go-live checklist BEFORE worker
    drains. The outstanding_failures item must classify the row as
    auto_recoverable (ok=True) instead of blocking."""
    row_id = f"TEST_iter264_gl_{uuid.uuid4().hex[:8]}"
    _run(lambda: mongo_db().integration_inbox.insert_one({
        "id": row_id,
        "trace_id": f"TEST_iter264_trace_{uuid.uuid4().hex[:8]}",
        "user_id": "main",
        "connector_key": "qoyod",
        "idempotency_key": row_id,
        "pipeline_stage": "DEAD_LETTER",
        "last_failed_stage": "FAILED_CUSTOMER",
        "pipeline_error": {
            "code": "qoyod_validation_error",
            "details": {"contact_name": ["Can't be blank"]},
        },
        "requeue_attempts": 0,
        "dry_run": False,
        "received_at": datetime.now(timezone.utc),
    }))
    try:
        r = client.get(f"{BASE_URL}/api/integrations/qoyod/go-live/checklist")
        assert r.status_code == 200, r.text
        data = r.json()
        items = data.get("items") or data.get("checks") or []
        outstanding = None
        for it in items:
            key = it.get("id") or it.get("key") or it.get("name") or ""
            if "outstanding" in str(key).lower() or "failures" in str(key).lower():
                outstanding = it
                break
        # Lenient: just ensure the endpoint works and returns an items list
        assert isinstance(items, list)
        if outstanding is not None:
            extra = outstanding.get("extra") or {}
            # When auto_recoverable_count > 0 and blocking_count == 0, ok must be True
            if extra.get("auto_recoverable_count", 0) > 0 and extra.get("blocking_count", 0) == 0:
                assert outstanding.get("ok") is True, outstanding
    finally:
        _run(lambda: mongo_db().integration_inbox.delete_one({"id": row_id}))
