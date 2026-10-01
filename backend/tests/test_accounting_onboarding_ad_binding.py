"""Read-only onboarding uses actual confirmed Track E bindings on scratch Mongo."""
import os
from uuid import uuid4
import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
from accounting_advertising_contract import BINDINGS, Binding
from accounting_advertising_setup import setup, binding_key
from accounting_onboarding_identities import require_ad_financial_binding, identities, verify_mappings

OWNER = "synthetic-ad-onboarding"

class Reads(CommandListener):
    def __init__(self): self.collections = []
    def started(self, event):
        if event.command_name in {"find", "aggregate", "count", "distinct"}:
            self.collections.append(event.command[event.command_name])
    def succeeded(self, event): pass
    def failed(self, event): pass

@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri: pytest.skip("Dedicated synthetic Mongo required")
    monitor = Reads()
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[monitor])
    database = client["mz2_onboarding_ad_"+uuid4().hex]
    assert (await client.admin.command("hello")).get("setName")
    await database.users.insert_one({"id": OWNER, "role": "owner", "is_active": True})
    try:
        yield database
        assert not {"accounts", "ad_accounts", "general_ledger", "ad_account_ledger", "snapchat_ad_accounts"}.intersection(monitor.collections)
    finally:
        await client.drop_database(database.name)
        client.close()

async def seed(db, mode="prepaid", platform="meta"):
    integration = platform+"-integration"
    await db.mezan_integration_accounts_v2.insert_one({"user_id": OWNER,
        "provider": "meta_ads" if platform == "meta" else "snapchat_ads",
        "mezan_integration_account_id": integration, "external_account_id": "same-platform-id",
        "display_name": "Synthetic ad", "currency": "SAR", "timezone": "Asia/Riyadh", "connection_status": "connected"})
    for suffix, kind in (("wallet", "ad_prepaid_wallet"), ("payable", "ad_payable")):
        await db.mz2_financial_accounts.insert_one({"user_id": OWNER, "id": platform+"-"+suffix,
            "account_type": kind, "currency": "SAR", "status": "active", "external_ref": "deliberately-unrelated"})
    await setup(db, OWNER, Binding(platform=platform, integration_account_id=integration,
        platform_account_id="same-platform-id", currency="SAR", funding_mode=mode,
        wallet_financial_account_id=platform+"-wallet" if mode != "postpaid" else None,
        payable_financial_account_id=platform+"-payable" if mode != "prepaid" else None,
        hybrid_policy="explicit_split" if mode == "hybrid" else None, version=0, evidence="Synthetic signed terms"))
    return await db[BINDINGS].find_one({"_id": binding_key(OWNER, platform, integration)})

async def compiled(db, binding):
    lines = []
    for field, category, sub in (("wallet_financial_account_id", "ad_prepaid_wallet", "balance"),
                                  ("payable_financial_account_id", "ad_payable", "debt")):
        key = binding.get(field)
        if key:
            account = await db.mz2_financial_accounts.find_one({"user_id": OWNER, "id": key})
            lines.append({"financial_account_id": key, "account_snapshot": account, "entity_type": "ad_account",
                "entity_id": key, "category": category, "sub_account": sub, "amount": "0.00", "evidence_file_id": "signed-zero"})
    return {"lines": lines, "section_evidence_file_ids": {}}

async def snapshot(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(None) for name in await db.list_collection_names()}

@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prepaid", "postpaid", "hybrid"])
async def test_confirmed_mode_exact_financial_ids_and_explicit_zero_coverage_read_only(db, mode):
    binding = await seed(db, mode)
    facts = await compiled(db, binding)
    before = await snapshot(db)
    catalog = await identities(db, OWNER, "ad_account")
    assert len(catalog) == 1 and catalog[0]["id"] == binding["_id"]
    assert catalog[0]["funding_mode"] == mode
    mapped = await verify_mappings(db, OWNER, facts, [])
    assert {row["financial_account_id"] for row in mapped} == {line["financial_account_id"] for line in facts["lines"]}
    for line in facts["lines"]:
        assert await require_ad_financial_binding(db, OWNER, line["financial_account_id"]) == binding
    with pytest.raises(HTTPException) as missing:
        await verify_mappings(db, OWNER, {**facts, "lines": facts["lines"][1:]}, [])
    assert missing.value.detail["code"] == "onboarding_entity_balance_required"
    assert await snapshot(db) == before

