"""Disposable in-memory domain adapter tests; never contact Mongo or external APIs."""
import asyncio
import copy
import json

import pytest
from pydantic import ValidationError

from accounting_onboarding_domains import (
    ExternalPersonIn, create_external_person,
    onboarding_domains, onboarding_inventory_catalog,
)


def matches(row, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(row, branch) for branch in value):
                return False
            continue
        actual = row.get(key)
        if isinstance(value, dict):
            if "$in" in value and actual not in value["$in"]:
                return False
            if "$ne" in value and actual == value["$ne"]:
                return False
            if "$nin" in value and actual in value["$nin"]:
                return False
        elif actual != value:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, maximum):
        return self.rows[:maximum]


class Collection:
    def __init__(self, rows=()):
        self.rows = copy.deepcopy(list(rows))
        self.writes = []

    def find(self, query, projection=None):
        assert "user_id" in query or "id" in query
        return Cursor([{key: copy.deepcopy(value) for key, value in row.items() if projection is None or (not any(projection.values()) and key != "_id") or projection.get(key)}
                       for row in self.rows if matches(row, query)])

    async def find_one(self, query, projection=None):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def distinct(self, field, query):
        return list(dict.fromkeys(row.get(field) for row in self.rows if matches(row, query) and row.get(field) is not None))

    async def insert_one(self, row):
        self.writes.append(copy.deepcopy(row))
        self.rows.append(copy.deepcopy(row))


class DB:
    def __init__(self, **collections):
        self.collections = {name: Collection(rows) for name, rows in collections.items()}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())

    def __getattr__(self, name):
        return self[name]


def run(value):
    return asyncio.run(value)


def test_discovery_is_tenant_scoped_identity_only_and_p02_locked():
    db = DB(mz2_financial_accounts=[
        {"user_id": "owner", "id": "bank", "name": "Bank", "account_type": "bank", "status": "active", "balance": 900},
        {"user_id": "other", "id": "foreign", "account_type": "bank", "status": "active"}],
        mezan_employees_v2=[{"user_id": "owner", "id": "e", "category": "employee", "name": "Employee", "salary": 500}],
        mezan_suppliers_v2=[{"user_id": "owner", "id": "s", "company_name": "Supplier"}],
        store_drivers=[{"user_id": "owner", "id": "d", "name": "Driver"}],
        mezan_integration_accounts_v2=[{"user_id": "owner", "mezan_integration_account_id": "ad", "provider": "meta_ads", "display_name": "Ad", "external_account_id": "external-ad", "connection_provenance": "api_connection"}],
        accounting_settlements_v2=[{"user_id": "owner", "provider": "salla", "balance": 200}],
        mz2_salla_order_evidence=[{"user_id": "owner", "shipping_company": "سمسا"}])
    result = run(onboarding_domains(db, "owner"))
    assert result["identity_only"] and result["p02_status"] == "LOCKED"
    for group in ("banks", "employees", "suppliers", "store_drivers", "ad_accounts", "couriers"):
        assert len(result["entities"][group]) == 1
    serialized = json.dumps(result)
    for forbidden in ('"balance"', '"salary"', '"cost"', '"cod_fee_percent"', '"settlement_bank"', 'foreign'):
        assert forbidden not in serialized
    assert all(not collection.writes for collection in db.collections.values())


def test_ambiguous_and_inactive_banks_are_not_proposed():
    db = DB(mz2_financial_accounts=[
        {"user_id": "o", "id": "same", "account_type": "bank", "status": "active"},
        {"user_id": "o", "id": "hidden", "account_type": "bank", "status": "hidden"}],
        accounts=[{"user_id": "o", "id": "same", "_id": "legacy"}])
    result = run(onboarding_domains(db, "o"))
    assert result["entities"]["banks"] == []
    assert any(row["code"] == "financial_account_identity_ambiguous" and row["entity_id"] == "same" for row in result["warnings"])


def test_catalog_keeps_registered_units_variants_and_options_without_financial_readiness():
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "p", "variants": [{"id": "v", "options": [{"color": "red"}]}]}],
        mezan_cost_resources_v2=[{"user_id": "o", "id": "r", "track_inventory": True, "unit": "meter", "category_ids": ["c"]},
                                 {"user_id": "o", "id": "inactive", "track_inventory": True, "status": "inactive"}],
        warehouse_locations=[{"user_id": "o", "id": "l", "warehouse_id": "w", "purpose": "permanent_storage", "barcode_value": "A"}])
    result = run(onboarding_inventory_catalog(db, "o"))
    assert result["products"][0]["variants_required"]
    assert result["products"][0]["variants"][0]["options"] == [{"color": "red"}]
    assert [row["unit"] for row in result["components"]] == ["meter"]
    assert result["locations"][0]["barcode_value"] == "A"
    assert result["inventory_accounts"] == [] and not result["unit_conversion_supported"]
    assert all(not collection.writes for collection in db.collections.values())


