"""Original-request recovery is read-only, tenant scoped and count proven."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

from preparation_file_failure_safety import make_preparation_file_failure_safety_router
from preparation_file_registry import REGISTRY
from reviewed_preparation_batches import BATCHES
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS


@pytest.mark.asyncio
async def test_lost_response_recovers_exact_ready_file_without_writing_or_reassigning():
    db = AsyncMongoMockClient().outcome
    request = "synthetic-request-1"
    selector = {"user_id": "owner", "client_request_id": request}
    await db[REGISTRY].insert_one({**selector, "status": "ready", "batch_id": "batch-1", "file_number": "PF-SYNTHETIC-1",
        "file_title": "Synthetic", "expected_quantity": 2, "allocated_quantity": 2, "responsible_employee_id": "employee-1"})
    await db[BATCHES].insert_one({**selector, "id": "batch-1", "status": "ready", "allocated_quantity": 2,
        "selections": [{"group_key": "physical-1", "quantity": 1, "revision": "snapshot-1"}]})
    await db[PREPARATION_UNIT_ALLOCATIONS].insert_many([{"user_id": "owner", "batch_id": "batch-1", "status": "committed", "unit_index": i} for i in [1, 2]])
    identity = {"id": "owner", "role": "owner"}
    app = FastAPI()
    app.include_router(make_preparation_file_failure_safety_router(db, lambda: identity))
    before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as http:
        path = f"/preparation-file-safety-v1/requests/{request}/status"
        response = await http.get(path)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == "COMMITTED" and result["read_only"] is True
        assert result["file"]["batch_id"] == "batch-1" and result["file"]["responsible_employee_id"] == "employee-1"
        assert result["batch"]["selections"][0]["revision"] == "snapshot-1"
        assert before == {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
        await db[PREPARATION_UNIT_ALLOCATIONS].update_one({"unit_index": 2}, {"$set": {"status": "reserved"}})
        assert (await http.get(path)).json()["state"] == "PENDING", "registry alone is not completion proof"
        identity["id"] = "foreign-owner"
        foreign = (await http.get(path)).json()
        assert foreign["state"] == "ABSENT" and "file" not in foreign and "batch" not in foreign
