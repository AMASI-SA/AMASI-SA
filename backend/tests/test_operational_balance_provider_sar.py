"""A provider payment has one underlying claim and one SAR reporting basis."""
from copy import deepcopy
from decimal import Decimal

from operational_balance_service import report
from operational_provider_balance_sar import refresh_basis
from operational_financial_position import build_financial_position
from test_operational_balance_financial_position import NOW, state_for, opening, obligation, table

ID = "provider:foreign"
PARTY = "tabby"


def fx(native="12.00", sar="147.55", basis="salla_list_paid"):
    return {"currency": "KWD", "native": {PARTY: native}, "shares": {PARTY: sar}, "paid": sar, "basis": basis}


def sample(*, sar="147.55", basis="salla_list_paid"):
    s = state_for()
    opening(s, "baseline", "provider", "500.00", party=PARTY)
    opening(s, "SAR bank", "bank", "90.00")
    obligation(s, ID, "provider", "provider", "12.00", currency="KWD", direction="receivable", party=PARTY)
    s["engine"]["facts"]["provider_order_balance:foreign"] = {
        "owner_id": s["owner_id"], "provider": PARTY, "order_id": "foreign", "order_number": "r-foreign",
        "currency": "KWD", "gross": "12.00", "paid": "12.00", "remaining": "0.00",
    }
    if sar:
        bind(s, fx(sar=sar, basis=basis))
    return s


def bind(s, payment):
    refresh_basis(s, [{"id":"foreign", "order_number": "r-foreign", "currency":"KWD",
                       "payment":{"provider": PARTY}, "customer_payment_sar": payment}], s["engine"]["issues"])


def payout(s, amount="5.00", currency="KWD", key=ID, fee="0.00"):
    s["movements"].append({"id": "payment" + str(len(s["movements"])), "kind":"settlement", "direction":"incoming",
        "party_type": "provider", "party_id": PARTY, "currency": currency, "amount": amount, "actual_fee_amount": fee,
        "bank_kind": "bank", "bank_id": "SAR bank" if currency == "SAR" else "KWD bank",
        "allocations": [{"obligation_id": key, "amount": str(Decimal(amount) + Decimal(fee))}] if key else []})


def test_distinct_sar_claim_in_both_actual_balance_tables_and_no_new_bank_money():
    s = sample()
    payout(s, "50.00", "SAR", "baseline")
    payout(s, "4.80", fee="0.20")
    before = deepcopy(s)
    native = report(s)
    assert native["summaries"]["KWD"]["receivable"] == "7.00"
    view = report(s, provider_balances_sar=True)
    # 147.55 * (5/12) = 61.48 applied at the original per-order basis.
    item = next(r for r in view["obligations"] if r["id"] == ID)
    assert (item["currency"], item["confirmed"], item["settled"], item["outstanding"]) == ("SAR","147.55","61.48","86.07")
    assert item["native_balance"]["outstanding"] == "7.00"
    assert view["summaries"]["SAR"]["receivable"] == "536.07"
    assert view["summaries"]["SAR"]["settled"] == "111.48"
    assert view["summaries"]["KWD"]["receivable"] == "0.00"
    assert view["summaries"]["SAR"]["actual_liquidity"] == "140.00"
    assert view["summaries"]["KWD"]["actual_liquidity"] == "4.80"
    assert next(p for p in view["details"]["provider_paid_totals"]["KWD"] if p["provider"] == PARTY)["paid"] == "12.00"
    position = build_financial_position(s)
    # FX proves the gross claim, not the unpriced fee on its remaining share.
    # The saved ledger above retains 536.07; known net keeps opening 500 minus
    # its actual settlement 50 and does not call the unknown-fee 86.07 net cash.
    assert position["currencies"]["SAR"]["debt_totals"]["receivable"] == "450.00"
    provider, = table(position, "providers")["rows"]
    assert provider["known_gross"] == "147.55" and provider["known_fees"] == "2.46"
    assert provider["known_net"] == "450.00" and provider["complete"] is False
    assert view["summaries"]["SAR"]["receivable"] == "536.07" and item["native_balance"]["outstanding"] == "7.00"
    assert any(r["posted"] == "147.55" for r in table(position, "balance_origin")["rows"])
    assert view == report(s, provider_balances_sar=True) and s == before


def test_legacy_missing_proof_never_labels_native_amount_as_riyals():
    s = sample(sar=None)
    value = report(s, provider_balances_sar=True)
    assert value["summaries"]["KWD"]["receivable"] == "12.00"
    assert value["summaries"]["SAR"]["receivable"] == "500.00"
    assert any(i["code"] == "provider_balance_sar_unavailable" and i["source_id"] == ID for i in value["issues"])
    bind(s, fx())
    assert report(s, provider_balances_sar=True)["summary"]["receivable"] == "647.55"


def test_direct_proof_supersedes_unallocated_rate_but_conflict_after_payout_never_rewrites_original_money():
    s = sample(sar="146.00", basis="salla_order_fx")
    bind(s, fx())
    assert report(s, provider_balances_sar=True)["summary"]["receivable"] == "647.55"
    bind(s, fx(sar="146.00", basis="salla_order_fx"))
    assert report(s, provider_balances_sar=True)["summary"]["receivable"] == "647.55"
    payout(s)
    bind(s, fx(sar="148.00"))
    r = report(s, provider_balances_sar=True)
    assert r["summary"]["receivable"] == "500.00"
    assert r["summaries"]["KWD"]["receivable"] == "7.00"
    assert any(i["code"] == "provider_sar_basis_conflict" for i in r["issues"])


