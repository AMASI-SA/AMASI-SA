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
        actual = row.get(key)
        if isinstance(value, dict):
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

    def find(self, query, projection):
        assert "user_id" in query or "id" in query
        include = {key for key, enabled in projection.items() if key != "_id" and enabled}
        return Cursor([{key: copy.deepcopy(value) for key, value in row.items()
                        if (key in include if include else projection.get(key) != 0)}
                       for row in self.rows if matches(row, query)])

    async def find_one(self, query, projection):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

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
        mezan_employees_v2=[{"user_id": "owner", "id": "e", "status": "active", "name": "Employee", "salary": 500}],
        mezan_suppliers_v2=[{"user_id": "owner", "id": "s", "status": "active", "company_name": "Supplier"}],
        store_drivers=[{"user_id": "owner", "id": "d", "name": "Driver"}],
        counterparties=[{"user_id": "owner", "id": "ad", "kind": "ad_account", "name": "Ad", "ad_provider": "meta"}],
        accounting_settlements_v2=[{"user_id": "owner", "provider": "salla", "balance": 200}],
        settings=[{"user_id": "owner", "shipping_companies": [{"name": "سمسا", "cost": 22, "cod_fee_percent": 5}, {"name": "مندوب الرياض"}]}])
    result = run(onboarding_domains(db, "owner"))
    assert result["identity_only"] and result["p02_status"] == "LOCKED"
    for group in ("banks", "employees", "suppliers", "store_drivers"):
        assert len(result["entities"][group]) == 1
    assert result["entities"]["ad_accounts"] == []
    assert len(result["entities"]["payment_providers"]) == 4
    serialized = json.dumps(result)
    for forbidden in ('"balance"', '"salary"', '"cost"', '"cod_fee_percent"', '"settlement_bank"', 'foreign'):
        assert forbidden not in serialized
    assert all(not collection.writes for collection in db.collections.values())


def test_legacy_collision_does_not_override_canonical_and_inactive_is_excluded():
    db = DB(mz2_financial_accounts=[
        {"user_id": "o", "id": "same", "account_type": "bank", "status": "active"},
        {"user_id": "o", "id": "hidden", "account_type": "bank", "status": "hidden"}],
        accounts=[{"user_id": "o", "id": "same", "_id": "legacy"}])
    result = run(onboarding_domains(db, "o"))
    assert [row["id"] for row in result["entities"]["banks"]] == ["same"]
    assert result["entities"]["banks"][0]["source"] == "mz2_financial_accounts"
    assert result["warnings"] == []


