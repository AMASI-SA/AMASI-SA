from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from mongomock_motor import AsyncMongoMockClient
import pytest

from snapchat_v2 import cadence
import snapchat_v2.sync_pipeline as pipeline
from tests.test_snapchat_memory_ordering import install_preflight_fakes, make_pipeline

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("configured,expected", [
    (None, 7200), ("300", 7200), ("1800", 7200), ("7200", 7200),
    ("10800", 10800), ("999999", 86400), ("invalid", 7200),
])
def test_owner_cadence_survives_legacy_configuration(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv(cadence.INTERVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(cadence.INTERVAL_ENV, configured)
    assert cadence.shadow_interval_seconds() == expected


@pytest.mark.asyncio
async def test_restart_reads_completion_and_manual_refresh_is_account_scoped(monkeypatch):
    monkeypatch.delenv(cadence.INTERVAL_ENV, raising=False)
    db = AsyncMongoMockClient().db
    await db[cadence.SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one({
        "user_id": "owner", "ad_account_id": "a", "run_type": "manual", "status": "complete",
        "started_at": NOW - timedelta(minutes=20), "finished_at": NOW - timedelta(minutes=4),
    })
    expected = NOW + timedelta(minutes=116)
    assert await cadence.automatic_next_due(db, "owner", "a", now=NOW) == expected
    assert await cadence.automatic_next_due(db, "owner", "b", now=NOW) is None
    assert await cadence.automatic_next_due(db, "another-owner", "a", now=NOW) is None
    assert await cadence.automatic_next_due(db, "owner", "a", now=expected) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,blocked", [("complete", True), ("partial", True),
    ("failed", True), ("skipped", False), ("abandoned", False)])
async def test_finished_attempts_cool_down_but_recovery_is_preserved(monkeypatch, status, blocked):
    monkeypatch.delenv(cadence.INTERVAL_ENV, raising=False)
    db = AsyncMongoMockClient().db
    await db[cadence.SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one({
        "user_id": "owner", "ad_account_id": "a", "status": status,
        "started_at": NOW - timedelta(minutes=16), "finished_at": NOW,
    })
    due = await cadence.automatic_next_due(db, "owner", "a", now=NOW)
    assert (due is not None) is blocked
    if blocked:
        assert due == NOW + timedelta(hours=2)


@pytest.mark.asyncio
async def test_gate_is_rechecked_under_account_lease_before_new_run(monkeypatch):
    install_preflight_fakes(monkeypatch)
    events = []
    token = object()
    class Governor:
        async def acquire(self, *args, **kwargs):
            events.append("admission")
            return token, None
        async def release(self, value):
            assert value is token
            events.append("release_admission")
    async def acquire(*args, **kwargs):
        events.append("account_lease")
        return True
    async def check(*args, **kwargs):
        events.append("persisted_due_check")
        return NOW + timedelta(hours=2)
    async def release(*args, **kwargs):
        events.append("release_account_lease")
    create = AsyncMock(side_effect=AssertionError("a throttled attempt must not create work"))
    monkeypatch.setattr(pipeline, "governor", Governor())
    monkeypatch.setattr(pipeline, "acquire_lease", acquire)
    monkeypatch.setattr(pipeline, "automatic_next_due", check)
    monkeypatch.setattr(pipeline, "release_lease", release)
    monkeypatch.setattr(pipeline, "create_sync_run", create)
    result = await make_pipeline(object()).run("owner", "a", automatic=True)
    assert result["reason"] == "cadence_gate_2h"
    assert events == ["admission", "account_lease", "persisted_due_check", "release_account_lease", "release_admission"]
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_manual_action_retains_existing_path(monkeypatch):
    install_preflight_fakes(monkeypatch)
    monkeypatch.setattr(pipeline.governor, "acquire", AsyncMock(return_value=(object(), None)))
    monkeypatch.setattr(pipeline.governor, "release", AsyncMock())
    monkeypatch.setattr(pipeline, "acquire_lease", AsyncMock(return_value=True))
    monkeypatch.setattr(pipeline, "release_lease", AsyncMock())
    gate = AsyncMock(side_effect=AssertionError("manual work must not call automatic cadence"))
    monkeypatch.setattr(pipeline, "automatic_next_due", gate)
    create = AsyncMock(side_effect=RuntimeError("manual entered existing run path"))
    monkeypatch.setattr(pipeline, "create_sync_run", create)
    with pytest.raises(RuntimeError, match="manual entered"):
        await make_pipeline(object()).run("owner", "a", run_type="manual")
    create.assert_awaited_once()
    gate.assert_not_awaited()
