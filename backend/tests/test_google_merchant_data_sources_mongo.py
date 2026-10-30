"""Actual Mongo owner/credential races, synthetic intercepted Google responses."""
import asyncio

import pytest

from integrations_control_center import google_ads_reporting as reporting
from tests.test_google_merchant_observation_routes import (
    OWNER, USER, CREDS, ACCOUNTS, INTEGRATIONS, fixture_rows,
)
from tests.test_google_merchant_observation_routes_mongo import owner_db  # noqa: F401
from tests.test_google_merchant_data_sources_routes import call, providers


@pytest.mark.asyncio
async def test_real_owner_source_read_preserves_all_persisted_documents(monkeypatch, owner_db):
    calls = providers(monkeypatch)
    before = {name: await owner_db[name].find({}).to_list(length=1000) for name in fixture_rows()}
    response = await call(owner_db)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sources"][0]["resource_name"] == "accounts/123/dataSources/18"
    assert data["pagination_traversal_complete"] is True
    assert data["publisher_identity_verified"] is data["store_product_mapping_verified"] is False
    after = {name: await owner_db[name].find({}).to_list(length=1000) for name in fixture_rows()}
    assert before == after and calls["merchant"] == calls["close"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner_role", "password_revoked", "account_deleted", "account_replaced",
    "duplicate_account", "provider_error", "scope_revoke", "credential_delete", "relink", "missing_to_null"])
async def test_real_membership_or_auth_change_during_native_read_discards_sources(monkeypatch, owner_db, change):
    started, proceed = asyncio.Event(), asyncio.Event()
    async def effect(stage, count):
        if stage == "merchant":
            started.set()
            await asyncio.wait_for(proceed.wait(), 3)
    calls = providers(monkeypatch, effect=effect)
    task = asyncio.create_task(call(owner_db))
    try:
        await asyncio.wait_for(started.wait(), 3)
        if change == "owner_role": await owner_db.users.update_one({"id": OWNER}, {"$set": {"role": "viewer"}})
        elif change == "password_revoked": await owner_db.users.update_one({"id": OWNER}, {"$set": {"password_updated_at": "new-stamp"}})
        elif change == "account_deleted": await owner_db[ACCOUNTS].delete_one({"_id": "merchant"})
        elif change in {"account_replaced", "duplicate_account"}:
            row = await owner_db[ACCOUNTS].find_one({"_id": "merchant"})
            if change == "account_replaced": await owner_db[ACCOUNTS].delete_one({"_id": "merchant"})
            row["_id"] = "changed-identity"
            await owner_db[ACCOUNTS].insert_one(row)
        elif change == "provider_error": await owner_db[INTEGRATIONS].update_one({"provider": "google_merchant_center"}, {"$set": {"connection_status": "error"}})
        elif change == "scope_revoke": await owner_db[CREDS].update_one({"user_id": OWNER}, {"$set": {"scope": ["https://www.googleapis.com/auth/adwords"]}})
        elif change == "credential_delete": await owner_db[CREDS].delete_one({"user_id": OWNER})
        elif change == "relink": await owner_db[CREDS].update_one({"user_id": OWNER}, {"$set": {"google_subject": "relinked-subject", "access_token_ciphertext": "PRIVATE_CHANGED"}})
        elif change == "missing_to_null": await owner_db[ACCOUNTS].update_one({"_id": "ads"}, {"$set": {"login_customer_id": None}})
        proceed.set()
        response = await asyncio.wait_for(task, 4)
        assert response.status_code in (403, 409), response.text
        assert "sources" not in response.json() and "PRIVATE_CHANGED" not in response.text
        assert calls["close"] == 1
    finally:
        proceed.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task


@pytest.mark.asyncio
async def test_real_membership_changed_while_token_acquired_stops_before_provider(monkeypatch, owner_db):
    calls = providers(monkeypatch)
    original = reporting._credential
    async def raced(*args):
        token = await original(*args)
        await owner_db[ACCOUNTS].delete_one({"_id": "merchant"})
        return token
    monkeypatch.setattr(reporting, "_credential", raced)
    response = await call(owner_db)
    assert response.status_code == 409
    assert calls["metadata"] == calls["merchant"] == 0 and calls["close"] == 1


@pytest.mark.asyncio
async def test_real_stale_authenticated_principal_refused_before_token(monkeypatch, owner_db):
    await owner_db.users.update_one({"id": OWNER}, {"$set": {"password_updated_at": "new-stamp"}})
    calls = providers(monkeypatch)
    assert (await call(owner_db, user=USER)).status_code == 403
    assert calls["constructed"] == 0


@pytest.mark.asyncio
async def test_real_other_owner_changes_cannot_poison_current_sources(monkeypatch, owner_db):
    async def effect(stage, count):
        if stage == "merchant":
            await owner_db[ACCOUNTS].insert_one({"user_id": "other", "provider": "google_merchant_center", "external_account_id": "123"})
    providers(monkeypatch, effect=effect)
    response = await call(owner_db)
    assert response.status_code == 200 and response.json()["scope"]["owner_id"] == OWNER
