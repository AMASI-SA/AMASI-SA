"""Synthetic native-date fixtures follow the unchanged financial engine exactly."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from operational_balance_engine import reconcile
from operational_balance_recovery_proof import build_recovery_proof


NOW = "2030-04-24T12:00:00+00:00"
APPROVED = "2030-04-23T21:17:10.123000+00:00"


def before_state():
    return {"owner_id": "owner", "status": "active", "revision": 8,
        "started_at": "2030-04-01T00:00:00+03:00", "movements": [], "openings": {}, "requests": {},
        "engine": {"as_of": "2030-04-21T14:30:00+00:00", "obligations": {}, "facts": {},
                   "recovery_proof_v1": {"historical_receipt_must_not_change": True}}}


def ad_setup(native="12.345678", day="2030-04-22", funding="wallet_first"):
    before = before_state()
    source = {"account_id": "google-account", "date": day, "currency": "SAR", "amount": native,
        "source_cumulative_amount": native, "baseline_amount": "0", "timezone": "Asia/Riyadh",
        "observed_at": NOW, "closed": True, "complete": True, "day_ended": True,
        "funding_type": "hybrid" if funding in {"wallet_first", "split"} else funding,
        **({"funding_policy": "wallet_first"} if funding == "wallet_first" else
           {"wallet_fraction": "0.5"} if funding == "split" else {})}
    if funding == "wallet_first":
        before["ad_funding"] = {"google-account": {"mode": "wallet_first", "currency": "SAR"}}
    sources = {"ad_snapshots": [source]}
    return before, reconcile(before, sources, NOW), sources


def supplier_setup(amount="35.00"):
    before = before_state()
    source = {"id": "invoice:item:product", "order_id": "order", "item_id": "item", "supplier_id": "supplier",
        "amount": amount, "net_amount": amount, "gross_amount": amount, "tax_amount": "0.00",
        "tax_evidence": {"treatment": "exempt"}, "currency": "SAR", "accepted_at": APPROVED,
        "invoice_id": "invoice", "invoice_number": "native-invoice-number", "component_fully_invoiced": True}
    sources = {"supplier_receipts": [source]}
    after = reconcile(before, sources, NOW)
    key = "supplier:order:item:receipt:invoice:item:product"
    assert after["engine"]["obligations"][key]["business_date"] == "2030-04-24"
    return before, after, sources, key


def prove(before, after, sources):
    originals = deepcopy((before, after, sources))
    result = build_recovery_proof(before, after, sources, NOW)
    assert (before, after, sources) == originals
    assert result["source_completeness_verified"] is False
    assert after["engine"]["recovery_proof_v1"] == {"historical_receipt_must_not_change": True}
    return result


@pytest.mark.parametrize("native,day,expected", [
    ("87.654321", "2030-04-21", "87.65"),
    ("12.345678", "2030-04-22", "12.35"),
    ("43.210987", "2030-04-23", "43.21"),
    ("1.005000", "2030-04-23", "1.01"),
    ("0.004999", "2030-04-23", "0.00"),
])
def test_closed_google_source_micros_verify_exact_saved_engine_cents(native, day, expected):
    before, after, sources = ad_setup(native, day)
    proof = prove(before, after, sources)
    assert proof["complete"] is True and proof["original_dates_verified"] is True
    row, = proof["new_original_dated"]["obligations"]
    assert row["business_date"] == day and row["confirmed"] == expected
    saved, = after["engine"]["obligations"].values()
    assert saved["source_cumulative_amount"] == native
    assert saved["ad_total_amount"] == expected


@pytest.mark.parametrize("funding", ["prepaid", "postpaid", "split", "wallet_first"])
def test_native_fractional_cent_verification_matches_each_engine_funding_allocation(funding):
    before, after, sources = ad_setup("1.005000", funding=funding)
    proof = prove(before, after, sources)
    assert proof["original_dates_verified"] is True
    row, = proof["new_original_dated"]["obligations"]
    assert row["confirmed"] == "1.01"
    assert row["count"] == (2 if funding == "split" else 1)


@pytest.mark.parametrize("fault", ["confirmed_cent", "total_cent", "saved_cent", "expected", "day", "account", "timezone", "not_closed", "not_complete", "early_observation", "direction", "row_id", "funding_mode", "funding_part", "wallet_fraction", "funding_policy"])
def test_ad_cent_conversion_does_not_relax_amount_identity_or_close_evidence(fault):
    before, after, sources = ad_setup()
    row, = after["engine"]["obligations"].values()
    day, = after["engine"]["ad_days"].values()
    source = sources["ad_snapshots"][0]
    if fault == "confirmed_cent": row["confirmed"] = "12.36"
    elif fault == "total_cent": row["ad_total_amount"] = "12.36"
    elif fault == "saved_cent": day["amount"] = "12.36"
    elif fault == "expected": row["expected"] = "0.01"
    elif fault == "day": row["business_date"] = "2030-04-23"
    elif fault == "account": row["party_id"] = "other-account"
    elif fault == "timezone": row["timezone"] = "UTC"
    elif fault == "not_closed": source["closed"] = False
    elif fault == "not_complete": source["complete"] = False
    elif fault == "early_observation": source["observed_at"] = "2030-04-22T12:00:00+00:00"
    elif fault == "direction": row["direction"] = "receivable"
    elif fault == "row_id": row["id"] = "unrelated"
    elif fault == "funding_mode": row["funding_mode"] = "prepaid"
    elif fault == "funding_part": row["funding_type"] = "postpaid"
    elif fault == "wallet_fraction": row["wallet_fraction"] = "0.5"
    else: row["funding_policy"] = "unrelated"
    proof = prove(before, after, sources)
    assert proof["original_dates_verified"] is False
    assert not proof["new_original_dated"]["obligations"]


def test_same_sum_wrong_hybrid_allocation_is_not_certified():
    before, after, sources = ad_setup("0.03", funding="split")
    rows = after["engine"]["obligations"]
    key = "advertising:google-account:2030-04-22"
    assert (rows[key+":wallet"]["confirmed"], rows[key+":credit"]["confirmed"]) == ("0.02", "0.01")
    rows[key+":wallet"]["confirmed"], rows[key+":credit"]["confirmed"] = "0.01", "0.02"
    assert prove(before, after, sources)["original_dates_verified"] is False


def test_changed_google_amount_certifies_after_only_and_keeps_saved_before_date():
    before, _, sources = ad_setup("40.25", "2030-04-21")
    old_source = deepcopy(sources)
    old_source["ad_snapshots"][0].update(closed=False, complete=False, observed_at="2030-04-21T12:00:00+00:00")
    before = reconcile(before, old_source, "2030-04-21T14:30:00+00:00")
    sources["ad_snapshots"][0].update(amount="87.654321", source_cumulative_amount="87.654321")
    after = reconcile(before, sources, NOW)
    proof = prove(before, after, sources)
    assert proof["new_obligation_count"] == 0
    changed = proof["changed_existing"]
    assert changed["count"] == 1 and changed["original_dates_verified"] is True
    assert changed["before"]["obligations"][0]["business_date"] == "2030-04-21"
    assert changed["before"]["obligations"][0]["expected"] == "40.25"
    assert changed["after"]["obligations"][0]["confirmed"] == "87.65"


@pytest.mark.parametrize("amount", ["35.00", "0.00"])
def test_native_supplier_approval_verifies_riyadh_original_day_without_financial_writes(amount):
    before, after, sources, key = supplier_setup(amount)
    proof = prove(before, after, sources)
    assert proof["complete"] is True and proof["original_dates_verified"] is True
    row, = proof["new_original_dated"]["obligations"]
    assert row["business_date"] == "2030-04-24" and row["confirmed"] == amount
    assert row["confirmed_count"] == int(Decimal(amount) > 0)
    assert after["engine"]["obligations"][key]["source_invoice_approved_at"] == APPROVED


@pytest.mark.parametrize("fault", ["missing_source", "duplicate_source", "invoice", "invoice_number", "component", "source_amount", "gross", "tax", "date", "approved_at", "frozen_fact", "fact_return", "source_return", "returned_amount", "expected", "currency", "direction", "identity", "estimate", "missing_frozen_fact"])
def test_supplier_verifier_stays_closed_without_unique_paired_native_approval(fault):
    before, after, sources, key = supplier_setup()
    source = sources["supplier_receipts"][0]
    row = after["engine"]["obligations"][key]
    rid = "receipt:invoice:item:product"
    if fault == "missing_source": sources["supplier_receipts"] = []
    elif fault == "duplicate_source": sources["supplier_receipts"].append(deepcopy(source))
    elif fault == "invoice": row["source_invoice_id"] = "other-invoice"
    elif fault == "invoice_number": row["source_invoice_number"] = "other-number"
    elif fault == "component": source["component_fully_invoiced"] = False
    elif fault == "source_amount": source["amount"] = "36.00"
    elif fault == "gross": row["gross_amount"] = "36.00"
    elif fault == "tax": row["tax_evidence"] = {"treatment": "different"}
    elif fault == "date": row["business_date"] = "2030-04-23"
    elif fault == "approved_at": row["source_invoice_approved_at"] = "2030-04-24T01:00:00+00:00"
    elif fault == "frozen_fact": after["engine"]["facts"][rid]["accepted_at"] = "2030-04-24T01:00:00+00:00"
    elif fault == "fact_return": after["engine"]["facts"]["return:one"] = {"receipt_id": rid, "amount": "0.00"}
    elif fault == "source_return": sources["supplier_returns"] = [{"id":"ret", "receipt_id":source["id"], "amount":"1.00", "accepted":True, "accepted_at":APPROVED}]
    elif fault == "returned_amount": row["returned_amount"] = "1.00"
    elif fault == "expected": row["expected"] = "1.00"
    elif fault == "currency": row["currency"] = "USD"
    elif fault == "direction": row["direction"] = "receivable"
    elif fault == "identity": row["evidence_ids"] = ["receipt:unrelated"]
    elif fault == "estimate": row.pop("source_invoice_id")
    else: after["engine"]["facts"].pop(rid)
    proof = prove(before, after, sources)
    assert proof["original_dates_verified"] is False
    assert not proof["new_original_dated"]["obligations"]


def test_unassigned_expected_supplier_estimate_is_not_certified_as_native_receipt():
    before = before_state()
    after = deepcopy(before)
    after["engine"]["obligations"]["supplier:order:item"] = {"id":"supplier:order:item", "kind":"supplier",
        "party_type":"supplier", "party_id":None, "currency":"SAR", "direction":"payable",
        "business_date":"2030-04-20", "confirmed":"0.00", "expected":"35.00"}
    proof = prove(before, after, {})
    assert proof["original_dates_verified"] is False
    assert proof["new_date_unverified"]["obligations"][0]["expected"] == "35.00"
