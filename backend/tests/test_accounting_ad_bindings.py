import asyncio
from copy import deepcopy
import pytest
from bson import json_util
from fastapi import HTTPException
from accounting_ad_bindings import AdBindingSave, save_ad_binding, ad_binding_metadata, list_ad_bindings
from tests.test_financial_accounts_real_mongo import mongo_db, OWNER


async def seed(db):
    await db.mezan_integration_accounts_v2.insert_many([
        {"user_id": OWNER, "mezan_integration_account_id": "meta-1", "provider": "meta_ads", "connection_provenance": "api_connection", "external_account_id": "external-1", "currency": "SAR"},
        {"user_id": OWNER, "mezan_integration_account_id": "meta-2", "provider": "meta_ads", "connection_provenance": "api_connection", "external_account_id": "external-2", "currency": "SAR"},
        {"user_id": "other-owner", "mezan_integration_account_id": "foreign", "provider": "meta_ads", "connection_provenance": "api_connection", "external_account_id": "external-3"},
        {"user_id": OWNER, "mezan_integration_account_id": "legacy-provenance", "provider": "meta_ads", "connection_provenance": "legacy", "external_account_id": "external-4"},
    ])
    await db.mz2_financial_accounts.insert_many([
        {"user_id": OWNER, "id": "wallet", "account_type": "ad_prepaid_wallet", "currency": "SAR", "status": "active", "external_ref": "unused-old-value"},
        {"user_id": OWNER, "id": "payable", "account_type": "ad_payable", "currency": "SAR", "status": "active"},
        {"user_id": OWNER, "id": "usd", "account_type": "ad_prepaid_wallet", "currency": "USD", "status": "active"},
        {"user_id": "other-owner", "id": "foreign-wallet", "account_type": "ad_prepaid_wallet", "currency": "SAR", "status": "active"},
    ])
    await db.accounts.insert_one({"user_id": OWNER, "id": "legacy-wallet", "account_type": "ad_prepaid_wallet", "currency": "SAR"})


def payload(**patch):
    return AdBindingSave(**{"version": 0, "idempotency_key": "ad-binding-create-001", "provider": "meta_ads", "currency": "SAR", "effective_from": "2026-10-01", "status": "active", "prepaid_wallet_account_id": "wallet", "payable_account_id": "payable", **patch})


async def snapshot(db, exclude=()):
    return {name: sorted(json_util.dumps(r, sort_keys=True) for r in await db[name].find({}).to_list(None)) for name in await db.list_collection_names() if name not in exclude}


@pytest.mark.asyncio
async def test_explicit_binding_save_reload_readonly_idempotency_and_audit(mongo_db):
    db = mongo_db
    await seed(db)
    untouched = await snapshot(db, ("mz2_ad_account_bindings", "mz2_atomic_owners"))
    saved = await save_ad_binding(db, OWNER, "meta-1", payload(), "actor")
    assert saved["version"] == 1 and saved["audit"][0]["actor_id"] == "actor"
    assert (await save_ad_binding(db, OWNER, "meta-1", payload(), "actor"))["existing"] is True
    assert await db.mz2_ad_account_bindings.count_documents({}) == 1
    before = await snapshot(db)
    meta = await ad_binding_metadata(db, OWNER, {"id": "meta-1", "provider": "meta_ads", "currency": "SAR"}, as_of="2026-10-01")
    assert meta["binding_status"] == "valid" and not meta["binding_gaps"]
    assert meta["prepaid_wallet_account_id"] == "wallet" and meta["payable_account_id"] == "payable"
    assert (await list_ad_bindings(db, OWNER))[0]["integration_account_id"] == "meta-1"
    assert await snapshot(db) == before
    assert await snapshot(db, ("mz2_ad_account_bindings", "mz2_atomic_owners")) == untouched


