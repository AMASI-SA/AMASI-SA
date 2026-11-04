"""Current advertising valuation preserves native balances and paid bank fees."""
from copy import deepcopy
from datetime import datetime

import pytest

from operational_ad_balance_sar import COLLECTION, saved_basis
from operational_balance_engine import reconcile
from operational_balance_service import report
from operational_financial_position import build_financial_position
from backend.tests.test_operational_balance_financial_position import state_for, opening, table, NOW


def configured_basis(**changes):
    binding = {"user_id": "owner", "_id": "ads"}
    account = {"mezan_integration_account_id": "account", "currency": "USD",
               "provider": "snapchat_ads", "external_account_id": "external"}
    setting = {"user_id": "owner", "mezan_integration_account_id": "account",
               "provider": "snapchat_ads", "external_account_id": "external",
               "native_currency": "USD", "exchange_rate_to_sar": "3.75",
               "bank_commission_pct": "2", "apply_bank_commission": True,
               "updated_at": "2026-10-30T00:00:00+00:00", **changes}
    return saved_basis(binding, account, [setting], datetime.fromisoformat(NOW))


def ad_state(amount="100.00", direction="for_party", **settings):
    state = state_for()
    opening(state, "ad-opening", "ad_account", amount, direction, party="ads", currency="USD")
    state["engine"]["ad_balance_sar_basis"] = {"ads": configured_basis(**settings)}
    return state


def test_usd_debt_is_in_sar_with_bank_fee_and_vat_on_fee_only():
    state = ad_state()
    before = deepcopy(state)
    result = build_financial_position(state)
    assert result["currency_order"] == ["SAR"]
    conversion = table(result, "ads_currency_conversion")
    row = conversion["rows"][0]
    assert {key: row[key] for key in ("amount_usd", "amount_sar", "bank_commission", "bank_commission_vat", "receivable", "payable")} == {
        "amount_usd": "100.00", "amount_sar": "375.00", "bank_commission": "7.50",
        "bank_commission_vat": "1.13", "receivable": "0.00", "payable": "383.63"}
    assert table(result, "ads")["totals"]["payable"] == "383.63"
    assert result["currencies"]["SAR"]["debt_totals"]["payable"] == "383.63"
    ids = [item["id"] for item in result["currencies"]["SAR"]["tables"]]
    assert ids.index("ads_currency_conversion") + 1 == ids.index("ads")
    assert state == before
    assert result == build_financial_position(state)
    assert report(state)["parties"][0]["currency"] == "USD"


def test_paid_principal_and_previously_recorded_bank_fee_are_not_charged_again():
    state = ad_state()
    opening(state, "bank", "bank", "1000.00")
    state["movements"] = [{"id": "payment", "kind": "settlement", "direction": "outgoing",
        "party_type": "ad_account", "party_id": "ads", "currency": "USD", "amount": "40.00",
        "bank_kind": "bank", "bank_id": "bank", "bank_currency": "SAR", "bank_amount": "153.45",
        "bank_net_amount": "150.00", "bank_fee_amount": "3.45",
        "allocations": [{"obligation_id": "ad-opening", "amount": "40.00"}]}]
    result = build_financial_position(state)
    conversion = table(result, "ads_currency_conversion")["rows"][0]
    assert conversion["amount_usd"] == "60.00"
    assert conversion["amount_sar"] == "225.00"
    assert conversion["bank_commission"] == "4.50"
    assert conversion["bank_commission_vat"] == "0.68"
    assert conversion["payable"] == "230.18"
    assert result["currencies"]["SAR"]["metrics"]["liquidity"] == "846.55"


def test_prepaid_usd_credit_is_converted_once_without_new_bank_fees():
    state = ad_state(direction="for_us")
    result = build_financial_position(state)
    conversion = table(result, "ads_currency_conversion")["rows"][0]
    assert conversion["amount_usd"] == "-100.00"
    assert conversion["amount_sar"] == "-375.00"
    assert conversion["bank_commission"] == conversion["bank_commission_vat"] == "0.00"
    assert conversion["receivable"] == "375.00" and conversion["payable"] == "0.00"
    assert result["currencies"]["SAR"]["metrics"]["prepaid_ad_credit"] == "375.00"
    assert result["currencies"]["SAR"]["metrics"]["displayed_position"] == "375.00"


def test_disabled_commission_keeps_both_fee_and_vat_zero():
    result = build_financial_position(ad_state(apply_bank_commission=False))
    row = table(result, "ads_currency_conversion")["rows"][0]
    assert row["bank_commission"] == row["bank_commission_vat"] == "0.00"
    assert row["payable"] == "375.00"


@pytest.mark.parametrize("changes", [
    {"user_id": "another-owner"}, {"provider": "meta_ads"},
    {"external_account_id": "another-account"}, {"native_currency": "SAR"},
    {"exchange_rate_to_sar": "NaN"}, {"exchange_rate_to_sar": 0},
    {"bank_commission_pct": -1}, {"bank_commission_pct": True},
    {"apply_bank_commission": "true"}, {"updated_at": "2026-11-05T00:00:00Z"},
    {"updated_at": "2026-10-30T00:00:00"},
])
def test_unmatched_or_invalid_settings_cannot_value_an_account(changes):
    assert configured_basis(**changes) is None


def test_missing_or_removed_settings_leave_native_evidence_and_missing_sar_issue():
    state = ad_state()
    state["engine"]["ad_balance_sar_basis"] = {}
    result = build_financial_position(state)
    assert table(result, "ads", "USD")["totals"]["payable"] == "100.00"
    assert any(row["code"] == "ad_balance_sar_unavailable" for row in result["issues"])
    assert all(item["id"] != "ads_currency_conversion" for item in result["currencies"]["USD"]["tables"])


def test_worker_refresh_replaces_saved_terms_and_clears_removed_configuration():
    state = ad_state()
    changed = configured_basis(exchange_rate_to_sar="3.80")
    updated = reconcile(state, {"ad_balance_sar_basis": {"ads": changed}}, NOW)
    assert updated["engine"]["ad_balance_sar_basis"]["ads"]["source"] == COLLECTION
    assert table(build_financial_position(updated), "ads_currency_conversion")["rows"][0]["amount_sar"] == "380.00"
    removed = reconcile(updated, {"ad_balance_sar_basis": {}}, NOW)
    assert removed["engine"]["ad_balance_sar_basis"] == {}
    assert state["engine"]["ad_balance_sar_basis"]["ads"]["exchange_rate_to_sar"] == "3.75"
