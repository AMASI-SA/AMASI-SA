"""Diagnostic lifecycle follows the existing financial refresh lease and result."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest

from operational_balance_freshness import HEALTH
from operational_balance_store import STATES
from operational_demand_refresh import LEASES
from operational_source_diagnostics import source_failure_detail
from test_operational_balance_demand_refresh import Collection as RefreshCollection, DB as RefreshDB
from test_operational_balance_demand_refresh_routes import post
from test_operational_balance_freshness import Collection as HealthCollection


class Collection(RefreshCollection):
    async def update_one(self, query, update, **options):
        if self.name == HEALTH:
            if getattr(self.db, "fail_completion", False) and "attempt_token" in query:
                raise RuntimeError("diagnostic unavailable")
            return await HealthCollection(self.db, self.name).update_one(query, update, **options)
        return await super().update_one(query, update)


class DB(RefreshDB):
    def __init__(self):
        super().__init__()
        self.broken = set()

    def __getitem__(self, name):
        return Collection(self, name)


def test_attempt_starts_after_lease_and_success_retains_source_gaps(monkeypatch):
    import operational_visible_evidence
    db = DB()
    async def acquire(database, owner):
        assert db.data[LEASES]
        assert db.data[HEALTH][owner]["last_attempt"]["outcome"] == "running"
        return {"status": "deferred", "bank": {"status": "deferred", "reason": "unavailable"}}
    monkeypatch.setattr(operational_visible_evidence, "acquire_visible_evidence", acquire)
    response, _ = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 200 and response.json()["status"] == "refreshed"
    record = db.data[HEALTH]["owner"]
    assert record["last_attempt"]["outcome"] == "success"
    assert record["source_warnings"][0]["key"] == "bank"
    assert db.data[STATES]["owner"]["revision"] == 2 and db.data[LEASES] == {}


def test_source_failure_is_persisted_without_changing_financial_error_contract(monkeypatch):
    import operational_visible_evidence
    db = DB()
    original = deepcopy(db.data[STATES])
    detail = source_failure_detail("operational_source_read_timeout", "mezan_employees_v2")
    async def acquire(*args, **kwargs):
        raise HTTPException(409, detail=detail)
    monkeypatch.setattr(operational_visible_evidence, "acquire_visible_evidence", acquire)
    response, _ = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 409
    assert all(response.json()["detail"][key] == value for key, value in detail.items())
    assert db.data[HEALTH]["owner"]["last_failure"] == detail
    assert db.data[HEALTH]["owner"]["last_attempt"]["outcome"] == "failed"
    assert db.data[STATES] == original and db.data[LEASES] == {}


def test_timeout_records_failed_attempt_after_owned_work_unwinds(monkeypatch):
    import operational_demand_refresh
    import operational_visible_evidence
    db, unwound = DB(), []
    async def acquire(*args, **kwargs):
        try:
            await asyncio.sleep(1)
        finally:
            unwound.append(True)
    monkeypatch.setattr(operational_visible_evidence, "acquire_visible_evidence", acquire)
    monkeypatch.setattr(operational_demand_refresh, "REQUEST_SECONDS", 0.02)
    response, _ = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 408 and unwound == [True]
    record = db.data[HEALTH]["owner"]["last_attempt"]
    assert record["outcome"] == "failed" and record["error_code"] == "operational_refresh_timeout"
    assert db.data[STATES]["owner"]["revision"] == 1 and db.data[LEASES] == {}


def test_cancelled_attempt_has_unknown_outcome_and_does_not_retry(monkeypatch):
    import operational_visible_evidence
    db, calls = DB(), []
    async def acquire(*args, **kwargs):
        calls.append(True)
        raise asyncio.CancelledError
    monkeypatch.setattr(operational_visible_evidence, "acquire_visible_evidence", acquire)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(post(db, monkeypatch))
    assert calls == [True]
    assert db.data[HEALTH]["owner"]["last_attempt"]["outcome"] == "unknown"
    assert db.data[STATES]["owner"]["revision"] == 1 and db.data[LEASES] == {}


def test_busy_lease_does_not_overwrite_previous_attempt(monkeypatch):
    db = DB()
    original = {"_id": "owner", "owner_id": "owner", "last_attempt": {"outcome": "running"}}
    db.data[HEALTH] = {"owner": deepcopy(original)}
    db.data[LEASES] = {"owner": {"_id": "owner", "owner_id": "owner", "actor_id": "other", "token": "busy",
                               "expires_at": datetime.now(timezone.utc) + timedelta(seconds=90)}}
    response, _ = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 409
    assert db.data[HEALTH]["owner"] == original
    assert all(row[0] != HEALTH for row in db.writes)


def test_diagnostic_completion_failure_does_not_change_known_financial_success(monkeypatch):
    import operational_visible_evidence
    db = DB()
    db.fail_completion = True
    async def acquire(*args, **kwargs):
        return {"status": "attempted"}
    monkeypatch.setattr(operational_visible_evidence, "acquire_visible_evidence", acquire)
    response, _ = asyncio.run(post(db, monkeypatch))
    assert response.status_code == 200 and response.json()["status"] == "refreshed"
    assert db.data[STATES]["owner"]["revision"] == 2
    assert db.data[HEALTH]["owner"]["last_attempt"]["outcome"] == "running"
    assert db.data[LEASES] == {}
