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
