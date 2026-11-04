"""Exercise automatic shutdown on local Mongo, with no accounting writer."""
from fastapi import HTTPException
import pytest

from test_operational_balance_integration import run, started, movement
from operational_balance_store import read
from operational_balance_service import create_movement
from operational_balance_worker import tick


def test_accounting_activation_freezes_once_without_accounting_write():
    async def scenario(db):
        await started(db)
        marker = {"_id": "owner", "ledger_backend_state": "v2_active"}
        await db.mz2_atomic_owners.insert_one(marker)
        await tick(db)
        state = await read(db, "owner")
        assert state["status"] == "frozen"
        assert state["snapshot"]["sha256"]
        assert state["movements"] == []
        await tick(db)
        assert await read(db, "owner") == state
        assert await db.mz2_atomic_owners.find_one({"_id": "owner"}) == marker
        with pytest.raises(HTTPException) as error:
            await create_movement(db, "owner", "owner", movement())
        assert error.value.detail["code"] == "operational_not_active"
    run(scenario)


@pytest.mark.asyncio
async def test_successful_worker_refresh_queues_missing_native_bank_proof(monkeypatch):
    import sys
    from types import SimpleNamespace
    import operational_balance_worker as worker

    issues = [{"code": "operational_order_bank_binding_incomplete", "source_id": "missing-bank"}]
    calls = []

    async def owners():
        yield {"owner_id": "owner"}

    async def refreshed(db, owner):
        return {"status": "active", "engine": {"issues": issues}}

    async def acquire(db, owner, pending):
        calls.append((owner, pending))

    class Database:
        def __getitem__(self, key):
            return SimpleNamespace(find=lambda *args: owners())

    monkeypatch.setattr(worker, "refresh", refreshed)
    monkeypatch.setitem(sys.modules, "salla_integration.bank_payment_sync", SimpleNamespace(acquire_missing=acquire))
    await worker.tick(Database())
    assert calls == [("owner", issues)]
