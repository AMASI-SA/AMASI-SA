"""Provider identity boundary: legacy collections are deliberately inaccessible."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, HTTPException
import accounting_settlement_routes as routes

OWNER = "identity-test-owner"


def bank(**changes):
    return {"id": "bank-1", "user_id": OWNER, "account_type": "bank", "currency": "SAR",
            "status": "active", "name": "Canonical bank", **changes}


def matches(row, query):
    for key, value in query.items():
        if isinstance(value, dict):
            if "$in" in value and row.get(key) not in value["$in"]: return False
            if "$ne" in value and row.get(key) == value["$ne"]: return False
        elif row.get(key) != value: return False
    return True


class Cursor:
    def __init__(self, rows): self.rows = deepcopy(rows)
    def sort(self, *args): return self
    def limit(self, count): self.rows = self.rows[:count]; return self
    async def to_list(self, length=None): return self.rows[:length]
    def __aiter__(self): self.iterator = iter(self.rows); return self
    async def __anext__(self):
        try: return next(self.iterator)
        except StopIteration: raise StopAsyncIteration


class Collection:
    def __init__(self, rows=()): self.rows = list(deepcopy(rows)); self.writes = 0
    async def find_one(self, query, projection=None):
        return deepcopy(next((row for row in self.rows if matches(row, query)), None))
    def find(self, query, projection=None): return Cursor([row for row in self.rows if matches(row, query)])
    def aggregate(self, pipeline): return Cursor([])
    async def update_one(self, query, update, **kwargs):
        self.writes += 1
        row = next((row for row in self.rows if matches(row, query)), None)
        if row is None: row = dict(query); self.rows.append(row)
        row.update(update["$set"])
        return SimpleNamespace(matched_count=1)


class Database:
    def __init__(self, accounts=(), bindings=()):
        self.mz2_financial_accounts = Collection(accounts)
        self.accounting_provider_bank_bindings_v2 = Collection(bindings)
        self.accounting_settlements_v2 = Collection()
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected collection access: {name}")
    def __getitem__(self, name): return getattr(self, name)
    def assert_no_writes(self):
        assert all(collection.writes == 0 for collection in vars(self).values())


def binding(**changes):
    return {"user_id": OWNER, "provider": "tamara", "bank_account_id": "bank-1",
            "verification_status": "verified", **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [bank()], [bank(status="inactive")]])
async def test_unmarked_old_binding_never_becomes_canonical_even_id_collision(rows):
    db = Database(rows, [binding()])
    result = await routes._binding_view(db, OWNER, "tamara")
    assert result["configured"] is False
    assert result["code"] == "MZ2_LINK_REQUIRED"
    assert await routes._verified_binding_bank_id(db, OWNER, "tamara") is None
    db.assert_no_writes()


@pytest.mark.asyncio
async def test_missing_binding_does_not_read_settings_or_create_binding():
    db = Database([bank()])
    result = await routes._binding_view(db, OWNER, "tamara")
    assert not result["configured"]
    assert result["code"] == "MZ2_LINK_REQUIRED"
    db.assert_no_writes()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,valid", [({}, True), ({"status": "inactive"}, False),
    ({"currency": "USD"}, False), ({"user_id": "other"}, False), ({"account_type": "cash"}, False)])
async def test_explicit_binding_requires_active_owner_sar_bank(changes, valid):
    db = Database([bank(**changes)], [binding(bank_account_source="mz2_financial_accounts", identity_contract_version=1)])
    view = await routes._binding_view(db, OWNER, "tamara")
    assert view["configured"] is valid
    assert await routes._verified_binding_bank_id(db, OWNER, "tamara") == ("bank-1" if valid else None)
    db.assert_no_writes()


def endpoint(db, monkeypatch, name):
    monkeypatch.setattr(routes, "_scope", AsyncMock(return_value=({"id": OWNER, "role": "owner"}, OWNER)))
    monkeypatch.setattr(routes, "ensure_accounting_settlement_indexes", AsyncMock())
    monkeypatch.setattr(routes, "write_audit", AsyncMock())
    router = APIRouter()
    routes.install_accounting_settlement_routes(router, db, lambda: {})
    return next(route.endpoint for route in router.routes if route.endpoint.__name__ == name)


@pytest.mark.asyncio
async def test_context_exposes_only_canonical_active_sar_bank_cash_without_writes(monkeypatch):
    db = Database([bank(), bank(id="cash-1", account_type="cash"), bank(id="inactive", status="inactive"),
                   bank(id="usd", currency="USD"), bank(id="foreign", user_id="other")])
    result = await endpoint(db, monkeypatch, "settlement_context")({})
    assert {row["id"] for row in result["banks"]} == {"bank-1", "cash-1"}
    assert result["legacy_financial_data_included"] is False
    db.assert_no_writes()


@pytest.mark.asyncio
async def test_explicit_rebind_records_canonical_fk_and_does_not_write_settings(monkeypatch):
    db = Database([bank()], [binding()])
    result = await endpoint(db, monkeypatch, "save_provider_binding")(
        "tamara", routes.ProviderBankBindingIn(bank_account_id="bank-1", confirmed=True), {})
    assert result["configured"] is True
    assert result["bank_account_source"] == "mz2_financial_accounts"
    assert result["identity_contract_version"] == 1
    assert result["currency"] == "SAR"
    assert db.accounting_provider_bank_bindings_v2.writes == 1
    assert db.mz2_financial_accounts.writes == db.accounting_settlements_v2.writes == 0


@pytest.mark.asyncio
async def test_legacy_only_bank_binding_fails_closed_before_writes(monkeypatch):
    db = Database()
    with pytest.raises(HTTPException) as exc:
        await endpoint(db, monkeypatch, "save_provider_binding")(
            "tamara", routes.ProviderBankBindingIn(bank_account_id="legacy-bank", confirmed=True), {})
    assert exc.value.detail["code"] == "MZ2_LINK_REQUIRED"
    db.assert_no_writes()
