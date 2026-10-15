"""Legacy-active state cannot authorize alternate user-facing opening writes."""
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from accounts_routes import attach_accounts_routes
from ad_account_routes import attach_ad_account_routes
from ledger_routes import make_ledger_router
from migration_routes import make_migration_router
from universal_accounting_routes import _ensure_opening_balance_seeded


class ReadCollection:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
    async def find_one(self, *args, **kwargs):
        return self.row
    def find(self, *args, **kwargs):
        return self
    async def to_list(self, *args, **kwargs):
        return self.rows
    def __getattr__(self, name):
        raise AssertionError("No legacy mutation allowed: " + name)


class ReadDatabase:
    def __init__(self, **collections):
        self.collections = collections
    def __getattr__(self, name):
        return self.collections.get(name, ReadCollection())
    def __getitem__(self, name):
        return self.__getattr__(name)


async def owner():
    return {"id": "synthetic-owner", "role": "owner", "is_owner": True}


def app_for(db):
    app, router = FastAPI(), APIRouter()
    attach_accounts_routes(router, db)
    attach_ad_account_routes(router, db)
    router.include_router(make_ledger_router(db))
    router.include_router(make_migration_router(db))
    def override_auth(routes):
        for route in routes:
            if hasattr(route, "original_router"):
                override_auth(route.original_router.routes)
            elif hasattr(route, "dependant"):
                for dependency in route.dependant.dependencies:
                    app.dependency_overrides[dependency.call] = owner
    override_auth(router.routes)
    app.include_router(router, prefix="/api")
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("POST", "/accounting/migration/run", {"cutoff_date": "2026-10-01", "dry_run": False}),
    ("PUT", "/ad-accounts/foreign-or-existing/opening", {"opening_balance": 0, "opening_debt": 99}),
    ("POST", "/accounts", {"name": "Bank", "account_type": "bank", "opening_balance": 99}),
    ("POST", "/accounts", {"name": "Bank", "account_type": "bank", "opening_balance": -99}),
    ("POST", "/accounts/bank/transactions", {"transaction_type": "opening_balance", "amount": 99, "direction": "in", "transaction_date": "2026-10-01"}),
    *[("POST", "/ledger/entries", {"entity_type": "bank", "entity_id": "bank", "entry_type": "opening_balance", "amount": 99, "side": "debit", "auto_post": auto}) for auto in (False, True)],
])
async def test_explicit_legacy_opening_routes_fail_before_database_write(method, path, payload):
    async with AsyncClient(transport=ASGITransport(app=app_for(ReadDatabase())), base_url="http://test") as client:
        response = await client.request(method, "/api" + path, json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "opening_onboarding_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("POST", "/ledger/entries/old/post", {}),
    ("POST", "/ledger/entries/old/reverse", {"reason_code": "other", "notes": "synthetic"}),
    ("POST", "/ledger/groups/old/reverse", {"reason_code": "other", "notes": "synthetic"}),
    ("DELETE", "/accounts/bank/transactions/old", None),
    ("DELETE", "/accounts/bank", None),
])
async def test_existing_opening_evidence_cannot_be_posted_reversed_or_deleted(method, path, payload):
    opening = {"id": "old", "entry_type": "opening_balance", "status": "draft", "transaction_type": "opening_balance"}
    db = ReadDatabase(general_ledger=ReadCollection(opening, [opening]), account_transactions=ReadCollection(opening), accounts=ReadCollection({"id": "bank", "opening_balance": 99}))
    async with AsyncClient(transport=ASGITransport(app=app_for(db)), base_url="http://test") as client:
        response = await client.request(method, "/api" + path, json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "opening_onboarding_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("balance", [100, -100])
async def test_lazy_seeding_fails_for_nonzero_unseeded_bank(balance):
    db = ReadDatabase(accounts=ReadCollection({"current_balance": balance}))
    with pytest.raises(HTTPException) as exc:
        await _ensure_opening_balance_seeded(db, user_id="synthetic-owner", account_id="bank")
    assert exc.value.detail["code"] == "opening_onboarding_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("existing,balance", [(True, 100), (False, 0)])
async def test_existing_ledger_or_zero_account_does_not_disable_unrelated_movement(existing, balance):
    db = ReadDatabase(general_ledger=ReadCollection({"_id": "existing"} if existing else None), accounts=ReadCollection({"current_balance": balance}))
    await _ensure_opening_balance_seeded(db, user_id="synthetic-owner", account_id="bank")


# Disposable local replica fixture; never a production database.
from tests.test_financial_accounts_real_mongo import mongo_db


@pytest.mark.asyncio
async def test_migration_dry_run_is_labelled_read_only_and_does_not_create_opening(mongo_db):
    before = {name: await mongo_db[name].count_documents({}) for name in await mongo_db.list_collection_names()}
    async with AsyncClient(transport=ASGITransport(app=app_for(mongo_db)), base_url="http://test") as client:
        response = await client.post("/api/accounting/migration/run", json={"cutoff_date": "2026-10-01", "dry_run": True})
    assert response.status_code == 200, response.text
    assert response.json()["report_scope"] == "LEGACY"
    assert response.json()["diagnostic_only"] is True
    assert response.json()["read_only"] is True
    after = {name: await mongo_db[name].count_documents({}) for name in await mongo_db.list_collection_names()}
    assert before == after


@pytest.mark.asyncio
async def test_zero_balance_account_setup_remains_available_without_opening_transaction(mongo_db):
    async with AsyncClient(transport=ASGITransport(app=app_for(mongo_db)), base_url="http://test") as client:
        response = await client.post("/api/accounts", json={"name": "Synthetic zero bank", "account_type": "bank", "opening_balance": 0})
    assert response.status_code == 200, response.text
    assert response.json()["opening_balance"] == 0
    assert await mongo_db.account_transactions.count_documents({"user_id": "synthetic-owner"}) == 0
    assert await mongo_db.general_ledger.count_documents({"user_id": "synthetic-owner"}) == 0
