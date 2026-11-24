"""New provider money uses capture evidence, never page observation or order time."""
from copy import deepcopy
from datetime import date

import pytest

from operational_balance_engine import reconcile
from operational_balance_freshness import source_warnings
from operational_provider_dates import MULTIPLE_DAYS, UNPROVEN, recognition_day, verified_original_obligation
from operational_provider_transaction_evidence import normalize_operational_payment
from order_activity_v1 import normalize_transaction_row


START = "2026-11-19T00:00:00+00:00"
CREATED = "2026-11-20T08:00:00+00:00"
CAPTURED = "2026-11-21T22:30:00+00:00"  # 22 November in the financial timezone.
NOW = "2026-11-24T09:00:00+00:00"
KEY = "provider:1001"


def state():
    return {"owner_id": "owner", "status": "active", "started_at": START,
            "movements": [], "openings": {}, "requests": {}}


def order(payment):
    return {"id": "1001", "order_number": "88001", "currency": "SAR",
            "created_at": CREATED, "updated_at": NOW, "payment": payment}


def capture(amount="100.00", ref="ref", stamp=CAPTURED):
    return {"id": ref, "amount": amount, "captured_at": stamp}


def page_payment(paid="100.00"):
    return {"provider": "tabby", "gross": "100.00", "order_page_balance": {
        "owner_id": "owner", "order_id": "1001", "order_number": "88001", "provider": "tabby",
        "currency": "SAR", "gross": "100.00", "paid": paid,
        "remaining": format(100 - float(paid), ".2f"), "source_updated_at": NOW,
        "source": "canonical_salla_order_page"}}


def claim(confirmed="0.00", expected="100.00", business_date="2026-11-20"):
    return {"id": KEY, "kind": "provider", "party_id": "tabby", "party_type": "provider",
        "currency": "SAR", "expected": expected, "confirmed": confirmed,
        "direction": "receivable", "business_date": business_date, "name": "تابي"}


def test_canonical_capture_uses_native_financial_day_and_replay_never_duplicates():
    original = state()
    sources = {"orders": [order({"provider": "tabby", "gross": "100.00", "captures": [capture()]})]}
    result = reconcile(original, sources, NOW)
    row = result["engine"]["obligations"][KEY]
    assert (row["confirmed"], row["business_date"]) == ("100.00", "2026-11-22")
    assert result["movements"] == [] and result["openings"] == {}
    again = reconcile(result, sources, NOW)
    assert again["engine"]["facts"] == result["engine"]["facts"]
    assert again["engine"]["obligations"] == result["engine"]["obligations"]
    assert original == state()


def test_new_paid_page_without_effective_capture_time_stays_pending():
    result = reconcile(state(), {"orders": [order(page_payment())]}, NOW)
    assert KEY not in result["engine"]["obligations"]
    assert "provider_order_balance:1001" not in result["engine"]["facts"]
    problem = next(row for row in result["engine"]["issues"] if row["code"] == UNPROVEN)
    assert (problem["party_type"], problem["party_id"], problem["missing_fields"]) == (
        "provider", "tabby", ["capture_timestamp"])
    assert source_warnings(result, {})[0]["code"] == UNPROVEN


