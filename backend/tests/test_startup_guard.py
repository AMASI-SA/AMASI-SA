import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError
import pytest

import resource_governor
import startup_guard


class LeaseCollection:
    def __init__(self):
        self.docs = {}
        self.lock = asyncio.Lock()

    async def find_one(self, query):
        async with self.lock:
            doc = self.docs.get(query["_id"])
            return dict(doc) if doc else None

    async def update_one(self, query, update, upsert=False):
        async with self.lock:
            key = query["_id"]
            doc = self.docs.get(key)
            matches = False
            if "status" in query and isinstance(query["status"], dict):
                current = query["$or"][0]["expires_at"]["$lte"]
                matches = bool(
                    doc
                    and doc.get("status") != "completed"
                    and ("expires_at" not in doc or doc["expires_at"] <= current)
                )
            elif doc:
                matches = all(doc.get(field) == value for field, value in query.items())
            if not matches and doc is not None and upsert:
                raise DuplicateKeyError("active or completed release lease")
            if not matches and doc is not None:
                return SimpleNamespace(matched_count=0, upserted_id=None)
            created = doc is None
            doc = dict(doc or {"_id": key})
            doc.update(update.get("$set", {}))
            for field in update.get("$unset", {}):
                doc.pop(field, None)
            self.docs[key] = doc
            return SimpleNamespace(
                matched_count=0 if created else 1,
                upserted_id=key if created else None,
            )


class DB:
    def __init__(self):
        self.collection = LeaseCollection()

    def __getitem__(self, name):
        assert name == startup_guard.COLLECTION
        return self.collection


class NoPressureGovernor:
    def __init__(self):
        self.entries = 0

    def heavy(self, kind, *, task_name):
        outer = self

        class Context:
            async def __aenter__(self):
                assert kind == "startup"
                outer.entries += 1

            async def __aexit__(self, *args):
                return False

        return Context()


@pytest.mark.asyncio
async def test_global_initialization_runs_once_and_followers_run_local():
    db = DB()
    governor = NoPressureGovernor()
    global_calls = 0
    local_calls = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def global_init():
        nonlocal global_calls
        global_calls += 1
        entered.set()
        await release.wait()

    async def run(owner):
        async def local_init():
            local_calls.append(owner)
        return await startup_guard.run_release_startup(
            db, release_key="sha-a", owner_id=owner, governor=governor,
            global_initialization=global_init, local_initialization=local_init,
            poll_interval=0.001, wait_timeout=1, ttl_seconds=5,
        )

    leader = asyncio.create_task(run("replica-a"))
    await entered.wait()
    follower = asyncio.create_task(run("replica-b"))
    await asyncio.sleep(0.01)
    assert global_calls == 1
    release.set()
    roles = await asyncio.gather(leader, follower)
    assert sorted(roles) == ["follower", "leader"]
    assert sorted(local_calls) == ["replica-a", "replica-b"]
    assert governor.entries == 1


@pytest.mark.asyncio
async def test_follower_replica_provider_scheduler_waits_for_local_completion():
    db = DB()
    governor = NoPressureGovernor()
    global_entered = asyncio.Event()
    finish_global = asyncio.Event()
    replica_b_ready = asyncio.Event()
    provider_called = asyncio.Event()

    async def global_init():
        global_entered.set()
        await finish_global.wait()

    async def scheduler_b():
        await replica_b_ready.wait()
        provider_called.set()

    scheduler = asyncio.create_task(scheduler_b())
    leader = asyncio.create_task(startup_guard.run_release_startup(
        db, release_key="sha-a", owner_id="replica-a", governor=governor,
        global_initialization=global_init,
        local_initialization=lambda: asyncio.sleep(0),
        poll_interval=0.001, wait_timeout=1, ttl_seconds=5,
    ))
    await global_entered.wait()

    async def local_b():
        replica_b_ready.set()

    follower = asyncio.create_task(startup_guard.run_release_startup(
        db, release_key="sha-a", owner_id="replica-b", governor=governor,
        global_initialization=global_init, local_initialization=local_b,
        poll_interval=0.001, wait_timeout=1, ttl_seconds=5,
    ))
    await asyncio.sleep(0.01)
    assert not replica_b_ready.is_set()
    assert not provider_called.is_set()
    finish_global.set()
    await asyncio.wait_for(asyncio.gather(leader, follower, scheduler), 1)
    assert replica_b_ready.is_set()
    assert provider_called.is_set()


