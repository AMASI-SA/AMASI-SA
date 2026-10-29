"""Disposable replica races; real stored membership/credential data, fake Google."""
import asyncio
from copy import deepcopy
import os
from urllib.parse import urlsplit
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.write_concern import WriteConcern
import pytest
import pytest_asyncio

from integrations_control_center import google_merchant_observation_routes as route
from tests.test_google_merchant_observation_routes import (
    OWNER, ACCOUNT, MERCHANT, USER, CREDS, ACCOUNTS, INTEGRATIONS,
    call, fixture_rows, providers,
)


@pytest_asyncio.fixture
async def owner_db():
    uri = os.getenv("GOOGLE_MERCHANT_OWNER_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set a disposable loopback GOOGLE_MERCHANT_OWNER_TEST_MONGO_URI")
    parsed = urlsplit(uri)
    if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None or parsed.username or parsed.password or "," in parsed.netloc
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Owner observation tests require one disposable loopback replica")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1000, socketTimeoutMS=4000, retryWrites=False)
    name = "google_merchant_owner_" + uuid.uuid4().hex
    db = client.get_database(name, write_concern=WriteConcern(w="majority", j=True))
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") and hello.get("isWritablePrimary") is True
        for collection, rows in fixture_rows().items():
            await db[collection].insert_many(deepcopy(rows))
        yield db
    finally:
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
async def test_real_owner_snapshot_and_actual_credential_reader_allow_bounded_observation(monkeypatch, owner_db):
    calls = providers(monkeypatch)
    before = {name: await owner_db[name].find({}).to_list(length=1000) for name in fixture_rows()}
    response = await call(owner_db)
    assert response.status_code == 200, response.text
    assert response.json()["products"][0]["offer_id"] == "SKU1"
    assert response.json()["store_product_mapping_verified"] is False
    assert calls["merchant"] == 1 and calls["close"] == 1
    after = {name: await owner_db[name].find({}).to_list(length=1000) for name in fixture_rows()}
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner_disabled", "owner_role", "owner_deleted", "account_add", "account_delete",
    "account_duplicate", "account_replaced", "provider_error", "provider_duplicate", "scope_revoke", "relink",
    "credential_delete", "absent_to_null", "null_to_absent"])
async def test_real_changes_while_google_read_is_suspended_discard_result(monkeypatch, owner_db, change):
    if change == "null_to_absent":
        await owner_db[ACCOUNTS].update_one({"_id": "ads"}, {"$set": {"login_customer_id": None}})
    started, proceed = asyncio.Event(), asyncio.Event()
    async def effect(stage, count):
        if stage == "merchant":
            started.set()
            await asyncio.wait_for(proceed.wait(), 3)
    calls = providers(monkeypatch, effect=effect)
    task = asyncio.create_task(call(owner_db))
    try:
        await asyncio.wait_for(started.wait(), 3)
        if change == "owner_disabled": await owner_db.users.update_one({"id": OWNER}, {"$set": {"disabled": True}})
        elif change == "owner_role": await owner_db.users.update_one({"id": OWNER}, {"$set": {"role": "viewer"}})
        elif change == "owner_deleted": await owner_db.users.delete_one({"id": OWNER})
        elif change in {"account_add", "account_duplicate"}:
            row = await owner_db[ACCOUNTS].find_one({"_id": "ads"})
            row["_id"] = "additional"
            if change == "account_add": row.update(external_account_id="456", ad_account_id="456")
            await owner_db[ACCOUNTS].insert_one(row)
        elif change == "account_delete": await owner_db[ACCOUNTS].delete_one({"_id": "merchant"})
        elif change == "account_replaced":
            row = await owner_db[ACCOUNTS].find_one({"_id": "ads"})
            await owner_db[ACCOUNTS].delete_one({"_id": "ads"})
            row["_id"] = "replacement"
            await owner_db[ACCOUNTS].insert_one(row)
        elif change == "provider_error": await owner_db[INTEGRATIONS].update_one({"provider": route.ADS}, {"$set": {"connection_status": "error"}})
        elif change == "provider_duplicate":
            row = await owner_db[INTEGRATIONS].find_one({"provider": route.MERCHANT})
            row["_id"] = "duplicate"
            await owner_db[INTEGRATIONS].insert_one(row)
        elif change == "scope_revoke": await owner_db[CREDS].update_one({"user_id": OWNER}, {"$set": {"scope": ["https://www.googleapis.com/auth/adwords"]}})
        elif change == "relink": await owner_db[CREDS].update_one({"user_id": OWNER}, {"$set": {"access_token_ciphertext": "new-ciphertext", "google_subject": "new-subject"}})
        elif change == "credential_delete": await owner_db[CREDS].delete_one({"user_id": OWNER})
        elif change == "absent_to_null": await owner_db[ACCOUNTS].update_one({"_id": "ads"}, {"$set": {"login_customer_id": None}})
        elif change == "null_to_absent": await owner_db[ACCOUNTS].update_one({"_id": "ads"}, {"$unset": {"login_customer_id": ""}})
        proceed.set()
        response = await asyncio.wait_for(task, 4)
        assert response.status_code in (403, 409), response.text
        assert "SKU1" not in response.text and "products" not in response.json()
        assert calls["close"] == 1
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task


@pytest.mark.asyncio
async def test_real_unrelated_owner_changes_do_not_break_selected_owner_read(monkeypatch, owner_db):
    async def effect(stage, count):
        if stage == "merchant":
            await owner_db[ACCOUNTS].insert_one({"user_id": "other", "provider": route.ADS,
                "external_account_id": ACCOUNT, "connection_status": "error"})
    providers(monkeypatch, effect=effect)
    response = await call(owner_db)
    assert response.status_code == 200 and response.json()["scope"]["owner_id"] == OWNER


@pytest.mark.asyncio
async def test_real_transaction_reads_consistent_snapshot_across_collections(owner_db):
    expected = await route._membership(owner_db, OWNER, ACCOUNT, MERCHANT)
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
                # Commit a different provider state AFTER the first actual
                # transaction read but before the provider collection read.
                await owner_db[INTEGRATIONS].update_one({"provider": route.MERCHANT}, {"$set": {"connection_status": "error"}})
            return rows
    class Users:
        def find(self, *args, **kwargs): return Cursor(owner_db.users.find(*args, **kwargs))
    class DB:
        client = owner_db.client
        def __getitem__(self, name): return Users() if name == "users" else owner_db[name]
    observed = await route._membership(DB(), OWNER, ACCOUNT, MERCHANT)
    assert observed == expected and changed is True
    with pytest.raises(Exception) as error:
        await route._membership(owner_db, OWNER, ACCOUNT, MERCHANT)
    assert error.value.detail["code"] == "google_merchant_membership_unproved"


@pytest.mark.asyncio
async def test_real_membership_change_after_token_acquisition_before_first_google_read(monkeypatch, owner_db):
    from integrations_control_center import google_ads_reporting as reporting
    actual = reporting._credential
    async def changed(db, user, now):
        token = await actual(db, user, now)
        await owner_db[ACCOUNTS].update_one({"_id": "merchant"}, {"$set": {"connection_status": "error"}})
        return token
    providers_calls = providers(monkeypatch)
    monkeypatch.setattr(reporting, "_credential", changed)
    response = await call(owner_db)
    assert response.status_code == 409
    assert providers_calls["metadata"] == providers_calls["links"] == providers_calls["merchant"] == 0
    assert providers_calls["close"] == 1
