"""Cumulative paid-on-order recognition, spanning page-only, capture and allocated settlement evidence."""

import pytest

from operational_balance_engine import reconcile
from operational_provider_transaction_evidence import normalize_operational_payment
from test_operational_balance_transaction_capture import native_txn, stored

START = "2026-10-04T00:00:00+00:00"
BORN = "2026-10-05T10:01:00+00:00"
T1 = "2026-10-05T13:00:00+00:00"
T2 = "2026-10-06T10:00:00+00:00"
KEY = "provider:1001"
FACT = "provider_order_balance:1001"


def raw_document(paid="45.00", due="55.00"):
    return {"id": "1001", "reference_id": "123", "currency": "SAR", "total": {"amount": "100.00", "currency": "SAR"},
            "payment_method": {"code": "tamara_installment", "name": "تمارا"}, "paid_amount": paid,
            "remaining_amount": due, "status": "under_review"}


def normalize(raw, rows=()):
    return normalize_operational_payment(raw, "SAR", "100.00", BORN, T2, owner="shop", order_number="123",
        provider_order_id="1001", rows=rows, source={"user_id": "shop", "order_number": "123"})


def state():
    return {"owner_id": "shop", "status": "active", "started_at": START, "openings": {}, "movements": []}


def apply(s, raw, version, *, rows=(), stamp=None):
    p, issues = normalize({**raw, "updated_at": version}, rows)
    assert p and not issues, issues
    order = {"id": "1001", "order_number": "123", "created_at": BORN, "updated_at": version, "currency": "SAR", "payment": p}
    return reconcile(s, {"orders": [order]}, stamp or version)


def source_txn(**changes):
    row = stored(native_txn("tamara_installment", **{"total": {"amount": "45.00", "currency": "SAR"}, **changes}))
    return {**row, "user_id": "shop", "order_number": "123", "provider_order_id": "1001"}


def payout(allocated, allocation_key=KEY):
    return {"party_type":"provider", "party_id":"tamara", "currency":"SAR", "kind":"settlement", "direction":"incoming",
            "amount":"43.00", "actual_fee_amount":"2.00", "allocations":[{"obligation_id":allocation_key,"amount":allocated}]}


def test_capture_after_page_corroborates_without_rebooking_same_claim():
    s=apply(state(),raw_document(),T1)
    before=s["engine"]["facts"][FACT].copy()
    r=apply(s,raw_document(),T2,rows=[source_txn()])
    assert r["engine"]["facts"][FACT] == before
    assert r["engine"]["facts"]["capture:capture-A"]["amount"] == "45.00"
    assert r["engine"]["obligations"][KEY]["confirmed"] == "45.00"
    assert not r["engine"]["obligations"][KEY].get("incomplete")
    assert not r["movements"]


def test_capture_first_then_higher_order_balance_after_an_allocated_payout():
    s=apply(state(),raw_document(),T1,rows=[source_txn()])
    assert FACT not in s["engine"]["facts"]
    assert s["engine"]["obligations"][KEY]["confirmed"]=="45.00"
    s["movements"]=[payout("45.00")]
    full = apply(s,raw_document("100.00","0.00"),T2)
    assert full["engine"]["obligations"][KEY]["confirmed"]=="100.00"
    assert full["engine"]["facts"]["capture:capture-A"]["amount"]=="45.00"
    assert full["engine"]["facts"][FACT]["paid"]=="100.00" and full["movements"] == s["movements"]
    assert full["engine"]["obligations"][KEY]["business_date"] == "2026-10-06"


@pytest.mark.parametrize("allocated,key,held", [
    ("45.00",KEY,False),("45.00","opening:provider",False),
    ("42.00",KEY,True),("45.00","credit:"+KEY,True),("99.00",KEY,True)])
def test_partial_order_amount_then_new_full_does_not_add_or_allocate_bank_money(allocated,key,held):
    s=apply(state(),raw_document(),T1)
    s["movements"]=[payout(allocated,key)]
    full=apply(s,raw_document("100.00","0.00"),T2)
    assert full["movements"]==s["movements"]
    assert full["engine"]["obligations"][KEY]["confirmed"] == ("45.00" if held else "100.00")
    if held:
        candidate=next(i for i in full["engine"]["issues"] if i["code"]=="provider_settled_capture_review_required")
        assert candidate["order_page_balance_review"]["paid"]=="100.00"


@pytest.mark.parametrize("amount,due,version", [("30.00","70.00",T2),("100.00","0.00",T1)])
def test_reduced_or_conflicting_same_version_page_never_reverses_a_receivable(amount,due,version):
    s=apply(state(),raw_document(),T1)
    r=apply(s,raw_document(amount,due),version,stamp=T2)
    assert r["engine"]["facts"][FACT]==s["engine"]["facts"][FACT]
    assert r["engine"]["obligations"][KEY]["confirmed"]=="45.00"
    assert any(i["code"]=="provider_order_page_version_conflict" for i in r["engine"]["issues"])


@pytest.mark.parametrize("method_amount,ccy,allowed",[("45.00","SAR",True),("22.00","SAR",False),("45.00","QAR",False)])
def test_one_unreferenced_same_provider_method_can_corroborate_but_not_change_the_page(method_amount,ccy,allowed):
    doc=raw_document()
    doc["payment_methods"]=[{"payment_method":"tamara","amount":{"amount":method_amount,"currency":ccy}}]
    payment, issues=normalize(doc)
    if allowed:
        assert not issues and payment["order_page_balance"]["paid"]=="45.00" and "captures" not in payment
    else:
        assert issues and payment is None