@pytest.mark.asyncio
async def test_crashed_owner_expires_and_stale_fence_cannot_heartbeat():
    db = DB()
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    first = await startup_guard.claim_startup_lease(
        db, "sha-a", "replica-a", now=now, ttl_seconds=5
    )
    recovered = await startup_guard.claim_startup_lease(
        db, "sha-a", "replica-b", now=now + timedelta(seconds=6), ttl_seconds=5
    )
    assert first.state == "leader"
    assert recovered.state == "leader"
    assert recovered.fence != first.fence
    assert not await startup_guard.heartbeat_startup_lease(
        db, first, now=now + timedelta(seconds=6), ttl_seconds=5
    )


@pytest.mark.asyncio
async def test_live_heartbeat_prevents_expiry_overlap():
    db = DB()
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    first = await startup_guard.claim_startup_lease(
        db, "sha-a", "replica-a", now=now, ttl_seconds=5
    )
    assert await startup_guard.heartbeat_startup_lease(
        db, first, now=now + timedelta(seconds=4), ttl_seconds=5
    )
    second = await startup_guard.claim_startup_lease(
        db, "sha-a", "replica-b", now=now + timedelta(seconds=6), ttl_seconds=5
    )
    assert second.state == "waiting"


@pytest.mark.asyncio
async def test_lost_owner_fence_cancels_old_global_initializer(monkeypatch):
    db = DB()
    cancelled = asyncio.Event()

    async def lost_heartbeat(*args, **kwargs):
        await asyncio.sleep(0)
        raise RuntimeError("startup lease fencing lost")

    async def global_init():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(startup_guard, "_heartbeat_loop", lost_heartbeat)
    with pytest.raises(RuntimeError, match="fencing lost"):
        await startup_guard.run_release_startup(
            db, release_key="sha-a", owner_id="replica-a",
            governor=NoPressureGovernor(), global_initialization=global_init,
            local_initialization=lambda: asyncio.sleep(0), wait_timeout=1,
        )
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_startup_reserves_weighted_capacity_before_ads(monkeypatch):
    monkeypatch.setenv("HEAVY_GLOBAL_CAPACITY", "2")
    monkeypatch.setenv("HEAVY_STARTUP_WEIGHT", "2")
    governor = resource_governor.ResourceGovernor()
    monkeypatch.setattr(
        resource_governor, "memory_snapshot",
        lambda: resource_governor.MemorySnapshot(10, 100, 10, {}, 1, None, 1, 0.1),
    )
    db = DB()
    startup_entered = asyncio.Event()
    release_startup = asyncio.Event()
    ads_entered = asyncio.Event()

    async def global_init():
        startup_entered.set()
        await release_startup.wait()

    startup = asyncio.create_task(startup_guard.run_release_startup(
        db, release_key="sha-a", owner_id="replica-a", governor=governor,
        global_initialization=global_init, local_initialization=lambda: asyncio.sleep(0),
        wait_timeout=1, ttl_seconds=5,
    ))
    await startup_entered.wait()

    async def ads():
        async with governor.heavy("ads", task_name="ads"):
            ads_entered.set()

    ads_task = asyncio.create_task(ads())
    await asyncio.sleep(0.01)
    assert not ads_entered.is_set()
    assert governor.diagnostics()["global_heavy_in_use"] == 2
    release_startup.set()
    await asyncio.wait_for(asyncio.gather(startup, ads_task), 1)
    assert ads_entered.is_set()


def test_jitter_uses_replica_identity_and_secure_random(monkeypatch):
    monkeypatch.setenv("REPLICA_ID", "replica-a")
    monkeypatch.setattr(startup_guard.secrets, "randbelow", lambda value: 0)
    first = startup_guard.replica_jitter(20)
    monkeypatch.setenv("REPLICA_ID", "replica-b")
    second = startup_guard.replica_jitter(20)
    assert 0 <= first <= 20
    assert 0 <= second <= 20
    assert first != second


def test_verified_release_identity_is_required_in_production():
    with pytest.raises(ValueError, match="verified release source_git_sha"):
        startup_guard.verified_release_key(
            {"release": {"source_git_sha": None}},
            environment={"APP_ENV": "production"},
        )
    assert startup_guard.verified_release_key(
        {"release": {
            "source_git_sha": "a" * 40,
            "verified_identity_available": True,
            "critical_file_hashes_match": True,
            "frontend_build_verified": True,
        }},
        environment={"APP_ENV": "production"},
    ) == "a" * 40
    with pytest.raises(ValueError):
        startup_guard.verified_release_key(
            {"release": {"source_git_sha": "a" * 40}},
            environment={"APP_ENV": "production"},
        )


def test_unverified_key_requires_explicit_test_or_development_configuration():
    assert startup_guard.verified_release_key(
        {"release": {}},
        environment={"APP_ENV": "test", "TEST_RELEASE_STARTUP_KEY": "test:unit"},
    ) == "test:unit"
    with pytest.raises(ValueError):
        startup_guard.verified_release_key(
            {"release": {}}, environment={"APP_ENV": "test"}
        )
