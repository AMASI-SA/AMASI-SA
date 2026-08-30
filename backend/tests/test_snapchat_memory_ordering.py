import asyncio
from datetime import date, datetime, timezone

import pytest

from resource_governor import ResourcePressure
import snapchat_v2.sync_pipeline as pipeline_module
from snapchat_v2.sync_pipeline import SnapchatV2SyncPipeline


NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)


class Connection:
    async def ensure_indexes(self):
        return None

    async def validate_ready(self, user_id, ad_account_id=None):
        return {}, {
            "ad_account_id": ad_account_id or "acct-1",
            "timezone": "Asia/Riyadh",
        }


class DB:
    def __init__(self):
        self.authoritative_writes = 0

    def __getitem__(self, name):
        outer = self

        class Collection:
            async def update_one(self, *args, **kwargs):
                outer.authoritative_writes += 1
        return Collection()


def install_preflight_fakes(monkeypatch):
    async def noop(*args, **kwargs):
        return None

    async def zero(*args, **kwargs):
        return 0

    monkeypatch.setattr(pipeline_module, "ensure_lease_indexes", noop)
    monkeypatch.setattr(pipeline_module, "ensure_sync_run_indexes", noop)
    monkeypatch.setattr(pipeline_module, "recover_expired_leases", zero)
    monkeypatch.setattr(pipeline_module, "recover_abandoned_sync_runs", zero)


def make_pipeline(db):
    return SnapchatV2SyncPipeline(
        db, now=lambda: NOW, connection_manager=Connection(),
        client_factory=lambda *_args: object(),
    )


@pytest.mark.asyncio
async def test_waiting_for_global_capacity_does_not_hold_lease_or_create_run(monkeypatch):
    install_preflight_fakes(monkeypatch)
    db = DB()
    gate = asyncio.Event()
    calls = []

    class Governor:
        async def acquire(self, *args, **kwargs):
            calls.append("admission_wait")
            await gate.wait()

    async def lease(*args, **kwargs):
        calls.append("lease")
        return True

    async def create(*args, **kwargs):
        calls.append("run")

    monkeypatch.setattr(pipeline_module, "governor", Governor())
    monkeypatch.setattr(pipeline_module, "acquire_lease", lease)
    monkeypatch.setattr(pipeline_module, "create_sync_run", create)
    task = asyncio.create_task(make_pipeline(db).run(
        "user-1", "acct-1", date_from=date(2026, 8, 30), date_to=date(2026, 8, 30)
    ))
    await asyncio.sleep(0.01)
    assert calls == ["admission_wait"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_unavailable_lease_releases_admission_without_run(monkeypatch):
    install_preflight_fakes(monkeypatch)
    db = DB()
    calls = []
    token = object()

    class Governor:
        async def acquire(self, *args, **kwargs):
            calls.append("admission")
            return token, None
        async def release(self, value):
            assert value is token
            calls.append("release_admission")

    async def lease(*args, **kwargs):
        calls.append("lease")
        return False

    async def forbidden_run(*args, **kwargs):
        raise AssertionError("run must not be created")

    monkeypatch.setattr(pipeline_module, "governor", Governor())
    monkeypatch.setattr(pipeline_module, "acquire_lease", lease)
    monkeypatch.setattr(pipeline_module, "create_sync_run", forbidden_run)
    result = await make_pipeline(db).run("user-1", "acct-1")
    assert result["reason"] == "lease_unavailable"
    assert calls == ["admission", "lease", "release_admission"]


@pytest.mark.asyncio
async def test_lease_error_releases_admission_exactly_once(monkeypatch):
    install_preflight_fakes(monkeypatch)
    db = DB()
    releases = 0

    class Governor:
        async def acquire(self, *args, **kwargs):
            return object(), None
        async def release(self, token):
            nonlocal releases
            releases += 1

    async def lease(*args, **kwargs):
        raise RuntimeError("lease backend failed")

    monkeypatch.setattr(pipeline_module, "governor", Governor())
    monkeypatch.setattr(pipeline_module, "acquire_lease", lease)
    with pytest.raises(RuntimeError, match="lease backend failed"):
        await make_pipeline(db).run("user-1", "acct-1")
    assert releases == 1


@pytest.mark.asyncio
async def test_run_creation_error_releases_lease_and_admission_once(monkeypatch):
    install_preflight_fakes(monkeypatch)
    db = DB()
    admission_releases = 0
    lease_releases = 0

    class Governor:
        async def acquire(self, *args, **kwargs):
            return object(), None
        async def release(self, token):
            nonlocal admission_releases
            admission_releases += 1

    async def acquire(*args, **kwargs):
        return True

    async def create(*args, **kwargs):
        raise RuntimeError("run insert failed")

    async def release(*args, **kwargs):
        nonlocal lease_releases
        lease_releases += 1

    monkeypatch.setattr(pipeline_module, "governor", Governor())
    monkeypatch.setattr(pipeline_module, "acquire_lease", acquire)
    monkeypatch.setattr(pipeline_module, "create_sync_run", create)
    monkeypatch.setattr(pipeline_module, "release_lease", release)
    with pytest.raises(RuntimeError, match="run insert failed"):
        await make_pipeline(db).run("user-1", "acct-1")
    assert admission_releases == 1
    assert lease_releases == 1


@pytest.mark.asyncio
async def test_resource_refusal_before_publish_writes_no_authoritative_data(monkeypatch):
    install_preflight_fakes(monkeypatch)
    db = DB()
    lease_calls = 0

    class Governor:
        async def acquire(self, *args, **kwargs):
            raise ResourcePressure("resource_pressure")

    async def lease(*args, **kwargs):
        nonlocal lease_calls
        lease_calls += 1
        return True

    monkeypatch.setattr(pipeline_module, "governor", Governor())
    monkeypatch.setattr(pipeline_module, "acquire_lease", lease)
    result = await make_pipeline(db).run("user-1", "acct-1")
    assert result == {
        "status": "skipped", "reason": "resource_pressure",
        "retryable": True, "ad_account_id": "acct-1",
    }
    assert lease_calls == 0
    assert db.authoritative_writes == 0
