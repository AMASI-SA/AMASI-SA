import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError

import campaign_ai_global_cadence as cadence


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
        current = update["$set"]["claimed_at"]
        next_run_at = self.document.get("next_run_at")
        due = next_run_at is None or next_run_at <= current
        state = self.document.get("state")
        lease_until = self.document.get("lease_until")
        available = (
            state != "running"
            or lease_until is None
            or lease_until <= current
        )
        if not (due and available):
            return None
        self.document.update(update.get("$set") or {})
        return dict(self.document)

    async def find_one(self, _query):
        return dict(self.document) if self.document is not None else None

    async def update_one(self, query, update):
        if self.document is None:
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
        assert name == cadence.CADENCE_COLLECTION
        return self.collection


def test_only_one_replica_can_claim_current_global_cycle(monkeypatch):
    monkeypatch.delenv("MEZAN_CAMPAIGN_AI_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("MEZAN_CAMPAIGN_AI_GLOBAL_LEASE_SECONDS", raising=False)
    db = FakeDB()
    now = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)

    first = asyncio.run(cadence.claim_global_cycle(db, now=now, owner="replica-a"))
    second = asyncio.run(cadence.claim_global_cycle(db, now=now, owner="replica-b"))

    assert first["claimed"] is True
    assert first["owner"] == "replica-a"
    assert second["claimed"] is False
    assert second["skip_reason"] == "running_elsewhere"


def test_completed_cycle_blocks_sequential_replica_for_five_hours(monkeypatch):
    monkeypatch.setenv("MEZAN_CAMPAIGN_AI_INTERVAL_SECONDS", str(5 * 60 * 60))
    db = FakeDB()
    now = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)

    first = asyncio.run(cadence.claim_global_cycle(db, now=now, owner="replica-a"))
    finished = asyncio.run(cadence.finish_global_cycle(
        db,
        first["owner"],
        retryable=False,
        outcome="success",
        now=now + timedelta(minutes=4),
    ))
    immediate = asyncio.run(cadence.claim_global_cycle(
        db,
        now=now + timedelta(minutes=5),
        owner="replica-b",
    ))
    due = asyncio.run(cadence.claim_global_cycle(
        db,
        now=finished["next_run_at"] + timedelta(seconds=1),
        owner="replica-c",
    ))

    assert immediate["claimed"] is False
    assert immediate["skip_reason"] == "not_due"
    assert due["claimed"] is True
    assert due["owner"] == "replica-c"


def test_retryable_failure_opens_only_short_retry_window(monkeypatch):
    monkeypatch.setenv("MEZAN_CAMPAIGN_AI_RETRY_DELAY_SECONDS", "900")
    db = FakeDB()
    now = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)

    claim = asyncio.run(cadence.claim_global_cycle(db, now=now, owner="replica-a"))
    finished = asyncio.run(cadence.finish_global_cycle(
        db,
        claim["owner"],
        retryable=True,
        outcome="retryable_ai_failure",
        now=now + timedelta(minutes=2),
    ))

    before_retry = asyncio.run(cadence.claim_global_cycle(
        db,
        now=finished["next_run_at"] - timedelta(seconds=1),
        owner="replica-b",
    ))
    at_retry = asyncio.run(cadence.claim_global_cycle(
        db,
        now=finished["next_run_at"] + timedelta(seconds=1),
        owner="replica-a-retry",
    ))

    assert before_retry["claimed"] is False
    assert before_retry["skip_reason"] == "not_due"
    assert at_retry["claimed"] is True


def test_expired_crashed_replica_lease_can_be_recovered(monkeypatch):
    monkeypatch.setenv("MEZAN_CAMPAIGN_AI_GLOBAL_LEASE_SECONDS", "120")
    db = FakeDB()
    now = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)

    first = asyncio.run(cadence.claim_global_cycle(db, now=now, owner="dead-replica"))
    recovered = asyncio.run(cadence.claim_global_cycle(
        db,
        now=first["lease_until"] + timedelta(seconds=1),
        owner="healthy-replica",
    ))

    assert recovered["claimed"] is True
    assert recovered["owner"] == "healthy-replica"
