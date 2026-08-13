import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center import snapchat_campaign_preview_async as preview


class Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class Collection:
    def __init__(self):
        self.rows = []
        self.create_index_calls = []

    async def create_index(self, *args, **kwargs):
        self.create_index_calls.append((args, kwargs))
        return kwargs.get("name")

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                result = deepcopy(row)
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                if projection and any(value == 1 for value in projection.values()):
                    return {
                        key: value
                        for key, value in result.items()
                        if key in projection and projection[key] == 1
                    }
                return result
        return None

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set") or {}))
                return Result(1)
        return Result(0)


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


def campaign_create(*, idempotency_key="async-preview-001", name="Safe"):
    return preview.SnapchatManagementProposalInput(
        action="campaign.create",
        account_id="account-1",
        payload={
            "name": name,
            "start_time": "2026-08-13T00:00:00Z",
            "objective_v2_properties": {"objective_v2_type": "SALES"},
            "daily_budget_micro": 40_000_000,
        },
        reason="تجهيز معاينة آمنة في الخلفية",
        idempotency_key=idempotency_key,
    )


@pytest.mark.asyncio
async def test_start_route_returns_202_before_worker_completion_and_replay_is_single(
    monkeypatch,
):
    db = DB()
    started = asyncio.Event()
    release = asyncio.Event()
    executions = []

    async def blocked_worker(db_arg, user_id, actor_id, preview_job_id):
        executions.append((user_id, actor_id, preview_job_id))
        started.set()
        await release.wait()

    monkeypatch.setattr(
        preview, "execute_snapchat_management_preview_job", blocked_worker
    )
    router = APIRouter()

    async def current_user():
        return {"id": "owner-1"}

    preview.attach_snapchat_campaign_preview_async_routes(
        router, db, current_user, lambda user: user
    )
    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    payload = campaign_create().model_dump(mode="json")
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = await asyncio.wait_for(
                client.post("/snapchat_ads/management/preview-jobs", json=payload),
                timeout=1,
            )
            await asyncio.wait_for(started.wait(), timeout=1)

            assert first.status_code == 202
            assert first.json()["status"] == "queued"
            assert first.json()["provider_write_reached"] is False
            assert release.is_set() is False
            assert len(preview._PREVIEW_WORKER_TASKS) == 1

            # A retry after a lost 202 uses the same durable job.  It may ask
            # the scheduler to recover a queued job, but cannot create a
            # second local worker or a second database row.
            replay = await asyncio.wait_for(
                client.post("/snapchat_ads/management/preview-jobs", json=payload),
                timeout=1,
            )
            assert replay.status_code == 202
            assert replay.json()["preview_job_id"] == first.json()["preview_job_id"]
            assert len(preview._PREVIEW_WORKER_TASKS) == 1
            assert len(executions) == 1
            assert len(db[preview.PREVIEW_JOB_COLLECTION].rows) == 1
            assert all(
                collection.create_index_calls == []
                for collection in db.collections.values()
            )
    finally:
        release.set()
        tasks = list(preview._PREVIEW_WORKER_TASKS.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)

    assert preview._PREVIEW_WORKER_TASKS == {}


@pytest.mark.asyncio
async def test_replay_reschedules_an_orphan_queued_job_without_a_second_row(
    monkeypatch,
):
    db = DB()
    payload = campaign_create(idempotency_key="orphan-queued-preview-1")
    orphan, created = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", payload
    )
    assert created is True
    assert preview._PREVIEW_WORKER_TASKS == {}

    executed = asyncio.Event()
    calls = []

    async def recovered_worker(db_arg, user_id, actor_id, preview_job_id):
        calls.append((user_id, actor_id, preview_job_id))
        executed.set()

    monkeypatch.setattr(
        preview, "execute_snapchat_management_preview_job", recovered_worker
    )
    router = APIRouter()
    preview.attach_snapchat_campaign_preview_async_routes(
        router, db, lambda: {"id": "owner-1"}, lambda user: user
    )
    route = next(
        item for item in router.routes if item.path.endswith("/management/preview-jobs")
    )
    replay = await route.endpoint(payload=payload, user={"id": "owner-1"})
    await asyncio.wait_for(executed.wait(), timeout=1)
    tasks = list(preview._PREVIEW_WORKER_TASKS.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)

    assert replay["preview_job_id"] == orphan["preview_job_id"]
    assert calls == [
        ("owner-1", "owner-1", orphan["preview_job_id"]),
    ]
    assert len(db[preview.PREVIEW_JOB_COLLECTION].rows) == 1
    assert preview._PREVIEW_WORKER_TASKS == {}