@pytest.mark.asyncio
async def test_paused_save_rolls_back_everything_and_cannot_touch_invalid_identity(mongo_db):
    await seed(mongo_db)
    await mongo_db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    before = await snapshot(mongo_db)
    with pytest.raises(HTTPException) as exc:
        await save_ad_binding(mongo_db, OWNER, "nonexistent", payload(), "actor")
    assert exc.value.status_code == 423 and exc.value.detail["code"] == "mz2_writes_paused"
    assert await snapshot(mongo_db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("identity,patch,code", [
    ("foreign", {}, "ad_binding_integration_identity_invalid"),
    ("legacy-provenance", {}, "ad_binding_integration_identity_invalid"),
    ("meta-1", {"prepaid_wallet_account_id": "legacy-wallet"}, "ad_binding_financial_identity_invalid"),
    ("meta-1", {"prepaid_wallet_account_id": "foreign-wallet"}, "ad_binding_financial_identity_invalid"),
    ("meta-1", {"prepaid_wallet_account_id": "usd"}, "ad_binding_currency_mismatch"),
    ("meta-1", {"provider": "snapchat_ads"}, "ad_binding_provider_mismatch"),
    ("meta-1", {"payable_account_id": "wallet", "prepaid_wallet_account_id": None}, "ad_binding_account_type_or_status_invalid"),
])
async def test_wrong_tenant_legacy_provider_currency_or_account_type_rejected(mongo_db, identity, patch, code):
    await seed(mongo_db)
    before = await snapshot(mongo_db)
    with pytest.raises(HTTPException) as exc:
        await save_ad_binding(mongo_db, OWNER, identity, payload(**patch), "actor")
    assert exc.value.detail["code"] == code
    assert await snapshot(mongo_db) == before


@pytest.mark.asyncio
async def test_duplicate_binding_reuse_stale_cas_and_changed_idempotency_rejected(mongo_db):
    await seed(mongo_db)
    await save_ad_binding(mongo_db, OWNER, "meta-1", payload(), "actor")
    with pytest.raises(HTTPException) as exc:
        await save_ad_binding(mongo_db, OWNER, "meta-2", payload(), "actor")
    assert exc.value.detail["code"] == "ad_binding_financial_account_already_bound"
    with pytest.raises(HTTPException) as exc:
        await save_ad_binding(mongo_db, OWNER, "meta-1", payload(effective_from="2026-09-01"), "actor")
    assert exc.value.detail["code"] == "ad_binding_idempotency_conflict"
    with pytest.raises(HTTPException) as exc:
        await save_ad_binding(mongo_db, OWNER, "meta-1", payload(idempotency_key="stale-request-new"), "actor")
    assert exc.value.detail["code"] == "ad_binding_version_conflict"


@pytest.mark.asyncio
async def test_incomplete_binding_and_cutover_date_are_explicit_gaps(mongo_db):
    await seed(mongo_db)
    profile = {"id": "meta-1", "provider": "meta_ads", "currency": "SAR"}
    missing = await ad_binding_metadata(mongo_db, OWNER, profile)
    assert missing["binding_status"] == "missing"
    assert missing["prepaid_wallet_account_id"] is None  # external_ref never auto-binds.
    await save_ad_binding(mongo_db, OWNER, "meta-1", payload(payable_account_id=None), "actor")
    incomplete = await ad_binding_metadata(mongo_db, OWNER, profile, as_of="2026-10-01")
    assert incomplete["binding_status"] == "incomplete" and incomplete["binding_gaps"] == ["ad_payable_missing"]
    future = await ad_binding_metadata(mongo_db, OWNER, profile, as_of="2026-09-30")
    assert future["binding_status"] == "invalid" and future["binding_gaps"] == ["ad_binding_not_effective_at_cutover"]
    await mongo_db.mz2_financial_accounts.update_one({"user_id": OWNER, "id": "wallet"}, {"$set": {"status": "inactive"}})
    revoked = await ad_binding_metadata(mongo_db, OWNER, profile)
    assert revoked["binding_status"] == "invalid" and revoked["prepaid_wallet_account_id"] is None


@pytest.mark.asyncio
async def test_concurrent_same_request_is_single_binding_and_single_audit_revision(mongo_db):
    await seed(mongo_db)
    rows = await asyncio.gather(*[save_ad_binding(mongo_db, OWNER, "meta-1", payload(), "actor") for _ in range(2)])
    assert {r["id"] for r in rows} == {rows[0]["id"]}
    assert sorted(r["existing"] for r in rows) == [False, True]
    stored = await mongo_db.mz2_ad_account_bindings.find_one({"user_id": OWNER})
    assert stored["version"] == 1 and len(stored["audit"]) == 1
    for name in ("general_ledger", "mz2_journals", "mz2_ledger_entries"):
        assert await mongo_db[name].count_documents({}) == 0