def test_catalog_keeps_registered_units_variants_and_options_without_financial_readiness():
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "p", "variants": [{"id": "v", "options": [{"color": "red"}]}]}],
        mezan_cost_resources_v2=[{"user_id": "o", "id": "r", "status": "active", "track_inventory": True, "unit": "meter", "category_ids": ["c"]},
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
    assert db.mz2_external_persons_v2.writes[0]["kind"] == "external_person"
    selected = run(onboarding_domains(db, "owner"))["entities"]["external_persons"]
    assert [r["id"] for r in selected] == [result["id"]]
    assert run(onboarding_domains(db, "other"))["entities"]["external_persons"] == []
    assert [key for key, collection in db.collections.items() if collection.writes] == ["mz2_external_persons_v2"]
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


def test_canonical_duplicate_is_excluded_but_legacy_collision_is_irrelevant():
    db = DB(mz2_financial_accounts=[
        {"user_id": "o", "id": "duplicate", "account_type": kind, "status": "active"}
        for kind in ("cash", "overdraft")
    ] + [{"user_id": "o", "id": "collision", "account_type": "cash", "status": "active"}],
        accounts=[{"_id": "legacy", "user_id": "o", "id": "collision"}])
    result = run(onboarding_domains(db, "o"))
    assert [row["id"] for row in result["entities"]["financial_accounts"]] == ["collision"]
    assert {row["id"] for row in result["warnings"]} == {"duplicate"}
    assert {row["code"] for row in result["warnings"]} == {"financial_account_identity_ambiguous"}


def test_ad_financial_identities_are_separate_and_never_joined_by_untyped_external_ref():
    db = DB(mz2_financial_accounts=[
        {"user_id": "o", "id": "wallet", "name": "Same name", "account_type": "ad_prepaid_wallet", "status": "active", "currency": "USD", "external_ref": "ad", "balance": 123},
        {"user_id": "o", "id": "payable", "account_type": "ad_payable", "status": "active", "currency": "SAR", "external_ref": None, "payable": 321}],
        counterparties=[{"user_id": "o", "id": "ad", "name": "Same name", "kind": "ad_account", "ad_provider": "meta"}])
    result = run(onboarding_domains(db, "o"))
    assert [row["id"] for row in result["entities"]["ad_accounts"]] == []
    accounts = result["entities"]["ad_financial_accounts"]
    assert [row["id"] for row in accounts] == ["wallet", "payable"]
    assert accounts[0]["external_ref"] == "ad" and accounts[0]["currency"] == "USD"
    assert result["entities"]["financial_accounts"] == []
    assert result["warnings"] == [
        {"code": "onboarding_native_ad_binding_dependency", "id": "wallet"},
        {"code": "onboarding_native_ad_binding_dependency", "id": "payable"}]
    for account in accounts:
        assert "balance" not in account and "payable" not in account
        assert "counterparty_id" not in account
    assert all(not collection.writes for collection in db.collections.values())


def test_inventory_v2_sources_images_options_and_strict_component_lifecycle():
    base = {"user_id": "o", "track_inventory": True, "status": "active", "kind": "material"}
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "p", "name": "Abaya", "sku": "P-SKU",
        "barcode": "123", "main_image": "https://example.test/product.jpg",
        "raw_salla": {"options": [{"name": "Color", "values": [{"name": "Black"}]}]},
        "variants": [{"id": "a", "sku": "A-SKU", "barcode": "456", "image": "https://example.test/a.jpg",
                      "selections": [{"name": "Color", "value": "Black"}]},
                     {"id": "b", "options": [{"name": "Color", "value": "Blue"}]}]},
        {"user_id": "foreign", "mezan_product_id": "private"}],
        products=[{"user_id": "o", "id": "legacy-product"}],
        mezan_cost_resources_v2=[{**base, "id": "active"}, {**base, "id": "archived", "archived": True},
            {**base, "id": "disabled", "is_active": False}, {**base, "id": "inactive", "status": "inactive"},
            {**base, "id": "service", "kind": "service"}, {**base, "id": "untracked", "track_inventory": False},
            {**base, "id": "unknown", "status": None}],
        components=[{**base, "id": "legacy-component"}],
        warehouse_locations=[{"user_id": "o", "id": "l", "warehouse_id": "w", "purpose": "permanent_storage"}])
    result = run(onboarding_inventory_catalog(db, "o"))
    product = result["products"][0]
    assert product["product_v2_id"] == "p" and product["sku"] == "P-SKU" and product["barcode"] == "123"
    assert product["options"][0]["name"] == "Color"
    assert [variant["id"] for variant in product["variants"]] == ["a", "b"]
    assert product["variants"][0]["options"] == [{"name": "Color", "value": "Black"}]
    assert product["variants"][0]["image_url"] == "https://example.test/a.jpg"
    assert product["variants"][0]["barcode"] == "456"
    assert product["variants"][1]["image_url"] == product["image_url"] == "https://example.test/product.jpg"
    assert [row["id"] for row in result["components"]] == ["active"]
    assert result["counts"] == {"products": 1, "components": 1, "locations": 1}
    assert result["locations"][0]["provenance"] == "AMBIGUOUS"
    assert not result["physical_approval_verified"]
    assert "legacy" not in json.dumps(result) and "private" not in json.dumps(result)
    assert all(not collection.writes for collection in db.collections.values())


def test_catalog_normalizes_original_option_shapes_without_inventing_variant_ids():
    db = DB(mezan_products_v2=[
        {"user_id": "o", "mezan_product_id": "mapping", "options": {"Color": "Black"},
         "variants": {"a": {"id": "a", "attributes": {"Color": "Black"}}, "unknown": {"sku": "NO-ID"}}},
        {"user_id": "o", "mezan_product_id": "malformed", "raw_salla": "invalid", "options": 5,
         "variants": True, "options_count": 1},
        {"user_id": "o", "mezan_product_id": "single", "options": {"name": "Size", "values": [{"name": "54"}]},
         "variants": [{"id": "v", "selections": "invalid"}]}])
    products = run(onboarding_inventory_catalog(db, "o"))["products"]
    assert products[0]["options"] == [{"name": "Color", "value": "Black"}]
    assert [v["id"] for v in products[0]["variants"]] == ["a"]
    assert products[0]["variants"][0]["options"] == [{"name": "Color", "value": "Black"}]
    assert products[1]["options"] == [] and products[1]["variants"] == []
    assert products[1]["variants_required"]
    assert products[2]["options"] == [{"name": "Size", "values": [{"name": "54"}]}]
    assert products[2]["variants"][0]["options"] == []


