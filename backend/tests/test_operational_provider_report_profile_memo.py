"""A saved fee profile is validated once per report scope, never across reports."""
from copy import deepcopy
import json

import pytest

import operational_provider_summary as summary
import operational_provider_statement_fees as statement_fees
from operational_financial_position import build_financial_position
from test_operational_balance_statement_fees import source_profile, tabby_state


VIEWS = ("summary", "providers", "couriers", "drivers", "employees", "accounts",
         "suppliers", "advertising", "other", "expected", "forecast", "all")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def repeated_state(count):
    state, _ = tabby_state()
    engine = state["engine"]
    claim = engine["obligations"].pop("provider:one")
    fact = engine["facts"].pop("capture:receipt")
    order = engine["provider_fee_inputs"]["orders"].pop("one")
    for index in range(count):
        oid, receipt = f"synthetic-{index}", f"receipt-{index}"
        identity = "provider:" + oid
        engine["obligations"][identity] = {**deepcopy(claim), "id": identity}
        engine["facts"]["capture:" + receipt] = {**deepcopy(fact), "order_id": oid}
        saved = deepcopy(order)
        saved["id"] = oid
        saved["payment"]["captures"][0]["id"] = receipt
        engine["provider_fee_inputs"]["orders"][oid] = saved
    return state


def scoped_state():
    state = repeated_state(12)
    inputs = state["engine"]["provider_fee_inputs"]
    inputs["average_profiles"] = [source_profile(state, account=account, source=source)
        for account, source in (("account-a", "a"), ("account-b", "b"), ("account-c", "c"))]
    for index, order in enumerate(inputs["orders"].values()):
        order["payment"]["merchant_account"] = ("account-a", "account-b", "account-c")[index % 3]
    # Distractor profiles must never make another provider or native currency
    # ambiguous, and must not be reused for an otherwise matching account.
    inputs["average_profiles"].extend([
        source_profile(state, provider="tamara", account="account-a", source="d"),
        source_profile(state, currency="USD", account="account-a", source="e"),
    ])
    return state


def test_repeated_profiles_have_bounded_validation_without_cross_report_cache(monkeypatch):
    state = repeated_state(60)
    before = deepcopy(state)
    calls = []
    validate = statement_fees._valid_average_profile

    def observed(profile, owner):
        calls.append((profile["profile_id"], owner))
        return validate(profile, owner)

    monkeypatch.setattr(statement_fees, "_valid_average_profile", observed)
    first = build_financial_position(state, view="employees")
    assert 1 <= len(calls) <= 4
    first_count = len(calls)
    assert build_financial_position(state, view="employees") == first
    assert len(calls) == first_count * 2
    assert state == before


@pytest.mark.parametrize("view", VIEWS)
def test_each_view_is_byte_identical_to_uncached_profile_selection(monkeypatch, view):
    state = scoped_state()
    before = deepcopy(state)
    with monkeypatch.context() as uncached:
        uncached.setattr(summary, "_REPORT_PROFILE_SCOPE_LIMIT", 0)
        expected = build_financial_position(state, view=view)
    actual = build_financial_position(state, view=view)
    assert encoded(actual) == encoded(expected)
    assert state == before


def test_profiles_are_not_shared_across_provider_or_native_currency(monkeypatch):
    state = repeated_state(9)
    engine, scopes = state["engine"], (("tabby", "SAR"), ("tamara", "SAR"), ("tabby", "USD"))
    inputs = engine["provider_fee_inputs"]
    prototype = inputs["policies"][0]
    inputs["policies"] = [{**deepcopy(prototype), "id": "policy-" + str(index),
                           "provider": provider, "currency": currency}
        for index, (provider, currency) in enumerate(scopes)]
    inputs["average_profiles"] = [source_profile(state, provider=provider, currency=currency,
        account="same-account", source=source) for (provider, currency), source in zip(scopes, "abc")]
    for index, (oid, order) in enumerate(inputs["orders"].items()):
        provider, currency = scopes[index % 3]
        engine["obligations"]["provider:" + oid].update(party_id=provider, currency=currency)
        engine["facts"]["capture:" + order["payment"]["captures"][0]["id"]]["provider"] = provider
        order["currency"] = currency
        order["payment"].update(provider=provider, currency=currency, merchant_account="same-account")
    before = deepcopy(state)
    with monkeypatch.context() as uncached:
        uncached.setattr(summary, "_REPORT_PROFILE_SCOPE_LIMIT", 0)
        expected = build_financial_position(state, view="providers")
    calls = []
    select = summary.select_statement_average_profile

    def observed(state, provider, currency, *, payment=None):
        result = select(state, provider, currency, payment=payment)
        calls.append((provider, currency, result["profile_id"]))
        return result

    monkeypatch.setattr(summary, "select_statement_average_profile", observed)
    actual = build_financial_position(state, view="providers")
    assert encoded(actual) == encoded(expected)
    assert len(calls) == 3
    assert {(provider, currency) for provider, currency, _ in calls} == set(scopes)
    assert len({profile for _, _, profile in calls}) == 3
    assert state == before


