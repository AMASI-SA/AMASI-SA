import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError

import advertising_product_watch_cycle_v3 as cycle


class FakeCollection:
    def __init__(self):
        self.document = None

    async def insert_one(self, document):
        if self.document is not None:
            raise DuplicateKeyError("duplicate")
        self.document = dict(document)
        return SimpleNamespace(inserted_id=document.get("_id"))

    async def find_one_and_update(self, _query, update, return_document=None):
        if self.document is None:
            return None
        current = update["$set"]["last_started_at"]
        next_run_at = self.document.get("next_run_at")
        due = next_run_at is None or next_run_at <= current
        lease_until = self.document.get("lease_until")
        available = (
            self.document.get("state") != "running"
            or lease_until is None
            or lease_until <= current
        )
        if not (due and available):
            return None
        self.document.update(update["$set"])
        return dict(self.document)

    async def find_one(self, _query, _projection=None):
        return dict(self.document) if self.document else None

    async def update_one(self, query, update):
        if not self.document:
            return SimpleNamespace(modified_count=0)
        if query.get("_id") != self.document.get("_id"):
            return SimpleNamespace(modified_count=0)
        if query.get("state") and query["state"] != self.document.get("state"):
            return SimpleNamespace(modified_count=0)
        if query.get("owner") and query["owner"] != self.document.get("owner"):
            return SimpleNamespace(modified_count=0)
        self.document.update(update.get("$set") or {})
        for key in (update.get("$unset") or {}):
            self.document.pop(key, None)
        return SimpleNamespace(modified_count=1)


class FakeDB:
    def __init__(self):
        self.collection = FakeCollection()

    def __getitem__(self, name):
        assert name == cycle.CADENCE_COLLECTION
        return self.collection


def test_only_one_replica_claims_product_watch_cycle():
    db = FakeDB()
    now = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    first = asyncio.run(cycle.claim_cycle(db, now=now, owner="replica-a"))
    second = asyncio.run(cycle.claim_cycle(db, now=now, owner="replica-b"))
    assert first["claimed"] is True
    assert second["claimed"] is False
    assert second["skip_reason"] == "running_elsewhere"


def test_completed_watch_cycle_blocks_until_short_operational_interval():
    db = FakeDB()
    now = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    claim = asyncio.run(cycle.claim_cycle(db, now=now, owner="replica-a"))
    finished = asyncio.run(cycle.finish_cycle(
        db,
        claim["owner"],
        failed=False,
        now=now + timedelta(minutes=1),
    ))
    early = asyncio.run(cycle.claim_cycle(
        db,
        now=finished["next_run_at"] - timedelta(seconds=1),
        owner="replica-b",
    ))
    due = asyncio.run(cycle.claim_cycle(
        db,
        now=finished["next_run_at"] + timedelta(seconds=1),
        owner="replica-c",
    ))
    assert early["claimed"] is False
    assert early["skip_reason"] == "not_due"
    assert due["claimed"] is True


def test_failed_watch_cycle_uses_five_minute_retry_window():
    db = FakeDB()
    now = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    claim = asyncio.run(cycle.claim_cycle(db, now=now, owner="replica-a"))
    finished = asyncio.run(cycle.finish_cycle(
        db,
        claim["owner"],
        failed=True,
        now=now,
    ))
    assert finished["next_run_at"] == now + timedelta(seconds=cycle.RETRY_SECONDS)


def test_expired_watch_lease_is_recoverable():
    db = FakeDB()
    now = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    first = asyncio.run(cycle.claim_cycle(db, now=now, owner="dead"))
    recovered = asyncio.run(cycle.claim_cycle(
        db,
        now=first["lease_until"] + timedelta(seconds=1),
        owner="healthy",
    ))
    assert recovered["claimed"] is True
    assert recovered["owner"] == "healthy"