@pytest.mark.parametrize("known", ["0.00", "45.00"])
def test_page_increase_cannot_change_a_prior_forecast_or_confirmed_money_or_cash(known):
    initial = state()
    previous = claim(known, "55.00", "2026-11-22" if known != "0.00" else "2026-11-20")
    facts = {"capture:old": {"order_id": "1001", "provider": "tabby", "amount": known}} if known != "0.00" else {}
    initial["engine"] = {"obligations": {KEY: previous}, "facts": facts,
        "order_versions": {"1001": CAPTURED}, "as_of": CAPTURED,
        "provider_reports": {KEY: {"gross": "100.00", "confirmed_receivable": known}}}
    initial["movements"] = [{"id": "manual", "party_type": "bank", "party_id": "bank-a", "currency": "SAR",
        "direction": "incoming", "kind": "manual", "amount": "73.00", "allocations": []}]
    initial["openings"] = {"manual-opening": {"party_type": "bank", "party_id": "bank-a", "currency": "SAR",
        "direction": "for_us", "amount": "20.00"}}
    initial["requests"] = {"manual-request": "manual"}
    before = deepcopy(initial)
    result = reconcile(initial, {"orders": [order(page_payment())]}, NOW)
    assert result["engine"]["obligations"][KEY] == previous
    assert result["engine"]["facts"] == facts
    assert result["engine"]["provider_reports"] == before["engine"]["provider_reports"]
    for field in ("movements", "openings", "requests"):
        assert result[field] == before[field]
    assert any(row["code"] == UNPROVEN for row in result["engine"]["issues"])
    assert initial == before


def test_dated_capture_can_confirm_an_existing_forecast_at_its_original_day():
    initial = state()
    initial["engine"] = {"obligations": {KEY: claim()}, "facts": {}}
    result = reconcile(initial, {"orders": [order({"provider": "tabby", "gross": "100.00", "captures": [capture()]})]}, NOW)
    assert result["engine"]["obligations"][KEY]["business_date"] == "2026-11-22"
    assert result["engine"]["obligations"][KEY]["confirmed"] == "100.00"


def test_legacy_unchanged_claim_retains_its_date_without_being_redated_by_a_new_read():
    initial = state()
    previous = claim("100.00", "0.00", "2026-11-23")
    saved_page = {**page_payment()["order_page_balance"], "observed_at": "2026-11-23T09:00:00+00:00"}
    initial["engine"] = {"obligations": {KEY: previous}, "facts": {"provider_order_balance:1001": saved_page}}
    result = reconcile(initial, {"orders": [order(page_payment())]}, NOW)
    assert result["engine"]["obligations"][KEY]["business_date"] == "2026-11-23"
    assert result["engine"]["facts"]["provider_order_balance:1001"] == saved_page


@pytest.mark.parametrize("captures, captured, reason", [
    ([], "100.00", UNPROVEN),
    ([capture(stamp=CREATED[:10])], "100.00", UNPROVEN),
    ([capture(stamp="2026-11-25T00:00:00Z")], "100.00", UNPROVEN),
    ([capture("45.00")], "100.00", UNPROVEN),
    ([capture("45.00"), capture("55.00", "other", "2026-11-23T09:00:00Z")], "100.00", MULTIPLE_DAYS),
])
def test_unproved_partial_or_multiple_day_capture_cannot_date_the_full_claim(captures, captured, reason):
    with pytest.raises(ValueError, match=reason):
        recognition_day(None, captures, currency="SAR", confirmed="100.00", captured=captured,
                        created_at=CREATED, as_of=NOW)


def test_same_day_distinct_references_and_duplicate_observations_have_one_stable_day():
    values = [capture("45.00"), capture("55.00", "other"), capture("45.00")]
    assert recognition_day(None, values, currency="SAR", confirmed="100.00", captured="100.00",
        created_at=CREATED, as_of=NOW) == date(2026, 11, 22)
    with pytest.raises(ValueError, match=MULTIPLE_DAYS):
        recognition_day(claim("45.00", business_date="2026-11-21"), values, currency="SAR",
            confirmed="100.00", captured="100.00", created_at=CREATED, as_of=NOW)


def split_source(second="mada"):
    raw = {"id": "1001", "reference_id": "88001", "created_at": CREATED, "updated_at": NOW,
        "currency": "SAR", "status": {"slug": "under_review"}, "total": {"amount": "100.00", "currency": "SAR"},
        "payment_method": "tabby_installment", "payment_methods": [
            {"payment_method": "tabby_installment", "transaction_reference": "ref-A", "amount": "65.00"},
            {"payment_method": second, "transaction_reference": "ref-B", "amount": "35.00"}],
        "payment_actions": {"remaining_action": {"paid_amount": {"amount": "100.00", "currency": "SAR"},
            "remaining_amount": {"amount": "0.00", "currency": "SAR"}, "has_remaining_amount": False}}}
    return {"user_id": "owner", "salla_order_id": "1001", "order_number": "88001",
        "raw_by_source": {"salla_direct": raw}}, raw