@pytest.mark.asyncio
async def test_queue_is_idempotent_and_conflicting_payload_is_rejected():
    db = DB()
    first, created = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create()
    )
    replay, replay_created = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create()
    )
    assert created is True
    assert replay_created is False
    assert replay["preview_job_id"] == first["preview_job_id"]
    assert len(db[preview.PREVIEW_JOB_COLLECTION].rows) == 1

    with pytest.raises(HTTPException) as conflict:
        await preview.queue_snapchat_management_preview_job(
            db, "owner-1", "owner-1", campaign_create(name="Different")
        )
    assert conflict.value.detail["code"] == "snapchat_management_idempotency_conflict"


@pytest.mark.asyncio
async def test_worker_prepares_existing_governed_proposal_without_provider_write(
    monkeypatch,
):
    db = DB()
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create()
    )
    called = []

    async def create_proposal(db_arg, user_id, actor_id, payload):
        called.append(payload.idempotency_key)
        db_arg[preview.PROPOSAL_COLLECTION].rows.append(
            {
                "user_id": user_id,
                "proposal_id": "proposal-1",
                "idempotency_key": payload.idempotency_key,
                "request_fingerprint": preview.snapchat_management_request_fingerprint(
                    payload
                ),
                "status": "previewed",
                "provider_write_reached": False,
            }
        )
        return {"proposal_id": "proposal-1", "status": "previewed"}

    monkeypatch.setattr(preview, "create_snapchat_management_proposal", create_proposal)
    await preview.execute_snapchat_management_preview_job(
        db, "owner-1", "owner-1", job["preview_job_id"]
    )
    result = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"]
    )
    assert called == ["async-preview-001"]
    assert result["status"] == "ready"
    assert result["proposal_id"] == "proposal-1"
    assert result["provider_write_reached"] is False
    assert "confirm_token" not in db[preview.PREVIEW_JOB_COLLECTION].rows[0]


