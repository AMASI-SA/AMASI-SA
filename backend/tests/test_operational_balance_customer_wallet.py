"""A dated Salla wallet closing is one liability and never a cash movement."""
import asyncio
from copy import deepcopy

from fastapi import HTTPException
import pytest

import operational_balance_service as service
import operational_customer_wallet as wallet
from operational_balance_engine import reconcile
from operational_balance_routes import CustomerWalletSnapshot, Opening, Movement
from operational_balance_store import digest, initial
from operational_financial_position import build_financial_position


OWNER = "synthetic-wallet-owner"
MERCHANT = "synthetic-store-one"
START = "2026-11-01T00:00:00+03:00"
SOURCE = "2026-11-02T12:00:00+03:00"
NOW = "2026-11-03T12:00:00+03:00"


def payload(**changes):
    return {"request_id": "wallet-statement-1", "expected_session_scope": digest([OWNER, OWNER]),
            "merchant_id": MERCHANT, "currency": "SAR", "as_of": SOURCE,
            "remaining_balance": "75.00", "source_reference": "Synthetic Salla wallet statement", **changes}


class BindingDB:
    def __init__(self):
        self.rows = [{"user_id": OWNER, "store_id": MERCHANT}]

    def __getitem__(self, name):
        assert name == "salla_integrations"
        return self

    def find(self, query, projection):
        assert query == {"user_id": OWNER}
        assert projection == {"_id": 0, "user_id": 1, "store_id": 1, "currency": 1, "store_currency": 1}
        return self

    async def to_list(self, limit):
        assert limit == 2
        return deepcopy(self.rows[:limit])


@pytest.fixture
def setup(monkeypatch):
    state = initial(OWNER)
    state.update(status="active", started_at=START)
    state["engine"] = {"as_of": NOW, "obligations": {}, "facts": {}, "issues": []}
    db = BindingDB()

    async def mutate(db, owner, change):
        assert owner == OWNER
        updated = deepcopy(state)
        result = await change(updated)
        state.clear()
        state.update(updated)
        return result

    async def read(db, owner):
        assert owner == OWNER
        return deepcopy(state)

    async def accounting_inactive(db, owner):
        assert owner == OWNER

    monkeypatch.setattr(wallet, "mutate", mutate)
    monkeypatch.setattr(wallet, "read", read)
    monkeypatch.setattr(wallet, "now", lambda: NOW)
    monkeypatch.setattr(service, "accounting_inactive", accounting_inactive)
    return state, db


def save(db, **changes):
    return asyncio.run(wallet.save(db, OWNER, OWNER, payload(**changes)))


def add_opening(state, identity, kind, amount, *, direction="for_us", currency="SAR"):
    state["openings"][identity] = {"id": identity, "party_id": identity, "party_type": kind,
                                   "currency": currency, "amount": amount, "direction": direction, "name": identity}


def observed_wallet_use(state):
    state["engine"]["customer_tender_coverage"] = {
        "by_currency": {"SAR": [{"method": "wallet", "name": "محفظة العميل", "orders": 1,
                                  "paid": "15.00", "remaining": "0.00", "incomplete_orders": 0}]},
        "sar": {"paid": "15.00", "known_paid": "15.00", "complete": True}}


def test_closing_projects_one_payable_and_preserves_cash_provider_ads_and_tender_value(setup):
    state, db = setup
    add_opening(state, "bank", "bank", "1000.00")
    add_opening(state, "provider", "provider", "100.00")
    add_opening(state, "ads", "ad_account", "40.00")
    observed_wallet_use(state)
    source = deepcopy(db.rows)
    save(db)
    before = deepcopy(state)
    report = service.report(state)
    assert report["summary"]["payable"] == "75.00"
    assert report["summary"]["actual_liquidity"] == "1000.00"
    assert report["details"]["customer_tender_coverage"]["sar"]["paid"] == "15.00"
    liability = next(row for row in report["parties"] if row["party_type"] == "customer_wallet")
    assert liability["outstanding_payable"] == "75.00" and liability["outstanding_receivable"] == "0.00"
    assert liability["read_only"] is True and liability["source_as_of"] == "2026-11-02T09:00:00+00:00"
    assert next(row for row in report["parties"] if row["party_type"] == "provider")["outstanding_receivable"] == "100.00"
    assert next(row for row in report["parties"] if row["party_type"] == "ad_account")["ad_wallet_balance"] == "40.00"
    result = build_financial_position(state)["currencies"]["SAR"]
    assert result["debt_totals"] == {"receivable": "100.00", "payable": "75.00"}
    assert result["metrics"]["monetary_position"] == "1025.00"
    table = next(row for row in result["tables"] if row["id"] == "customer_wallets")
    assert table["rows"][0]["remaining"] == "75.00" and table["show_totals"] is False
    assert table["rows"][0]["as_of"] == "2026-11-02T09:00:00+00:00"
    tender_table = next(row for row in result["tables"] if row["id"] == "customer_other_tenders")
    assert tender_table["rows"][0]["name"] == "المستخدم من أرصدة محافظ العملاء"
    assert tender_table["rows"][0]["paid"] == "15.00"
    assert report == service.report(state) and state == before and db.rows == source
    assert not state["movements"] and not state["engine"]["obligations"]
    refreshed = reconcile(state, {}, NOW)
    assert refreshed[wallet.SNAPSHOTS] == state[wallet.SNAPSHOTS]
    assert service.report(refreshed)["summary"]["payable"] == "75.00"