def test_external_person_create_and_select_preserves_phone_and_real_entity_id():
    db = DB()
    result = run(create_external_person(db, "owner", ExternalPersonIn(name="  Person  ", phone="+966555555555", notes="Evidence contact")))
    assert result["id"] == result["entity_id"] and len(result["id"]) == 36
    assert result["phone"] == "+966555555555"
    assert db.counterparties.writes[0]["kind"] == "general"
    selected = run(onboarding_domains(db, "owner"))["entities"]["external_persons"]
    assert [{key: row[key] for key in result} for row in selected] == [result]
    assert run(onboarding_domains(db, "other"))["entities"]["external_persons"] == []
    assert [key for key, collection in db.collections.items() if collection.writes] == ["counterparties"]
    with pytest.raises(Exception) as duplicate:
        run(create_external_person(db, "owner", ExternalPersonIn(name="Person", force=True)))
    assert duplicate.value.status_code == 409


def test_external_person_rejects_empty_name_and_financial_fields():
    for payload in ({"name": "   "}, {"name": "P", "opening_balance": 0}):
        with pytest.raises(ValidationError):
            ExternalPersonIn(**payload)


def test_domain_helpers_do_not_expose_routes_before_track_a_contract():
    import accounting_onboarding_domains as domains
    assert not hasattr(domains, "make_accounting_onboarding_domains_router")


def test_canonical_financial_accounts_keep_currency_type_and_reference_without_balances():
    db = DB(mz2_financial_accounts=[
        {"user_id": "o", "id": "b", "account_type": "bank", "currency": "USD", "external_ref": "bank-ref", "status": "active", "balance": 999},
        {"user_id": "o", "id": "c", "account_type": "cash", "currency": "SAR", "external_ref": "cash-ref", "status": "active", "opening_balance": 88},
        {"user_id": "o", "id": "d", "account_type": "overdraft", "currency": "EUR", "status": "active", "credit_limit": 1000},
        {"user_id": "other", "id": "foreign", "account_type": "cash", "status": "active"}])
    result = run(onboarding_domains(db, "o"))
    choices = result["entities"]["financial_accounts"]
    assert [(row["id"], row["account_type"], row["currency"]) for row in choices] == [
        ("b", "bank", "USD"), ("c", "cash", "SAR"), ("d", "overdraft", "EUR")]
    assert choices[1]["external_ref"] == "cash-ref"
    assert [row["id"] for row in result["entities"]["banks"]] == ["b"]
    serialized = json.dumps(result)
    for field in ('"balance"', '"opening_balance"', '"credit_limit"', 'foreign'):
        assert field not in serialized
    assert all(not collection.writes for collection in db.collections.values())


def test_canonical_duplicate_and_legacy_cash_collisions_are_excluded():
    db = DB(mz2_financial_accounts=[
        {"user_id": "o", "id": "duplicate", "account_type": kind, "status": "active"}
        for kind in ("cash", "overdraft")
    ] + [{"user_id": "o", "id": "collision", "account_type": "cash", "status": "active"}],
        accounts=[{"_id": "legacy", "user_id": "o", "id": "collision"}])
    result = run(onboarding_domains(db, "o"))
    assert result["entities"]["financial_accounts"] == []
    account_warnings = [row for row in result["warnings"] if row["stage"] == "02"]
    assert {row["entity_id"] for row in account_warnings} == {"duplicate", "collision"}
    assert {row["code"] for row in account_warnings} == {"financial_account_identity_ambiguous"}


def test_ad_financial_identities_require_explicit_binding_not_external_ref():
    db = DB(mz2_financial_accounts=[{"user_id": "o", "id": "wallet", "account_type": "ad_prepaid_wallet", "status": "active", "currency": "USD", "external_ref": "ad"}],
        mezan_integration_accounts_v2=[{"user_id": "o", "mezan_integration_account_id": "ad", "provider": "meta_ads", "external_account_id": "external-ad", "display_name": "Ad", "currency": "USD", "connection_provenance": "api_connection"}],
        counterparties=[{"user_id": "o", "id": "legacy-ad", "kind": "ad_account"}])
    result = run(onboarding_domains(db, "o"))
    assert [row["id"] for row in result["entities"]["ad_accounts"]] == ["ad"]
    ad = result["entities"]["ad_accounts"][0]
    assert ad["prepaid_wallet_account_id"] is None
    assert ad["binding_status"] == "missing"
    assert ad["currency"] == "USD"
    assert all(not collection.writes for collection in db.collections.values())