def transaction(ref, method, amount, stamp, *, bound=True, identity=None):
    refs = {"transaction": ref, **({"order_id": "1001"} if bound else {})}
    raw = {"id": identity or "native-" + ref, "references": refs, "paid_at": stamp,
        "created_at": CREATED, "total": {"amount": amount, "currency": "SAR"},
        "status": {"slug": "captured"}, "payment_method": {"slug": method}}
    stored = normalize_transaction_row(raw, order_number="88001", provider_order_id="1001")
    stored["user_id"] = "owner"
    stored["native_transaction_evidence"] = [stored["native_transaction_evidence"]]
    return stored


def normalized_split(rows=(), *, second="mada"):
    doc, raw = split_source(second)
    payment, issues = normalize_operational_payment(raw, "SAR", "100.00", CREATED, NOW,
        owner="owner", order_number="88001", provider_order_id="1001", rows=rows, source=doc)
    assert not issues
    return order(payment)


def test_split_uses_each_provider_native_capture_day_not_a_shared_refresh_day():
    rows = [transaction("ref-A", "tabby_installment", "65.00", CAPTURED),
            transaction("ref-B", "mada", "35.00", "2026-11-23T09:00:00+00:00")]
    source = normalized_split(rows)
    result = reconcile(state(), {"orders": [source]}, NOW)
    obligations = result["engine"]["obligations"]
    assert (obligations[KEY + ":tabby"]["confirmed"], obligations[KEY + ":tabby"]["business_date"]) == ("65.00", "2026-11-22")
    assert (obligations[KEY + ":salla"]["confirmed"], obligations[KEY + ":salla"]["business_date"]) == ("35.00", "2026-11-23")
    assert "split_captures" not in result["engine"]["facts"]["provider_split:1001"]["evidence"]
    again = reconcile(result, {"orders": [source]}, NOW)
    assert again["engine"]["facts"] == result["engine"]["facts"]
    assert again["engine"]["obligations"] == obligations
    assert result["movements"] == []


def test_split_fee_basis_retains_original_capture_day_across_replay_and_missing_later_clock():
    from test_operational_balance_engine import policy
    source = normalized_split([transaction("ref-A", "tabby_installment", "65.00", CAPTURED),
        transaction("ref-B", "mada", "35.00", CAPTURED)])
    policies = [policy(effective_from="2026-11-22"), policy(id="salla-fees", provider="salla", effective_from="2026-11-22")]
    inputs = {"orders": [source], "fee_policies": policies}
    result = reconcile(state(), inputs, NOW)
    assert KEY + ":tabby" in result["engine"]["provider_reports"]
    assert result["engine"]["facts"]["provider_split:1001"]["capture_business_dates"] == {
        "tabby": "2026-11-22", "salla": "2026-11-22"}
    for current in (source, normalized_split()):
        again = reconcile(result, {"orders": [current], "fee_policies": policies}, NOW)
        for field in ("obligations", "facts", "provider_reports"):
            assert again["engine"][field] == result["engine"][field]


@pytest.mark.parametrize("evidence", ["missing", "unbound", "created_only", "other_owner"])
def test_split_page_or_activity_without_bound_paid_timestamp_cannot_confirm_money(evidence):
    rows = [] if evidence == "missing" else [transaction("ref-A", "tabby_installment", "65.00",
        None if evidence == "created_only" else CAPTURED, bound=evidence != "unbound")]
    if evidence == "other_owner":
        rows[0]["user_id"] = "different-owner"
    result = reconcile(state(), {"orders": [normalized_split(rows)]}, NOW)
    assert result["engine"]["obligations"] == {}
    assert result["engine"]["facts"]["provider_split:1001"]["booked_providers"] == []
    assert {r["party_id"] for r in result["engine"]["issues"] if r["code"] == UNPROVEN} == {"tabby", "salla"}


