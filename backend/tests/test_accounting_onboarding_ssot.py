"""Synthetic source-selection tests; no network, ledger, or database writes."""
import pytest
from fastapi import HTTPException
from accounting_onboarding_identities import identities, verify_mappings
from accounting_onboarding_source_gaps import onboarding_source_gaps
from tests.test_accounting_onboarding_domains import DB, run


def test_v2_people_only_and_employee_without_salary_contract_remains_selectable():
    db = DB(mezan_employees_v2=[{"user_id": "o", "id": "e", "name": "New"}, {"user_id": "other", "id": "foreign"}],
            operating_salaries=[{"user_id": "o", "id": "legacy-employee", "category": "employee"}],
            mezan_suppliers_v2=[{"user_id": "o", "id": "s", "company_name": "V2 supplier"}],
            suppliers=[{"user_id": "o", "id": "legacy-supplier"}],
            counterparties=[{"user_id": "o", "id": "legacy-supplier", "kind": "supplier"}, {"user_id": "o", "id": "p", "kind": "general"}],
            store_drivers=[{"user_id": "o", "id": "d"}])
    employees = run(identities(db, "o", "employee"))
    assert [row["id"] for row in employees] == ["e"]
    assert employees[0]["salary_contract_status"] == "missing"
    assert [row["id"] for row in run(identities(db, "o", "supplier"))] == ["s"]
    assert [row["id"] for row in run(identities(db, "o", "external_person"))] == ["p"]
    assert [row["id"] for row in run(identities(db, "o", "store_driver"))] == ["d"]
    assert all(not col.writes for col in db.collections.values())


def test_couriers_include_operational_mz2_without_p02_policy_not_legacy_settings():
    db = DB(mz2_salla_order_evidence=[{"user_id": "o", "shipping_company": "سمسا"}, {"user_id": "o", "shipping_company": "Aramex", "conflict": True}],
            mz2_shipping_rate_policies=[{"_id": "o", "user_id": "o", "versions": [{"id": "policy", "courier_id": "imile", "verification_status": "approved"}, {"id": "draft", "courier_id": "zajil", "verification_status": "draft"}]}],
            settings=[{"user_id": "o", "shipping_companies": [{"name": "legacy-only"}]}])
    choices = {row["id"]: row for row in run(identities(db, "o", "courier"))}
    assert set(choices) == {"smsa", "imile", "zajil"}
    assert choices["smsa"]["contract_state"] == "contract_incomplete"
    assert choices["imile"]["approved_policy_id"] == "policy"
    assert choices["zajil"]["contract_state"] == "rates_incomplete"
    assert all(not col.writes for col in db.collections.values())


def test_bank_binding_preserved_with_explicit_legacy_gap_and_no_fallback():
    db = DB(mz2_financial_accounts=[{"user_id": "o", "id": "bank", "name": "Canonical", "account_type": "bank", "currency": "SAR", "status": "active"}],
            accounts=[{"user_id": "o", "id": "old", "name": "Legacy", "account_type": "bank"}],
            accounting_provider_bank_bindings_v2=[{"user_id": "o", "provider": "tabby", "bank_account_id": "bank"}, {"user_id": "o", "provider": "tamara", "bank_account_id": "old"}])
    choices = {row["id"]: row for row in run(identities(db, "o", "provider"))}
    assert choices["tabby"]["binding_status"] == "valid"
    assert choices["tabby"]["bank_name"] == "Canonical"
    assert choices["tamara"]["bank_account_id"] == "old"
    assert choices["tamara"]["binding_status"] == "noncanonical"
    assert [row["id"] for row in run(identities(db, "o", "bank"))] == ["bank"]
    diagnostic = run(onboarding_source_gaps(db, "o"))
    assert next(row for row in diagnostic["items"] if row["id"] == "old")["selectable"] is False
    assert diagnostic["fee_capability"]["status"] == "GAP"
    assert not diagnostic["fee_capability"]["writer_available"]
    assert all(not col.writes for col in db.collections.values())


def test_ad_bindings_currency_known_unknown_conflict_without_sar_default():
    db = DB(counterparties=[{"user_id": "o", "id": "ad", "kind": "ad_account"}, {"user_id": "o", "id": "empty", "kind": "ad_account"}],
            mz2_financial_accounts=[{"user_id": "o", "id": key, "external_ref": "ad", "account_type": kind, "currency": "USD", "status": "active"} for key,kind in (("wallet", "ad_prepaid_wallet"), ("payable", "ad_payable"))])
    choices = {row["id"]: row for row in run(identities(db, "o", "ad_account"))}
    assert choices["ad"]["currency"] == "USD"
    assert choices["ad"]["prepaid_wallet_account_id"] == "wallet"
    assert choices["ad"]["payable_account_id"] == "payable"
    assert choices["empty"]["currency"] is None
    assert "ad_currency_unknown" in choices["empty"]["binding_gaps"]
    db.mz2_financial_accounts.rows[0]["currency"] = "EUR"
    assert "ad_currency_conflict" in run(identities(db, "o", "ad_account"))[0]["binding_gaps"]


def test_all_three_employee_balances_and_both_supplier_balances_use_v2_ids():
    db = DB(mezan_employees_v2=[{"user_id": "o", "id": "e"}], mezan_suppliers_v2=[{"user_id": "o", "id": "s"}])
    lines = [{"category": category, "entity_type": kind, "entity_id": key, "financial_account_id": None} for kind,key,categories in (("employee", "e", ("employee_salary_payable", "employee_advance", "employee_custody")), ("supplier", "s", ("supplier_payable", "supplier_advance"))) for category in categories]
    assert len(run(verify_mappings(db, "o", {"lines": lines}, []))) == 5
    with pytest.raises(HTTPException) as error:
        run(verify_mappings(db, "o", {"lines": lines[:-1]}, []))
    assert error.value.detail["code"] == "onboarding_entity_balance_required"
    assert "suppliers" not in db.collections and "operating_salaries" not in db.collections
    assert all(not col.writes for col in db.collections.values())