def test_same_request_replays_and_newer_closing_replaces_including_verified_zero(setup):
    state, db = setup
    first = save(db)
    assert save(db) == first and len(state["audit"]) == 1
    save(db, request_id="wallet-statement-2", as_of=NOW, remaining_balance="0")
    assert service.report(state)["summary"]["payable"] == "0.00"
    assert wallet.project(state)[0]["SAR"]["remaining"] == "0.00"
    assert wallet.project(state)[0]["SAR"]["complete"] is True
    assert len(state["audit"]) == 2 and len(state[wallet.SNAPSHOTS]) == 1
    assert save(db) == first
    assert service.report(state)["summary"]["payable"] == "0.00"
    assert len(state["audit"]) == 2


def test_optional_verified_flow_reconciles_and_remains_evidence_only(setup):
    state, db = setup
    flow = {"period_start": START, "opening_balance": "60", "added": "32", "used": "15",
            "other_deductions": "2", "opening_verified": True}
    result = save(db, flow=flow)
    assert result["flow"]["opening_balance"] == "60.00"
    assert service.report(state)["summary"]["payable"] == "75.00"
    assert state["openings"] == {} and state["movements"] == []


@pytest.mark.parametrize("changes", [
    {"remaining_balance": "-1"}, {"remaining_balance": "NaN"}, {"remaining_balance": "1e3"},
    {"remaining_balance": "1.001"}, {"remaining_balance": True}, {"remaining_balance": None},
    {"as_of": "2026-11-02T12:00:00"}, {"as_of": "2026-11-04T12:00:00+03:00"},
    {"as_of": "2026-10-31T12:00:00+03:00"}, {"source_reference": ""}, {"currency": ""},
    {"merchant_id": "foreign-store"}, {"source_hash": "bad"}, {"expected_session_scope": "0" * 64},
    {"flow": {"period_start": START, "opening_balance": "60", "added": "30", "used": "15", "other_deductions": "0", "opening_verified": False}},
    {"flow": {"period_start": START, "opening_balance": "60", "added": "30", "used": "16", "other_deductions": "0", "opening_verified": True}},
    {"flow": {"period_start": NOW, "opening_balance": "60", "added": "30", "used": "15", "other_deductions": "0", "opening_verified": True}},
])
def test_invalid_or_unbound_evidence_is_rejected_atomically(setup, changes):
    state, db = setup
    before = deepcopy(state)
    with pytest.raises(HTTPException):
        save(db, **changes)
    assert state == before


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign_owner", "currency"])
def test_binding_must_be_unique_and_owner_scoped(setup, change):
    state, db = setup
    if change == "missing":
        db.rows = []
    elif change == "duplicate":
        db.rows.append(deepcopy(db.rows[0]))
    elif change == "foreign_owner":
        db.rows[0]["user_id"] = "other-owner"
    else:
        db.rows[0]["currency"] = "USD"
    before = deepcopy(state)
    with pytest.raises(HTTPException):
        save(db)
    assert state == before


@pytest.mark.parametrize("change", ["staff", "draft", "frozen", "state_owner"])
def test_owner_active_state_required(setup, change):
    state, db = setup
    if change in {"draft", "frozen"}:
        state["status"] = change
    elif change == "state_owner":
        state["owner_id"] = "other-owner"
    before = deepcopy(state)
    with pytest.raises(HTTPException):
        asyncio.run(wallet.save(db, OWNER, "staff" if change == "staff" else OWNER, payload()))
    assert state == before


@pytest.mark.parametrize("changes", [
    {"request_id": "older-statement", "as_of": "2026-11-01T18:00:00+03:00"},
    {"request_id": "conflicting-statement", "remaining_balance": "74.00"},
    {"remaining_balance": "74.00"},
])
def test_backward_same_timestamp_and_request_conflicts_do_not_replace_snapshot(setup, changes):
    state, db = setup
    save(db)
    before = deepcopy(state)
    with pytest.raises(HTTPException):
        save(db, **changes)
    assert state == before