@pytest.mark.asyncio
@pytest.mark.parametrize("collection,changes,code", [
    (BINDINGS, {"confirmed_by": ""}, "ad_binding_missing"),
    (BINDINGS, {"platform": {}}, "onboarding_native_ad_binding_dependency"),
    (BINDINGS, {"currency": None}, "onboarding_native_ad_binding_dependency"),
    (BINDINGS, {"status": "inactive"}, "ad_binding_inactive"),
    (BINDINGS, {"currency": "USD"}, "ad_binding_identity_or_currency_mismatch"),
    (BINDINGS, {"platform_account_id": "wrong"}, "ad_binding_identity_or_currency_mismatch"),
    (BINDINGS, {"funding_mode": "postpaid"}, "ad_binding_funding_contract_invalid"),
    ("mz2_financial_accounts", {"status": "inactive"}, "ad_financial_binding_foreign_inactive_or_wrong_type"),
    ("mz2_financial_accounts", {"account_type": "bank"}, "ad_financial_binding_foreign_inactive_or_wrong_type"),
    ("mz2_financial_accounts", {"currency": "USD"}, "ad_financial_binding_currency_mismatch"),
    ("mezan_integration_accounts_v2", {"connection_status": "disconnected"}, "ad_v2_integration_inactive"),
])
async def test_invalid_current_binding_fails_closed_without_mutation(db, collection, changes, code):
    await seed(db)
    query = {"user_id": OWNER}
    if collection == "mz2_financial_accounts": query["id"] = "meta-wallet"
    await db[collection].update_one(query, {"$set": changes})
    before = await snapshot(db)
    with pytest.raises(HTTPException) as denied:
        await require_ad_financial_binding(db, OWNER, "meta-wallet")
    assert denied.value.status_code == 409 and denied.value.detail["code"] == code
    assert await snapshot(db) == before

@pytest.mark.asyncio
async def test_ambiguity_owner_scope_and_external_ref_cannot_authorize(db):
    binding = await seed(db)
    for owner, identity in (("other", "meta-wallet"), (OWNER, "deliberately-unrelated"), (OWNER, "meta-payable")):
        with pytest.raises(HTTPException) as denied:
            await require_ad_financial_binding(db, owner, identity)
        assert denied.value.detail["code"] == "onboarding_native_ad_binding_dependency"
    duplicate = {**binding, "_id": "duplicate-binding"}
    await db[BINDINGS].insert_one(duplicate)
    with pytest.raises(HTTPException) as denied:
        await require_ad_financial_binding(db, OWNER, "meta-wallet")
    assert denied.value.detail["code"] == "onboarding_native_ad_binding_ambiguous"
    await db[BINDINGS].delete_one({"_id": "duplicate-binding"})
    financial = await db.mz2_financial_accounts.find_one({"user_id": OWNER, "id": "meta-wallet"})
    financial.pop("_id")
    await db.mz2_financial_accounts.insert_one(financial)
    with pytest.raises(HTTPException) as denied:
        await require_ad_financial_binding(db, OWNER, "meta-wallet")
    assert denied.value.detail["code"] == "onboarding_native_ad_financial_identity_invalid"

@pytest.mark.asyncio
async def test_platform_scope_preserves_distinct_bindings_with_same_external_id(db):
    await seed(db, "prepaid", "meta")
    await seed(db, "postpaid", "snapchat")
    catalog = await identities(db, OWNER, "ad_account")
    assert len(catalog) == 2 and len({row["id"] for row in catalog}) == 2
    assert {row["platform"] for row in catalog} == {"meta", "snapchat"}
