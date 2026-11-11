import asyncio
from datetime import datetime, timedelta, timezone
import sys
from unittest.mock import AsyncMock

from fastapi import FastAPI, APIRouter, HTTPException
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
import pytest

import campaign_ai_shadow_jobs as jobs
from campaign_ai_unified_shadow_routes import attach_campaign_ai_unified_shadow_routes


def app_for(db, user):
    app, router = FastAPI(), APIRouter()
    def require_owner(actor):
        if actor.get("role") != "owner":
            raise HTTPException(403, "owner required")
        return actor
    attach_campaign_ai_unified_shadow_routes(router, db, lambda: user, require_owner)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_http_reads_do_not_enqueue_or_compute_and_scope_cache_by_owner():
    db = AsyncMongoMockClient().db
    clock = datetime.now(timezone.utc)
    await db[jobs.COLLECTION].insert_one({"_id": jobs._key("other", 1), "user_id": "other",
        "status": "complete", "result": {"private": "other-owner"}})
    app = app_for(db, {"id": "owner", "role": "owner"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(4):
            response = await client.get("/ai-monitor/unified-shadow")
            assert response.status_code == 200
            assert response.json()["result"] is None
            assert response.json()["job"]["status"] == "idle"
        assert await db[jobs.COLLECTION].count_documents({}) == 1
        queued = await client.post("/ai-monitor/unified-shadow/refresh")
        assert queued.status_code == 202
        assert queued.json()["job"]["status"] == "queued"
        same = await client.post("/ai-monitor/unified-shadow/refresh")
        assert same.json()["job"]["request_id"] == queued.json()["job"]["request_id"]
        assert await db[jobs.COLLECTION].count_documents({"user_id": "owner"}) == 1
        for days in (0, 15):
            assert (await client.post(f"/ai-monitor/unified-shadow/refresh?days={days}")).status_code == 422
        different = await client.post("/ai-monitor/unified-shadow/refresh?days=14")
        manual = await client.post("/ai-monitor/manual/refresh")
        assert manual.status_code == 202
        for response in (different, manual):
            assert response.json()["job"]["request_id"] == queued.json()["job"]["request_id"]
            assert response.json()["request_accepted"] is False
            assert response.json()["request_blocked_by"] == "active_job"
        assert await db[jobs.COLLECTION].count_documents({"user_id": "owner"}) == 1
    owner_row = await db[jobs.COLLECTION].find_one({"user_id": "owner"})
    assert owner_row["requested_at"].replace(tzinfo=timezone.utc) >= clock


@pytest.mark.asyncio
async def test_owner_permission_precedes_job_reads_and_writes():
    class ForbiddenDB:
        def __getitem__(self, _):
            pytest.fail("unauthorized caller touched the queue")
    app = app_for(ForbiddenDB(), {"id": "staff", "role": "employee"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/ai-monitor/unified-shadow")).status_code == 403
        assert (await client.post("/ai-monitor/unified-shadow/refresh")).status_code == 403
        assert (await client.get("/ai-monitor/manual")).status_code == 403
        assert (await client.post("/ai-monitor/manual/refresh")).status_code == 403


def test_stale_cached_result_survives_failure_without_internal_tokens():
    now = datetime.now(timezone.utc)
    response = jobs._public({"kind": "comparison", "status": "failed", "cached_results": {"comparison": {
        "result": {"shadow_passed": True}, "days": 1,
        "generated_at": now - timedelta(hours=3), "expires_at": now - timedelta(hours=1)}},
        "lease_token": "private-token", "user_id": "private-owner", "error_code": "comparison_timeout"}, now=now)
    assert response["stale"] is True
    assert response["result"] == {"shadow_passed": True}
    assert response["job"]["error_code"] == "comparison_timeout"
    assert "private-" not in repr(response)


@pytest.mark.asyncio
async def test_actual_manual_analysis_is_explicit_and_uses_fixed_three_day_scope():
    db = AsyncMongoMockClient().db
    app = app_for(db, {"id": "owner", "role": "owner"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/ai-monitor/manual")).json()["job"]["status"] == "idle"
        assert await db[jobs.COLLECTION].count_documents({}) == 0
        queued = (await client.post("/ai-monitor/manual/refresh")).json()
        assert queued["request_accepted"] is True
        assert queued["job"]["kind"] == "monitor"
        assert queued["job"]["days"] == 3
        assert queued["analysis_may_call_openai"] is True
        assert queued["provider_writes_performed"] is False


@pytest.mark.asyncio
async def test_manual_builder_disables_sync_and_ad_execution_and_uses_fenced_publication(monkeypatch):
    import campaign_ai_monitor as monitor
    import mezan_campaign_profit_loader as profits
    analyze = AsyncMock(return_value={"snapshot_id": "s", "execution_targets": {"private": True}})
    monkeypatch.setattr(monitor, "ensure_campaign_ai_indexes", AsyncMock())
    monkeypatch.setattr(monitor, "run_campaign_ai_monitor", analyze)
    monkeypatch.setattr(profits, "make_mezan_campaign_profit_loader", lambda db: "existing-profit-loader")
    result = await jobs._build_monitor(object(), {"user_id": "owner"})
    assert result == {"snapshot_id": "s"}
    args = analyze.await_args.kwargs
    assert args["refresh_meta"] is False
    assert args["recommendation_only"] is True
    assert args["business_context_loader"] == "existing-profit-loader"
    assert callable(args["snapshot_publisher"])


@pytest.mark.asyncio
async def test_manual_monitor_never_dispatches_autonomy_or_spend_guard(monkeypatch):
    import campaign_ai_monitor as monitor
    import campaign_ai_google_periodic as google
    import campaign_ai_tiktok_periodic as tiktok
    import campaign_ai_snapchat_autonomy as snap
    import integrations_control_center.tiktok_spend_guard as guard
    db = AsyncMongoMockClient().db
    snapshot = {"snapshot_id": "s", "recommendations": [], "writes_performed": False}
    await db[monitor.RECOMMENDATION_COLLECTION].insert_one({**snapshot, "user_id": "owner"})
    monkeypatch.setattr(monitor, "_base_run_campaign_ai_monitor", AsyncMock(return_value=snapshot))
    async def append(_db, _user, document, **kwargs):
        return document
    monkeypatch.setattr(google, "append_google_periodic_recommendations", append)
    monkeypatch.setattr(tiktok, "append_tiktok_periodic_recommendations", append)
    autonomy = AsyncMock(side_effect=AssertionError("manual monitoring cannot execute advertising"))
    spend_guard = AsyncMock(side_effect=AssertionError("manual monitoring cannot pause advertising"))
    monkeypatch.setattr(snap, "dispatch_snapchat_autonomy", autonomy)
    monkeypatch.setattr(guard, "run_spend_guard", spend_guard)
    result = await monitor.run_campaign_ai_monitor(db, "owner", refresh_meta=False, recommendation_only=True)
    autonomy.assert_not_awaited()
    spend_guard.assert_not_awaited()
    assert result["trigger"] == "manual"
    assert result["next_run_at"] is None
    assert result["automatic_execution"] is False
    stored = await db[monitor.RECOMMENDATION_COLLECTION].find_one({"snapshot_id": "s"})
    assert stored["manual_job_status"] == "complete"
    assert stored["writes_performed"] is False


@pytest.mark.asyncio
async def test_cpu_child_does_not_block_supervisor_and_cancellation_joins_it(monkeypatch, tmp_path):
    path = tmp_path / "busy.py"
    path.write_text("while True:\n sum(range(50000))\n")
    processes = []
    async def launch(*args, **kwargs):
        process = await asyncio.create_subprocess_exec(sys.executable, str(path), **kwargs)
        processes.append(process)
        return process
    renew = AsyncMock()
    monkeypatch.setattr(jobs, "renew_claim", renew)
    monkeypatch.setattr(jobs, "_release_runner", AsyncMock())
    monkeypatch.setattr(jobs, "_fail", AsyncMock())
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", .02)
    task = asyncio.create_task(jobs.run_claim(object(), {"_id": "job", "lease_token": "token"}, launcher=launch))
    try:
        async with asyncio.timeout(2):
            while not processes or renew.await_count < 2:
                await asyncio.sleep(.005)
        assert processes[0].returncode is None
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert processes[0].returncode is not None
    jobs._fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_loss_stops_subprocess_before_releasing_runner(monkeypatch, tmp_path):
    path = tmp_path / "busy.py"
    path.write_text("while True:\n sum(range(50000))\n")
    processes = []
    async def launch(*args, **kwargs):
        process = await asyncio.create_subprocess_exec(sys.executable, str(path), **kwargs)
        processes.append(process)
        return process
    async def release(*args):
        assert processes[0].returncode is not None
    monkeypatch.setattr(jobs, "renew_claim", AsyncMock(side_effect=RuntimeError("lease lost")))
    monkeypatch.setattr(jobs, "_release_runner", release)
    monkeypatch.setattr(jobs, "_fail", AsyncMock())
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", .02)
    await jobs.run_claim(object(), {"_id": "job", "lease_token": "token"}, launcher=launch)
    assert processes[0].returncode is not None
