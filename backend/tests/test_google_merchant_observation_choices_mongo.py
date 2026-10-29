"""Actual disposable replica membership reads; no credential/provider access."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import HTTPException
import pytest

from integrations_control_center import google_merchant_observation_choices as choices
from tests.test_google_merchant_observation_routes_mongo import owner_db
from tests.test_google_merchant_observation_choices import call
from tests.test_google_merchant_observation_routes import OWNER, USER, CREDS, ACCOUNTS, INTEGRATIONS


@pytest.mark.asyncio
async def test_real_local_choices_do_not_read_or_modify_credentials(owner_db):
    await owner_db[CREDS].delete_many({})
    before = {name: await owner_db[name].find({}).to_list(length=1000) for name in ("users", INTEGRATIONS, ACCOUNTS, CREDS)}
    response = await call(owner_db)
    assert response.status_code == 200 and response.json()["local_account_count"] == 2
    assert response.json()["merchant_access_verified"] is False
    after = {name: await owner_db[name].find({}).to_list(length=1000) for name in before}
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize("count,status", [(400, 200), (401, 409)])
async def test_real_complete_set_sentinel_is_global_not_first_page(owner_db, count, status):
    row = await owner_db[ACCOUNTS].find_one({"_id": "merchant"})
    await owner_db[ACCOUNTS].delete_many({})
    await owner_db[ACCOUNTS].insert_many([{**row, "_id": str(i), "external_account_id": str(i + 1)} for i in range(count)])
    response = await call(owner_db)
    assert response.status_code == status
    if status == 200: assert len(response.json()["merchant_accounts"]) == count
    else: assert "merchant_accounts" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["password", "owner_disable", "owner_role", "owner_replace", "account_add", "account_delete",
    "account_replace", "account_duplicate", "account_name", "provider_revision", "provider_error", "absent_null", "bson_type"])
async def test_real_changes_between_local_snapshots_discard_choices(monkeypatch, owner_db, change):
    if change == "bson_type": await owner_db[ACCOUNTS].update_one({"_id": "ads"}, {"$set": {"mezan_selected": 0}})
    first_done, proceed = asyncio.Event(), asyncio.Event()
    original, reads = choices._snapshot, 0
    async def paused(*args):
        nonlocal reads
        result = await original(*args); reads += 1
        if reads == 1:
            first_done.set()
            await asyncio.wait_for(proceed.wait(), 3)
        return result
    monkeypatch.setattr(choices, "_snapshot", paused)
    task = asyncio.create_task(call(owner_db))
    try:
        await asyncio.wait_for(first_done.wait(), 3)
        if change == "password": await owner_db.users.update_one({"id": OWNER}, {"$set": {"password_updated_at": datetime.now(timezone.utc)}})
        elif change == "owner_disable": await owner_db.users.update_one({"id": OWNER}, {"$set": {"disabled": True}})
        elif change == "owner_role": await owner_db.users.update_one({"id": OWNER}, {"$set": {"role": "viewer"}})
        elif change == "owner_replace":
            row = await owner_db.users.find_one({"id": OWNER}); await owner_db.users.delete_one({"id": OWNER})
            row["_id"] = "replacement-owner"; await owner_db.users.insert_one(row)
        elif change in {"account_add", "account_duplicate", "account_replace"}:
            row = await owner_db[ACCOUNTS].find_one({"_id": "merchant"}); row["_id"] = "new-merchant"
            if change == "account_add": row["external_account_id"] = "456"
            if change == "account_replace": await owner_db[ACCOUNTS].delete_one({"_id": "merchant"})
            await owner_db[ACCOUNTS].insert_one(row)
        elif change == "account_delete": await owner_db[ACCOUNTS].delete_one({"_id": "merchant"})
        elif change == "account_name": await owner_db[ACCOUNTS].update_one({"_id": "merchant"}, {"$set": {"display_name": "new"}})
        elif change == "provider_revision": await owner_db[INTEGRATIONS].update_one({"provider": choices.ADS}, {"$set": {"permission_observation_id": "new"}})
        elif change == "provider_error": await owner_db[INTEGRATIONS].update_one({"provider": choices.ADS}, {"$set": {"connection_status": "error"}})
        elif change == "absent_null": await owner_db[ACCOUNTS].update_one({"_id": "ads"}, {"$set": {"login_customer_id": None}})
        elif change == "bson_type": await owner_db[ACCOUNTS].update_one({"_id": "ads"}, {"$set": {"mezan_selected": False}})
        proceed.set()
        response = await asyncio.wait_for(task, 5)
        assert response.status_code in {403, 409} and "ads_accounts" not in response.json()
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task


@pytest.mark.asyncio
async def test_real_snapshot_consistent_across_collections_then_next_snapshot_sees_change(owner_db):
    expected = await choices._snapshot(owner_db, OWNER)
    changed = False
    class Cursor:
        def __init__(self, cursor): self.cursor = cursor
        def max_time_ms(self, value): self.cursor = self.cursor.max_time_ms(value); return self
        def limit(self, value): self.cursor = self.cursor.limit(value); return self
        async def to_list(self, length):
            nonlocal changed
            rows = await self.cursor.to_list(length=length)
            if not changed:
                changed = True
                await owner_db[INTEGRATIONS].update_one({"provider": choices.MERCHANT}, {"$set": {"connection_status": "error"}})
            return rows
    class Users:
        def find(self, *args, **kwargs): return Cursor(owner_db.users.find(*args, **kwargs))
    class DB:
        client = owner_db.client
        def __getitem__(self, name): return Users() if name == "users" else owner_db[name]
    assert await choices._snapshot(DB(), OWNER) == expected
    with pytest.raises(HTTPException): await choices._snapshot(owner_db, OWNER)


@pytest.mark.asyncio
async def test_real_auth_timestamp_binding_and_fresh_dependency_shape(owner_db):
    await owner_db.users.update_one({"id": OWNER}, {"$set": {
        "password_updated_at": datetime(2026, 10, 29, tzinfo=timezone.utc), "disabled": False, "is_active": True}})
    assert (await call(owner_db, user=deepcopy(USER))).status_code == 403
    user = await owner_db.users.find_one({"id": OWNER}, {"_id": 0})
    user.update(_session_client=None, _review_session=None)
    assert (await call(owner_db, user=user)).status_code == 200