@pytest.mark.asyncio
async def test_worker_failure_is_terminal_known_no_write_without_a_proposal(
    monkeypatch,
):
    db = DB()
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create()
    )

    async def fail_before_proposal(*args, **kwargs):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "snapchat_needs_reauth",
                "message": "أعد ربط Snapchat.",
                "retryable": True,
            },
        )

    monkeypatch.setattr(
        preview, "create_snapchat_management_proposal", fail_before_proposal
    )
    await preview.execute_snapchat_management_preview_job(
        db, "owner-1", "owner-1", job["preview_job_id"]
    )
    result = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"]
    )
    assert result["status"] == "failed"
    assert result["failure"] == {
        "code": "snapchat_needs_reauth",
        "message": "أعد ربط Snapchat.",
        "retryable": True,
    }
    assert result["proposal_id"] is None
    assert result["provider_write_reached"] is False
    assert result["provider_write_state"] == "not_attempted"
    assert result["provider_write_uncertain"] is False
    assert result["terminal_reconciled"] is True
    assert db[preview.PROPOSAL_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_worker_timeout_is_bounded_terminal_and_known_no_write(monkeypatch):
    db = DB()
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create(idempotency_key="timeout-001")
    )
    entered = asyncio.Event()

    async def never_finishes(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(preview, "PREVIEW_JOB_EXECUTION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(preview, "create_snapchat_management_proposal", never_finishes)
    await preview.execute_snapchat_management_preview_job(
        db, "owner-1", "owner-1", job["preview_job_id"]
    )
    assert entered.is_set() is True
    result = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"]
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "snapchat_management_preview_worker_timeout"
    assert result["terminal_reconciled"] is True
    assert result["provider_write_reached"] is False
    assert db[preview.PROPOSAL_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_post_claim_read_failure_is_caught_and_terminalized(monkeypatch):
    db = DB()
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create(idempotency_key="find-fail-001")
    )
    jobs = db[preview.PREVIEW_JOB_COLLECTION]
    original_find = jobs.find_one

    async def fail_claimed_read(query, projection=None):
        if query.get("preview_job_id") == job["preview_job_id"]:
            raise RuntimeError("simulated_post_claim_read_failure")
        return await original_find(query, projection)

    monkeypatch.setattr(jobs, "find_one", fail_claimed_read)
    await preview.execute_snapchat_management_preview_job(
        db, "owner-1", "owner-1", job["preview_job_id"]
    )
    monkeypatch.setattr(jobs, "find_one", original_find)
    result = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"]
    )
    assert result["status"] == "failed"
    assert result["failure"]["error_type"] == "RuntimeError"
    assert result["terminal_reconciled"] is True
    assert result["provider_write_reached"] is False


@pytest.mark.asyncio
async def test_orphan_running_job_reconciles_from_durable_proposal():
    db = DB()
    payload = campaign_create()
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", payload
    )
    db[preview.PREVIEW_JOB_COLLECTION].rows[0]["status"] = "running"
    db[preview.PROPOSAL_COLLECTION].rows.append(
        {
            "user_id": "owner-1",
            "proposal_id": "proposal-orphan",
            "idempotency_key": payload.idempotency_key,
            "request_fingerprint": preview.snapchat_management_request_fingerprint(
                payload
            ),
            "status": "previewed",
        }
    )
    result = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"]
    )
    assert result["status"] == "ready"
    assert result["proposal_id"] == "proposal-orphan"
    assert result["failure"] is None
    assert result["terminal_reconciled"] is True


@pytest.mark.asyncio
async def test_current_lookup_is_exact_tenant_scoped_and_does_not_leak_request():
    db = DB()
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create(idempotency_key="current-001")
    )
    current = await preview.get_current_snapchat_management_preview_job(
        db, "owner-1", "current-001"
    )
    assert current["preview_job_id"] == job["preview_job_id"]
    assert current["status"] == "queued"
    assert current["phase"] == "queued"
    assert current["terminal_reconciled"] is False
    for forbidden in (
        "request",
        "request_fingerprint",
        "idempotency_key",
        "actor_id",
        "confirm_token",
    ):
        assert forbidden not in current

    with pytest.raises(HTTPException) as other_owner:
        await preview.get_current_snapchat_management_preview_job(
            db, "owner-2", "current-001"
        )
    assert other_owner.value.status_code == 404


@pytest.mark.asyncio
async def test_current_route_is_not_captured_as_a_dynamic_job_id():
    db = DB()
    payload = campaign_create(idempotency_key="current-route-001")
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", payload
    )
    router = APIRouter()

    async def current_user():
        return {"id": "owner-1"}

    preview.attach_snapchat_campaign_preview_async_routes(
        router, db, current_user, lambda user: user
    )
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/snapchat_ads/management/preview-jobs/current",
            params={"idempotency_key": payload.idempotency_key},
        )
    assert response.status_code == 200
    assert response.json()["preview_job_id"] == job["preview_job_id"]


