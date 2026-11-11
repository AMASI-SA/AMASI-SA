"""Manual send must acknowledge a durable job before any provider work."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod_manual import routes, auto_send
from integrations.qoyod_manual import send_jobs as jobs
from integrations.qoyod_manual.send import ManualSendRefused

OWNER = {"id": "owner", "role": "owner"}
BASE = "/api/integrations/qoyod/manual"


async def make_db():
    db = AsyncMongoMockClient().db
    await db.users.insert_one(OWNER)
    await db.qoyod_settings.insert_one({"user_id": "main", "security_owner_id": "owner"})
    return db


def make_app(db, user=OWNER):
    async def current_user():
        return user
    app = FastAPI()
    app.include_router(routes.make_qoyod_manual_router(db, current_user), prefix="/api")
    return app


@pytest.mark.asyncio
async def test_send_acknowledges_before_slow_provider_and_duplicate_reuses_job(monkeypatch):
    db = await make_db()
    async def slow(*args, **kwargs):
        await asyncio.Event().wait()
    refresh = AsyncMock(side_effect=slow)
    sender = AsyncMock(side_effect=AssertionError("HTTP request must not send"))
    monkeypatch.setattr(auto_send, "_refresh_and_verify_salla_status", refresh)
    monkeypatch.setattr(routes, "manual_send_one", sender)
    async with AsyncClient(transport=ASGITransport(app=make_app(db)), base_url="http://test") as client:
        first = await asyncio.wait_for(client.post(BASE + "/send/123"), timeout=0.25)
        second = await client.post(BASE + "/send/123")
    assert first.status_code == 202
    assert first.json()["job"]["status"] == "queued"
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    refresh.assert_not_awaited()
    sender.assert_not_awaited()
    assert await db.qoyod_manual_send_jobs.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "retry"])
async def test_two_workers_execute_one_send_and_polling_reads_saved_result(operation):
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(db, row):
        entered.set()
        await release.wait()
        return {"invoice_id": "invoice-1", "payment_id": "payment-1", "private": "hidden"}
    first = asyncio.create_task(jobs.run_once(db, executor=execute))
    await entered.wait()
    assert await jobs.run_once(db, executor=execute) is False
    assert (await jobs.enqueue(db, user=OWNER, order_number="123", operation=operation,
                               request_id="another-explicit-click"))["job"]["status"] == "running"
    release.set()
    await first
    result = await jobs.read_job(db, user=OWNER, job_id=queued["job"]["id"])
    assert result["job"]["status"] == "succeeded"
    assert result["job"]["result"] == {"ok": True, "order_number": "123",
        "invoice_id": "invoice-1", "payment_id": "payment-1"}
    assert await jobs.run_once(db, executor=execute) is False


@pytest.mark.asyncio
async def test_cancelled_send_is_held_without_releasing_invoice_lock_or_replaying():
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    entered = asyncio.Event()
    async def execute(db, row):
        await db.qoyod_manual_send_locks.insert_one({"user_id": "main", "order_number": "123",
            "status": "in_progress", "manual_qoyod_invoice_id": "possible-invoice"})
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(jobs.run_once(db, executor=execute))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    again = await jobs.enqueue(db, user=OWNER, order_number="123", operation="retry", request_id="new-click")
    assert again["job"]["id"] == queued["job"]["id"]
    assert again["job"]["status"] == "needs_review"
    assert (await db.qoyod_manual_send_locks.find_one({}))["status"] == "in_progress"
    assert (await db.qoyod_manual_auto_quarantines.find_one({}))["code"] == "manual_job_outcome_unknown"
    forbidden = AsyncMock(side_effect=AssertionError("must not replay"))
    assert await jobs.run_once(db, executor=forbidden) is False
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_holds_expired_executing_job_but_can_execute_never_started_job():
    db = await make_db()
    old = await jobs.enqueue(db, user=OWNER, order_number="123")
    await db.qoyod_manual_send_jobs.update_one({"_id": old["job"]["id"]}, {"$set": {
        "status": "running", "claim_token": "dead-process", "lease_until": jobs.utcnow() - timedelta(seconds=1)}})
    await jobs.enqueue(db, user=OWNER, order_number="456")
    execute = AsyncMock(return_value={"invoice_id": "new-invoice"})
    await jobs.run_once(db, executor=execute)
    assert execute.await_count == 1
    assert execute.call_args.args[1]["order_number"] == "456"
    assert (await jobs.read_job(db, user=OWNER, job_id=old["job"]["id"]))["job"]["status"] == "needs_review"


@pytest.mark.asyncio
async def test_replaced_worker_cannot_publish_success_or_release_replacement():
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    async def execute(db, row):
        await db.qoyod_manual_send_jobs.update_one({"_id": row["_id"]},
            {"$set": {"claim_token": "replacement", "status": "needs_review"}})
        return {"invoice_id": "stale-result"}
    await jobs.run_once(db, executor=execute)
    saved = await db.qoyod_manual_send_jobs.find_one({"_id": queued["job"]["id"]})
    assert saved["claim_token"] == "replacement"
    assert saved["status"] == "needs_review"
    assert "result" not in saved


@pytest.mark.asyncio
async def test_revoked_authority_is_rechecked_before_provider_work(monkeypatch):
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
    forbidden = AsyncMock(side_effect=AssertionError("provider must not be called"))
    monkeypatch.setattr(auto_send, "_refresh_and_verify_salla_status", forbidden)
    await jobs.run_once(db)
    saved = await db.qoyod_manual_send_jobs.find_one({"_id": queued["job"]["id"]})
    assert saved["status"] == "refused"
    assert saved["error"]["code"] == "manual_send_authority_changed"
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "retry"])
async def test_only_new_explicit_request_rearms_known_refusal_and_never_success(operation):
    db = await make_db()
    await jobs.enqueue(db, user=OWNER, order_number="123", request_id="first")
    refused = AsyncMock(side_effect=ManualSendRefused("not_qoyod_eligible_status", "الحالة غير مؤهلة"))
    await jobs.run_once(db, executor=refused)
    assert (await jobs.enqueue(db, user=OWNER, order_number="123", operation=operation,
                               request_id="first"))["job"]["status"] == "refused"
    assert (await jobs.enqueue(db, user=OWNER, order_number="123", operation=operation,
                               request_id="legacy"))["job"]["status"] == "refused"
    assert (await jobs.enqueue(db, user=OWNER, order_number="123", operation=operation,
                               request_id="second"))["job"]["status"] == "queued"
    await jobs.run_once(db, executor=AsyncMock(return_value={"invoice_id": "once"}))
    assert (await jobs.enqueue(db, user=OWNER, order_number="123", operation=operation,
                               request_id="third"))["job"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_corrected_prewrite_refusal_can_be_sent_from_same_bulk_ui_path():
    from integrations.qoyod.unsent_orders import _manual_failure_evidence, simplify_row

    db = await make_db()
    path = BASE + "/send/123"
    async with AsyncClient(transport=ASGITransport(app=make_app(db)), base_url="http://test") as client:
        first = await client.post(path, headers={"Idempotency-Key": "first-click"})
        assert first.json()["job"]["status"] == "queued"
        await jobs.run_once(db, executor=AsyncMock(side_effect=ManualSendRefused(
            "payment_method_unmapped", "طريقة الدفع غير مرتبطة بحساب")))

        # This gate runs before invoice locks/quarantines are written. The
        # ordinary unsent row therefore has no separate failed-retry action.
        assert await _manual_failure_evidence(
            db, markers_user_id="main", order_numbers=["123"]) == {}
        classification = simplify_row(
            {"pipeline_stage": "NORMALIZED"}, in_qoyod_by_reference=False)
        assert classification.get("retry_allowed", False) is False
        replay = await client.post(path, headers={"Idempotency-Key": "first-click"})
        assert replay.json()["job"]["status"] == "refused"

        # After fixing the mapping, the next explicit confirmation uses a new
        # key, but still enters /send rather than the absent /retry-failed UI.
        corrected = await client.post(path, headers={"Idempotency-Key": "corrected-click"})
        assert corrected.json()["job"]["status"] == "queued"
        duplicate = await client.post(path, headers={"Idempotency-Key": "corrected-click"})
        assert duplicate.json()["job"]["id"] == corrected.json()["job"]["id"]
        execute = AsyncMock(return_value={"invoice_id": "corrected-invoice"})
        assert await jobs.run_once(db, executor=execute) is True
        assert await jobs.run_once(db, executor=execute) is False
        execute.assert_awaited_once()
        result = await client.get(BASE + "/jobs/" + corrected.json()["job"]["id"])
        assert result.json()["job"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_status_and_enqueue_refuse_unbound_or_employee_actor():
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    await db.users.insert_one({"id": "employee", "role": "operations", "created_by": "owner"})
    async with AsyncClient(transport=ASGITransport(app=make_app(db, {"id": "employee"})), base_url="http://test") as client:
        assert (await client.post(BASE + "/send/123")).status_code == 403
        denied = await client.get(BASE + "/jobs/" + queued["job"]["id"])
    assert denied.status_code == 403
    assert "invoice" not in denied.text


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "retry"])
async def test_provider_timeout_never_claims_no_invoice_or_leaks_details(operation):
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    await jobs.run_once(db, executor=AsyncMock(side_effect=RuntimeError("private-provider-payload")))
    result = await jobs.read_job(db, user=OWNER, job_id=queued["job"]["id"])
    assert result["job"]["status"] == "needs_review"
    assert "private-provider-payload" not in str(result)
    assert (await jobs.enqueue(db, user=OWNER, order_number="123", operation=operation,
                               request_id="new-click"))["job"]["status"] == "needs_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "retry"])
@pytest.mark.parametrize("code,extra", [
    ("qoyod_http_error", {"status_code": 0, "endpoint": "POST /invoices"}),
    ("invoice_created_payment_failed", {"status_code": 0, "endpoint": "POST /invoice_payments"}),
    ("invoice_id_invalid", {}),
])
async def test_translated_provider_failures_and_partial_effects_never_rearm(code, extra, operation):
    db = await make_db()
    await jobs.enqueue(db, user=OWNER, order_number="123", request_id="first")
    failure = ManualSendRefused(code, "private-response", extra)
    await jobs.run_once(db, executor=AsyncMock(side_effect=failure))
    retry = await jobs.enqueue(db, user=OWNER, order_number="123",
                               operation=operation, request_id="new-click")
    assert retry["job"]["status"] == "needs_review"
    assert "private-response" not in str(retry)
    assert await db.qoyod_manual_auto_quarantines.count_documents({"status": "open"}) == 1


@pytest.mark.asyncio
async def test_hold_failure_is_durable_and_repaired_without_repeating_send(monkeypatch):
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    hold = jobs._hold_unknown
    monkeypatch.setattr(jobs, "_hold_unknown", AsyncMock(side_effect=RuntimeError("storage unavailable")))
    await jobs.run_once(db, executor=AsyncMock(side_effect=RuntimeError("uncertain")))
    saved = await db.qoyod_manual_send_jobs.find_one({"_id": queued["job"]["id"]})
    assert saved["status"] == "needs_review"
    assert saved["hold_pending"] is True
    monkeypatch.setattr(jobs, "_hold_unknown", hold)
    forbidden = AsyncMock(side_effect=AssertionError("never resend to repair metadata"))
    assert await jobs.run_once(db, executor=forbidden) is False
    saved = await db.qoyod_manual_send_jobs.find_one({"_id": queued["job"]["id"]})
    assert "hold_pending" not in saved
    assert (await db.qoyod_manual_auto_quarantines.find_one({}))["code"] == "manual_job_outcome_unknown"
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["lease", "authority"])
async def test_every_provider_write_rechecks_job_lease_and_authority(monkeypatch, change):
    import httpx
    from integrations.qoyod_manual.client import ManualQoyodClient
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    request = AsyncMock(return_value=httpx.Response(200, json={"id": "synthetic"}))
    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    async def execute(db, row):
        client = ManualQoyodClient(api_key="synthetic-key", base_url="https://qoyod.invalid/2.0")
        await client._request("POST", "/invoices", json_body={})
        if change == "lease":
            await db.qoyod_manual_send_jobs.update_one({"_id": row["_id"]},
                {"$set": {"lease_until": jobs.utcnow() - timedelta(seconds=1)}})
        else:
            await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
        await client._request("POST", "/invoice_payments", json_body={})
        raise AssertionError("fenced second write reached")
    await jobs.run_once(db, executor=execute)
    assert request.await_count == 1
    await jobs.recover_expired(db)
    saved = await db.qoyod_manual_send_jobs.find_one({"_id": queued["job"]["id"]})
    assert saved["status"] == "needs_review"


@pytest.mark.asyncio
async def test_heartbeat_lease_loss_stops_execution_without_cancelling_worker_task(monkeypatch):
    db = await make_db()
    queued = await jobs.enqueue(db, user=OWNER, order_number="123")
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", 0.01)
    async def execute(db, row):
        await db.qoyod_manual_send_jobs.update_one({"_id": row["_id"]},
            {"$set": {"lease_until": jobs.utcnow() - timedelta(seconds=1)}})
        await asyncio.Event().wait()
    task = asyncio.create_task(jobs.run_once(db, executor=execute))
    assert await asyncio.wait_for(task, 0.5) is True
    assert not task.cancelled()
    await jobs.recover_expired(db)
    assert (await db.qoyod_manual_send_jobs.find_one({"_id": queued["job"]["id"]}))["status"] == "needs_review"
