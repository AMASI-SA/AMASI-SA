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
    assert db[preview.PROPOSAL_COLLECTION].rows == []


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
    job, _ = await preview.queue_snapchat_management_preview_job(
        db, "owner-1", "owner-1", campaign_create()
    )
    row = db[preview.PREVIEW_JOB_COLLECTION].rows[0]
    row["status"] = "running"
    row["stale_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    result = await preview.get_snapchat_management_preview_job(
        db, "owner-1", job["preview_job_id"]
    )
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "snapchat_management_preview_job_stale"
    assert result["provider_write_state"] == "not_attempted"
    assert result["provider_write_uncertain"] is False
    assert row["automatic_retry_allowed"] is False


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