def test_context_and_explicit_report_cutoff_preserve_source_date(setup):
    state, db = setup
    context = asyncio.run(wallet.context(db, OWNER, OWNER))
    assert context["merchant_id"] == MERCHANT and context["can_manage"]
    assert context["session_scope"] == digest([OWNER, OWNER]) and context["balances"] == {}
    save(db)
    historical = service.report(state, as_of=START)
    assert historical["details"]["customer_wallet_balances"]["SAR"]["remaining"] is None
    assert any(row["code"] == "customer_wallet_balance_unavailable" for row in historical["issues"])
    current = asyncio.run(wallet.context(db, OWNER, OWNER))["balances"]["SAR"]
    assert current["remaining"] == "75.00" and current["as_of"] == "2026-11-02T09:00:00+00:00"
    assert service.report(state, as_of=SOURCE)["summary"]["payable"] == "75.00"


@pytest.mark.parametrize("cutoff", [START, None])
def test_default_report_requires_a_cutoff_covering_the_saved_closing(setup, cutoff):
    state, db = setup
    save(db)
    state["engine"]["as_of"] = cutoff
    report = service.report(state)
    assert report["details"]["customer_wallet_balances"]["SAR"]["remaining"] is None
    assert any(row["code"] == "customer_wallet_balance_unavailable" for row in report["issues"])
    position = build_financial_position(state)["currencies"]["SAR"]
    assert position["debt_totals"]["payable"] is None
    assert position["metrics"]["monetary_position"] is None
    assert asyncio.run(wallet.context(db, OWNER, OWNER))["balances"]["SAR"]["remaining"] == "75.00"
    state["engine"]["as_of"] = SOURCE
    assert service.report(state)["summary"]["payable"] == "75.00"


def test_foreign_order_tender_does_not_invent_a_foreign_wallet_ledger(setup):
    state, db = setup
    observed_wallet_use(state)
    coverage = state["engine"]["customer_tender_coverage"]
    coverage["by_currency"]["USD"] = coverage["by_currency"].pop("SAR")
    save(db)
    report = service.report(state)
    assert set(report["details"]["customer_wallet_balances"]) == {"SAR"}
    assert not any(row["code"].startswith("customer_wallet_") for row in report["issues"])
    assert [row["currency"] for row in report["parties"] if row["party_type"] == "customer_wallet"] == ["SAR"]
    assert report["summary"]["payable"] == "75.00"


def test_missing_closing_and_invalid_saved_evidence_are_unavailable(setup):
    state, db = setup
    observed_wallet_use(state)
    report = service.report(state)
    assert report["details"]["customer_wallet_balances"] == {}
    assert any(row["code"] == "customer_wallet_balance_missing" for row in report["issues"])
    assert build_financial_position(state)["currencies"]["SAR"]["metrics"]["monetary_position"] is None
    save(db)
    state[wallet.SNAPSHOTS]["SAR"]["remaining_balance"] = "900.00"
    assert wallet.project(state)[0]["SAR"]["remaining"] is None
    result = build_financial_position(state)["currencies"]["SAR"]
    assert result["debt_totals"]["payable"] is None and result["metrics"]["monetary_position"] is None


def test_absent_wallet_statement_preserves_known_money_without_inventing_a_zero_or_order_currency_ledger(setup):
    state, _ = setup
    add_opening(state, "bank", "bank", "100.00")
    observed_wallet_use(state)
    coverage = state["engine"]["customer_tender_coverage"]
    coverage["by_currency"]["USD"] = coverage["by_currency"].pop("SAR")
    source = service.report(state)
    assert source["details"]["customer_wallet_balances"] == {}
    assert not any(row["party_type"] == "customer_wallet" for row in source["parties"])
    result = build_financial_position(state)["currencies"]["SAR"]
    assert result["debt_totals"]["payable"] is None
    assert result["known_debt_totals"]["payable"] == "0.00"
    assert result["metrics"]["liquidity"] == result["metrics"]["known_monetary_position"] == "100.00"
    assert result["metrics"]["monetary_position"] is None
    payable = next(row for row in result["current_rows"] if row["id"] == "debts_payable")
    assert payable["amount"] is None and payable["known_subtotal"] == "0.00"
    assert "كشف سلة موثق" in payable["reason"]


def test_currencies_are_separate_and_snapshot_kind_is_never_a_money_writer(setup):
    state, db = setup
    save(db)
    save(db, request_id="foreign-currency-statement", currency="USD", remaining_balance="12.00")
    report = service.report(state)
    assert report["summaries"]["SAR"]["payable"] == "75.00"
    assert report["summaries"]["USD"]["payable"] == "12.00"
    assert "customer_wallet" not in service.KINDS
    for model in (Opening, Movement):
        assert "customer_wallet" not in model.model_fields["party_type"].annotation.__args__
    valid = CustomerWalletSnapshot.model_validate(payload()).model_dump()
    assert valid["merchant_id"] == MERCHANT and valid["flow"] is None