@pytest.mark.parametrize("changes,allowed",[
    ({"references":{"transaction":"capture-A"}, "paid_at":None},True),
    ({"references":{"transaction":"capture-A"}, "status":{"slug":"authorized"}},False),
    ({"references":{"transaction":"capture-A"}, "total":{"amount":"45.01","currency":"SAR"}},False),
    ({"paid_at":None},True),({"status":{"slug":"declined"}},False)])
def test_optional_transaction_may_corroborate_or_block_but_never_add_to_page(changes,allowed):
    payment, issues=normalize(raw_document(),[source_txn(**changes)])
    if allowed:
        assert payment["order_page_balance"]["paid"]=="45.00" and not payment.get("captures") and not issues
    else:
        assert payment is None and issues


def test_unwitnessed_older_order_is_review_only_even_after_a_later_nonpayment_edit():
    s=state();s["engine"]={"as_of":T1}
    r=apply(s,raw_document(),T2)
    issue=next(i for i in r["engine"]["issues"] if i["code"]=="provider_historical_capture_review_required")
    assert issue["order_page_balance_review"]["paid"]=="45.00" and issue["order_page_balance_review"]["remaining"]=="55.00"
    assert FACT not in r["engine"]["facts"] and KEY not in r["engine"]["obligations"]


@pytest.mark.parametrize("source_changes", [{"user_id":"other"},{"order_number":"999"},{"salla_order_id":"999"},
    {"g47_salla_snapshot":{"cancelled":True}},{"g47_salla_snapshot":{"component_pending":True}},
    {"g47_salla_snapshot":{"requires_authoritative_refresh":True}},{"g47_salla_snapshot":{"source_updated_at":T2}}])
def test_inconsistent_unified_snapshot_cannot_establish_money(source_changes):
    payment, problems=normalize_operational_payment({**raw_document(),"updated_at":T1},"SAR","100.00",BORN,T2,
        owner="shop", order_number="123",provider_order_id="1001",rows=[],
        source={"user_id":"shop","order_number":"123",**source_changes})
    assert payment is None and problems


@pytest.mark.parametrize("policy_change", [{"percentage":"invalid"}, {"percentage":None}, {"fixed_amount":None},
    {"effective_from":"??"}, {"vat_treatment":"unsupported"}])
def test_fee_contract_damage_withholds_only_the_fee_estimate(policy_change):
    from test_operational_balance_native_payment_evidence import policy
    p, issues=normalize({**raw_document(),"updated_at":T1})
    assert not issues
    config = {**policy("tamara"), **policy_change}
    config = {k:v for k,v in config.items() if v is not None}
    s = reconcile(state(),{"orders":[{"id":"1001","order_number":"123","currency":"SAR","payment":p,
        "created_at":BORN,"updated_at":T1}],"fee_policies":[config]},T1)
    assert s["engine"]["obligations"][KEY]["confirmed"]=="45.00"
    assert s["engine"]["obligations"][KEY]["fee_estimate_complete"] is False
    assert KEY not in s["engine"]["provider_reports"]


def test_separately_recorded_provider_refund_uses_corroborated_real_capture_after_page_claim():
    from operational_customer_returns import attach_returns
    from test_operational_balance_native_payment_evidence import policy

    initial=apply(state(),raw_document(),T1)
    captured=apply(initial,raw_document(),T2,rows=[source_txn()])
    payment, problems=normalize({**raw_document(),"updated_at":T2},[source_txn()])
    assert not problems and payment["captures"]
    snapshot={k:v for k,v in payment.items() if k not in ("refunds","order_page_balance")}
    case={"status":"refunded","order_id":"1001","refund_source_type":"provider","refund_source_id":"tamara",
          "shipping":{"kind":"none"},"refund_reference":"synthetic-refund","amount":"10.00","payment_snapshot":snapshot}
    captured["customer_returns"]=[case]
    order={"id":"1001","order_number":"123","created_at":BORN,"updated_at":T2,"currency":"SAR"}
    sources=attach_returns(captured,{"orders":[order],"fee_policies":[policy("tamara")]})
    reduced=reconcile(captured,sources,T2)
    assert reduced["engine"]["obligations"][KEY]["confirmed"]=="35.00"
    assert reduced["engine"]["facts"][FACT] == captured["engine"]["facts"][FACT]
    assert reconcile(reduced,sources,T2)["engine"]["obligations"][KEY] == reduced["engine"]["obligations"][KEY]
    assert reduced["movements"]==[] and reduced["openings"]=={}


def test_corroborating_transaction_cannot_make_a_proved_order_balance_depend_on_incomplete_fee_policy():
    from test_operational_balance_native_payment_evidence import policy
    p, problems = normalize({**raw_document(), "updated_at": T1}, [source_txn()])
    assert not problems and p["captures"] and p["order_page_balance"]
    config = policy("tamara")
    del config["percentage"]
    s = reconcile(state(), {"orders": [{"id":"1001","order_number":"123","currency":"SAR","payment":p,
        "created_at":BORN,"updated_at":T1}],"fee_policies":[config]}, T1)
    assert s["engine"]["obligations"][KEY]["confirmed"] == "45.00"
    assert s["engine"]["facts"]["capture:capture-A"]["amount"] == "45.00"
    assert FACT not in s["engine"]["facts"] or s["engine"]["facts"][FACT]["paid"] == "45.00"
    assert s["engine"]["obligations"][KEY]["fee_estimate_complete"] is False and s["movements"] == []