def test_inventory_customization_options_do_not_invent_stock_combinations():
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "customized",
        "options": [{"id": "engraving", "name": "Engraving", "type": "text"}],
        "options_count": 1, "variants": [], "variants_count": 0,
        "raw_salla_details": {"variants": [], "options": [{"id": "engraving", "type": "text"}]}}])
    product = run(onboarding_inventory_catalog(db, "o"))["products"][0]
    assert product["options"] and not product["variants_required"]
    assert product["variants"] == []


@pytest.mark.parametrize("patch", [
    {}, {"variants": None}, {"variants": []}, {"variants": [], "variants_count": 0},
    {"variants": [], "variants_count": "0"}, {"variants": "unavailable"},
    {"variants": [], "variants_count": 0, "raw_salla": {"options": [{"type": "text"}]}},
])
def test_inventory_options_without_a_proven_variant_source_fail_closed(patch):
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "missing",
        "options": [{"id": "color", "name": "Color"}], "options_count": 1, **patch}])
    before = copy.deepcopy(db.mezan_products_v2.rows)
    result = run(onboarding_inventory_catalog(db, "o"))
    product = result["products"][0]
    assert product["variants_required"] is True
    assert product["variants_source_missing"] is True
    assert product["variants"] == []
    assert {"code": "inventory_variant_source_missing", "product_v2_id": "missing"} in result["warnings"]
    assert db.mezan_products_v2.rows == before
    assert all(not collection.writes for collection in db.collections.values())


@pytest.mark.parametrize("raw_key, variants_key", [
    ("raw_salla", "variants"), ("raw_salla_details", "skus"),
    ("raw_salla_details", "product_variants"),
])
def test_inventory_explicit_empty_source_proves_text_customization_only(raw_key, variants_key):
    options = [{"id": "engraving", "name": "Engraving", "type": "text"}]
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "customized",
        "options": options, "options_count": 1, "variants": [], "variants_count": 0,
        raw_key: {variants_key: [], "options": options}}])
    product = run(onboarding_inventory_catalog(db, "o"))["products"][0]
    assert product["variants_required"] is False
    assert product["variants_source_available"] is True
    assert product["variants_source_missing"] is False


@pytest.mark.parametrize("patch", [
    {"options": [{"id": "color", "type": "select"}], "raw_salla_details": {"variants": []}},
    {"options": [], "options_count": 1},
    {"options": [{"id": "text", "type": "text"}], "raw_salla_details": {"options": [{"type": "text"}]}},
    {"options": [{"id": "text", "type": "text"}], "raw_salla": {"variants": []}, "raw_salla_details": {}},
])
def test_inventory_options_or_text_type_alone_cannot_prove_no_stock_combinations(patch):
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "unknown",
        "variants": [], "variants_count": 0, **patch}])
    product = run(onboarding_inventory_catalog(db, "o"))["products"][0]
    assert product["variants_required"] is True
    assert product["variants_source_missing"] is True


@pytest.mark.parametrize("patch", [{}, {"variants": [], "variants_count": 0}, {"variants": [], "variants_count": "0"}])
def test_inventory_plain_product_without_options_does_not_invent_variants(patch):
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "plain", **patch}])
    product = run(onboarding_inventory_catalog(db, "o"))["products"][0]
    assert product["variants_required"] is False
    assert product["variants"] == []


def test_inventory_missing_or_duplicate_variant_ids_are_visible_but_not_selectable():
    db = DB(mezan_products_v2=[{"user_id": "o", "mezan_product_id": "p", "variants_count": 4,
        "variants": [{"id": None, "sku": "NO-ID"}, {"id": "duplicate"},
                     {"id": "duplicate"}, {"id": "valid", "sku": "OK"}]}])
    result = run(onboarding_inventory_catalog(db, "o"))
    product = result["products"][0]
    assert product["variants_required"]
    assert [row["id"] for row in product["variants"]] == ["valid"]
    assert product["unresolved_variants_count"] == 3
    assert {"code": "inventory_variant_identity_unresolved", "product_v2_id": "p", "count": 3} in result["warnings"]