@pytest.mark.asyncio
async def test_tenant_cannot_read_or_reconcile_another_owners_job():
    db = DB()
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create()
    )
    db[preview.PROPOSAL_COLLECTION].rows.append(
        {
            "user_id": "owner-2",
            "proposal_id": "proposal-other",
            "idempotency_key": "async-preview-001",
            "request_fingerprint": db[preview.PREVIEW_JOB_COLLECTION].rows[0][
                "request_fingerprint"
            ],
        }
    )
    with pytest.raises(HTTPException) as missing:
        await preview.get_snapchat_management_preview_job(
            db, "owner-2", job["preview_job_id"]
        )
    assert missing.value.status_code == 404
    own = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"]
    )
    assert own["status"] == "queued"
    assert own["proposal_id"] is None


@pytest.mark.asyncio
async def test_stale_job_fails_known_no_write_and_never_auto_retries():
    db = DB()
    observed_at = datetime(2026, 8, 13, 3, 0, tzinfo=timezone.utc)
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create()
    )
    row = db[preview.PREVIEW_JOB_COLLECTION].rows[0]
    row["status"] = "running"
    row["stale_at"] = (observed_at - timedelta(minutes=1)).isoformat()
    result = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"], now=lambda: observed_at
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "snapchat_management_preview_job_stale"
    assert result["provider_write_state"] == "not_attempted"
    assert result["provider_write_uncertain"] is False
    assert result["terminal_reconciled"] is False
    assert result["recovery_action"] == "continue_read_only_reconciliation"
    assert result["failure"]["message"] == (
        "تجاوزت المعاينة مهلة المتابعة، وما زال التحقق من نتيجتها جاريًا؛ "
        "استمر بمتابعة نفس المعاينة ولا تنشئ أخرى الآن."
    )
    assert result["reconcile_deadline_at"] == (
        observed_at + preview.PREVIEW_JOB_RECONCILIATION_GRACE
    ).isoformat()
    assert row["automatic_retry_allowed"] is False

    db[preview.PROPOSAL_COLLECTION].rows.append(
        {
            "user_id": "owner-1",
            "proposal_id": "proposal-after-stale",
            "idempotency_key": row["idempotency_key"],
            "request_fingerprint": row["request_fingerprint"],
            "status": "previewed",
        }
    )
    reconciled = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"]
    )
    assert reconciled["status"] == "ready"
    assert reconciled["proposal_id"] == "proposal-after-stale"
    assert reconciled["terminal_reconciled"] is True
    assert reconciled["recovery_action"] is None


@pytest.mark.asyncio
async def test_stale_job_without_late_proposal_converges_to_terminal_failure():
    db = DB()
    observed_at = datetime(2026, 8, 13, 3, 0, tzinfo=timezone.utc)
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create(idempotency_key="stale-final-001")
    )
    row = db[preview.PREVIEW_JOB_COLLECTION].rows[0]
    row["status"] = "running"
    row["stale_at"] = (observed_at - timedelta(seconds=1)).isoformat()

    pending = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"], now=lambda: observed_at
    )
    assert pending["status"] == "failed"
    assert pending["terminal_reconciled"] is False
    assert pending["recovery_action"] == "continue_read_only_reconciliation"

    terminal_at = (
        observed_at + preview.PREVIEW_JOB_RECONCILIATION_GRACE + timedelta(seconds=1)
    )
    terminal = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"], now=lambda: terminal_at
    )
    assert terminal["status"] == "failed"
    assert terminal["terminal_reconciled"] is True
    assert terminal["recovery_action"] == "create_new_preview"
    assert terminal["failure"] == {
        "code": "snapchat_management_preview_job_stale",
        "message": (
            "لم تكتمل المعاينة بعد انتهاء مهلة التحقق؛ يمكنك إنشاء معاينة جديدة."
        ),
        "retryable": False,
    }
    assert terminal["provider_write_reached"] is False


def test_http_failure_preserves_only_bounded_retryability():
    failure = preview._safe_failure(
        HTTPException(
            status_code=503,
            detail={
                "code": "snapchat_needs_reauth",
                "message": "renew",
                "retryable": True,
                "access_token": "must-not-leak",
            },
        )
    )
    assert failure == {
        "code": "snapchat_needs_reauth",
        "message": "renew",
        "retryable": True,
    }