@pytest.mark.parametrize("change", [
    "wrong_owner", "wrong_provider", "wrong_currency", "wrong_account", "bad_rate",
    "bad_hash", "nonfinite_tax", "duplicate_scope", "invalid_shape", "missing_profile",
])
def test_invalid_or_ambiguous_saved_profile_retains_unknown_fees(monkeypatch, change):
    state = repeated_state(5)
    inputs = state["engine"]["provider_fee_inputs"]
    profile = inputs["average_profiles"][0]
    fields = {"wrong_owner": ("owner_id", "another-owner"),
              "wrong_provider": ("provider", "tamara"),
              "wrong_currency": ("native_currency", "USD"),
              "wrong_account": ("merchant_account", "another-account"),
              "bad_rate": ("rate_percent", "999.00000000"),
              "bad_hash": ("profile_id", "0" * 64),
              "nonfinite_tax": ("fee_vat_total", "NaN")}
    if change in fields:
        key, value = fields[change]
        profile[key] = value
    elif change == "duplicate_scope":
        inputs["average_profiles"].append(deepcopy(profile))
    elif change == "invalid_shape":
        inputs["average_profiles"] = [None, "invalid", []]
    else:
        inputs["average_profiles"] = []
    before = deepcopy(state)
    with monkeypatch.context() as uncached:
        uncached.setattr(summary, "_REPORT_PROFILE_SCOPE_LIMIT", 0)
        expected = build_financial_position(state, view="providers")
    actual = build_financial_position(state, view="providers")
    assert encoded(actual) == encoded(expected)
    row = next(table for table in actual["currencies"]["SAR"]["tables"] if table["id"] == "providers")["rows"][0]
    assert row["fees"] is None and row["net"] is None
    assert state == before


@pytest.mark.parametrize("account", [None, "", "account-a", "account-missing", 1, True, 1.0, {}, []])
def test_account_types_keep_original_selection_semantics(monkeypatch, account):
    state = scoped_state()
    for order in state["engine"]["provider_fee_inputs"]["orders"].values():
        order["payment"]["merchant_account"] = deepcopy(account)
    before = deepcopy(state)
    with monkeypatch.context() as uncached:
        uncached.setattr(summary, "_REPORT_PROFILE_SCOPE_LIMIT", 0)
        expected = build_financial_position(state, view="providers")
    assert encoded(build_financial_position(state, view="providers")) == encoded(expected)
    assert state == before


def test_scope_limit_falls_back_without_discarding_other_accounts(monkeypatch):
    state = scoped_state()
    with monkeypatch.context() as uncached:
        uncached.setattr(summary, "_REPORT_PROFILE_SCOPE_LIMIT", 0)
        expected = build_financial_position(state, view="providers")
    calls = []
    select = summary.select_statement_average_profile

    def observed(state, provider, currency, *, payment=None):
        calls.append(payment.get("merchant_account"))
        return select(state, provider, currency, payment=payment)

    monkeypatch.setattr(summary, "_REPORT_PROFILE_SCOPE_LIMIT", 1)
    monkeypatch.setattr(summary, "select_statement_average_profile", observed)
    actual = build_financial_position(state, view="providers")
    assert encoded(actual) == encoded(expected)
    assert calls.count("account-a") == 1
    assert calls.count("account-b") == calls.count("account-c") == 4


def test_changed_owner_or_profile_is_revalidated_by_next_report():
    state = repeated_state(6)
    good = build_financial_position(state, view="providers")
    state["engine"]["provider_fee_inputs"]["average_profiles"][0]["owner_id"] = "another-owner"
    changed = build_financial_position(state, view="providers")
    table = lambda result: next(table for table in result["currencies"]["SAR"]["tables"] if table["id"] == "providers")
    assert table(good)["rows"][0]["fees"] is not None
    assert table(changed)["rows"][0]["fees"] is None