def test_overpaid_refunded_claim_presents_only_the_converted_credit():
    s = sample()
    payout(s, "12.00")
    s["engine"]["obligations"][ID]["confirmed"] = "11.00"
    r = report(s, provider_balances_sar=True)
    assert (r["summary"]["receivable"], r["summary"]["payable"]) == ("500.00","12.30")
    assert r["summaries"]["KWD"]["payable"] == "0.00"
    assert r["summaries"]["KWD"]["actual_liquidity"] == "12.00"


def test_unknown_pooled_foreign_payout_keeps_provider_scope_together():
    s = sample()
    payout(s, "9.00", key=None)
    r = report(s, provider_balances_sar=True)
    assert r["summary"]["receivable"] == "500.00"
    assert r["summaries"]["KWD"]["receivable"] == "12.00"
    assert r["summaries"]["KWD"]["payable"] == "9.00"


def test_changed_native_amount_needs_a_corresponding_updated_fx_basis():
    s = sample()
    s["engine"]["facts"]["provider_order_balance:foreign"].update(gross="19.00", paid="19.00")
    s["engine"]["obligations"][ID]["confirmed"] = "19.00"
    r = report(s, provider_balances_sar=True)
    assert r["summary"]["receivable"] == "500.00"
    assert any(i["code"] == "provider_balance_sar_unavailable" for i in r["issues"])


def test_split_foreign_fix_uses_the_tender_share_and_retains_its_parent_identity():
    s = state_for()
    original = "provider:mix:tabby"
    obligation(s, original, "provider", "provider", "7.00", currency="KWD", direction="receivable", party=PARTY)
    s["engine"]["facts"]["provider_split:mix"] = {"evidence": {"order_id": "mix", "currency":"KWD", "tenders": [
        {"provider": "tabby", "method": "tabby_installment", "amount": "7.00"},
        {"provider": None, "method": "customer_wallet", "amount": "3.00"}]},
        "booked_providers": ["tabby"]}
    refresh_basis(s, [{"id": "mix", "currency": "KWD", "payment": {"split": True},
        "customer_payment_sar": {"native": {"tabby": "7.00", "wallet": "3.00"},
                                 "shares": {"tabby": "84.77", "wallet": "36.33"},
                                 "paid": "121.10", "basis": "salla_list_paid", "currency": "KWD"}}], [])
    balances = report(s, provider_balances_sar=True)
    assert balances["summary"]["receivable"] == "84.77"
    assert [r["id"] for r in balances["obligations"]] == [original]
    assert report(s)["summaries"]["KWD"]["receivable"] == "7.00"


def test_foreign_order_sar_pool_holds_its_first_claim_only_and_preserves_source_gross():
    from operational_balance_engine import reconcile
    from test_operational_balance_order_page_recognition import BORN, T1, T2
    from operational_provider_transaction_evidence import normalize_operational_payment
    def apply(s):
        raw = {"id":"foreign","reference_id":"r","currency":"KWD","total":{"amount":"12.00","currency":"KWD"},
               "payment_method":"tabby_installment","paid_amount":"12.00","remaining_amount":"0.00",
               "status":"under_review","updated_at": T1}
        payment, issues = normalize_operational_payment(raw,"KWD","12.00",BORN,T2,owner="shop",order_number="r",
            provider_order_id="foreign",rows=[],source={"user_id":"shop","order_number":"r"})
        assert not issues and payment
        order = {"id":"foreign","order_number":"r","created_at":BORN,"updated_at":T1,"currency":"KWD",
                 "payment":payment,"customer_payment_sar":fx()}
        return reconcile(s, {"orders":[order]},T2)

    s = state_for()
    s.update(owner_id="shop",started_at="2026-10-01T00:00:00Z")
    s["engine"]["as_of"] = T1
    payout(s,"11.00","SAR",key=None)
    s["movements"][0]["occurred_at"] = T1
    held = apply(s)
    assert ID not in held["engine"]["obligations"]
    held_report = report(held, provider_balances_sar=True)
    assert any(i["code"]=="provider_historical_capture_review_required" for i in held_report["issues"])
    assert held_report["details"]["provider_paid_totals"]["KWD"][0]["paid"] == "12.00"

    s["movements"][0]["occurred_at"] = "2026-10-02T00:00:00Z"
    assert apply(s)["engine"]["obligations"][ID]["confirmed"] == "12.00"
    s["movements"][0]["order_number"] = "r"
    assert ID not in apply(s)["engine"]["obligations"]
    del s["movements"][0]["order_number"]
    s["movements"][0]["occurred_at"] = T1
    obligation(s,"provider:other","provider","provider","11.00",direction="receivable",party=PARTY)
    s["movements"][0]["allocations"] = [{"obligation_id":"provider:other","amount":"11.00"}]
    booked = apply(s)
    assert booked["engine"]["obligations"][ID]["confirmed"] == "12.00"
    booked["movements"][0]["allocations"] = []
    assert apply(booked)["engine"]["obligations"][ID]["confirmed"] == "12.00"
