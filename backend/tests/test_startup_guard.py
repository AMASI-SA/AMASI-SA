from datetime import datetime, timezone
from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError
import pytest

import startup_guard


class LeaseCollection:
    def __init__(self): self.owner = None

    async def update_one(self, query, update, upsert=False):
        requested = update["$set"]["owner_id"]
        if self.owner not in (None, requested):
            raise DuplicateKeyError("active lease")
        created = self.owner is None
        self.owner = requested
        return SimpleNamespace(matched_count=not created, upserted_id="x" if created else None)

    async def delete_one(self, query):
        if self.owner == query["owner_id"]: self.owner = None


class DB:
    def __init__(self): self.collection = LeaseCollection()
    def __getitem__(self, name): return self.collection


@pytest.mark.asyncio
async def test_replicas_cannot_hold_startup_lease_together():
    db = DB()
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    assert await startup_guard.acquire_startup_lease(db, "replica-a", now=now)
    assert not await startup_guard.acquire_startup_lease(db, "replica-b", now=now)
    await startup_guard.release_startup_lease(db, "replica-a")
    assert await startup_guard.acquire_startup_lease(db, "replica-b", now=now)


def test_jitter_uses_replica_identity_and_secure_random(monkeypatch):
    monkeypatch.setenv("REPLICA_ID", "replica-a")
    monkeypatch.setattr(startup_guard.secrets, "randbelow", lambda value: 0)
    first = startup_guard.replica_jitter(20)
    monkeypatch.setenv("REPLICA_ID", "replica-b")
    second = startup_guard.replica_jitter(20)
    assert 0 <= first <= 20
    assert 0 <= second <= 20
    assert first != second