def test_split_partial_proof_dates_only_the_complete_provider_share_and_preserves_pending_identity():
    source = normalized_split([transaction("ref-A", "tabby_installment", "65.00", CAPTURED)])
    result = reconcile(state(), {"orders": [source]}, NOW)
    assert set(result["engine"]["obligations"]) == {KEY + ":tabby"}
    completed = normalized_split([transaction("ref-A", "tabby_installment", "65.00", CAPTURED),
        transaction("ref-B", "mada", "35.00", CAPTURED)])
    final = reconcile(result, {"orders": [completed]}, NOW)
    assert set(final["engine"]["obligations"]) == {KEY + ":tabby", KEY + ":salla"}
    assert final["engine"]["obligations"][KEY + ":tabby"] == result["engine"]["obligations"][KEY + ":tabby"]
    assert final["engine"]["facts"]["provider_split:1001"]["evidence"] == result["engine"]["facts"]["provider_split:1001"]["evidence"]


def test_split_conflicting_native_capture_times_fail_normalization():
    doc, raw = split_source()
    rows = [transaction("ref-A", "tabby_installment", "65.00", stamp)
            for stamp in (CAPTURED, "2026-11-23T09:00:00+00:00")]
    payment, issues = normalize_operational_payment(raw, "SAR", "100.00", CREATED, NOW,
        owner="owner", order_number="88001", provider_order_id="1001", rows=rows, source=doc)
    assert payment is None and issues == [{"code": "provider_capture_timestamp_conflict"}]


def test_legacy_split_claim_replays_without_new_dates_or_capture_proof():
    source = normalized_split()
    initial = state()
    existing = {**claim("65.00", "0.00", "2026-11-21"), "id": KEY + ":tabby"}
    initial["engine"] = {"obligations": {KEY + ":tabby": existing}, "facts": {"provider_split:1001": {
        "evidence": deepcopy(source["payment"]["split"]), "booked_providers": ["tabby"],
        "observed_at": "2026-11-21T09:00:00+00:00"}}}
    result = reconcile(initial, {"orders": [source]}, NOW)
    assert result["engine"]["obligations"][KEY + ":tabby"]["business_date"] == "2026-11-21"
    assert result["engine"]["obligations"][KEY + ":tabby"]["confirmed"] == "65.00"
    assert KEY + ":salla" not in result["engine"]["obligations"]
    assert result["engine"]["facts"] == initial["engine"]["facts"]


@pytest.mark.parametrize('split', [False, True])
def test_recovery_proof_requires_real_source_day_amount_party_and_saved_fact(split):
    source = (normalized_split([transaction('ref-A', 'tabby_installment', '65.00', CAPTURED)]) if split
        else order({'provider': 'tabby', 'gross': '100.00', 'captures': [capture()]}))
    result = reconcile(state(), {'orders': [source]}, NOW)
    key = KEY + ':tabby' if split else KEY
    row = result['engine']['obligations'][key]
    assert verified_original_obligation(result, source, key, row, NOW)
    for field, value in [('business_date', '2026-11-24'), ('confirmed', '64.00'), ('party_id', 'tamara'),
                         ('currency', 'USD'), ('expected', '1.00'), ('direction', 'payable')]:
        assert not verified_original_obligation(result, source, key, {**row, field: value}, NOW)
    damaged = deepcopy(result)
    damaged['engine']['facts'].clear()
    assert not verified_original_obligation(damaged, source, key, row, NOW)
    undated = deepcopy(source)
    undated['payment'].pop('split_captures' if split else 'captures')
    assert not verified_original_obligation(result, undated, key, row, NOW)
    refunded = deepcopy(source)
    refunded['payment']['refunds'] = [{'id': 'refund', 'amount': '1.00', 'status': 'executed'}]
    assert not verified_original_obligation(result, refunded, key, row, NOW)
