"""Synthetic closed-day approval boundaries; no production data or service calls."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from operational_balance_engine import reconcile
from operational_balance_store import digest
from operational_ad_corrections import build_preview, record_approval, _check_approval, approval_receipt
from operational_ad_correction_routes import AdCorrectionApproval


OWNER = "synthetic-owner"
ACCOUNT = "synthetic-ad"
DAY = "2030-04-02"
STAMP = "2030-04-04T04:00:00+00:00"
KEY = f"advertising:{ACCOUNT}:{DAY}"


def snapshot(**changes):
    return {"id": "synthetic-source", "account_id": ACCOUNT, "date": DAY,
            "currency": "SAR", "timezone": "Asia/Riyadh", "funding_type": "postpaid",
            "amount": "12.345678", "source_cumulative_amount": "12.345678", "baseline_amount": "0",
            "observed_at": "2030-04-03T04:00:00+00:00", "complete": True,
            "closed": True, "day_ended": True, **changes}


def closed_state(source=None, *, funding=None):
    initial = {"_id": OWNER, "owner_id": OWNER, "revision": 7, "status": "active",
               "started_at": "2030-04-01T00:00:00+00:00", "openings": {}, "movements": [],
               "requests": {}, "audit": []}
    if funding is not None:
        initial["ad_funding"] = funding
    result = reconcile(initial, {"ad_snapshots": [source or snapshot()]}, STAMP)
    result["engine"]["as_of"] = STAMP
    return result


def codes(state):
    return {row["code"] for row in state["engine"]["issues"]}


def test_identical_native_precision_is_not_a_closed_day_financial_correction():
    before = closed_state()
    after = reconcile(before, {"ad_snapshots": [snapshot()]}, STAMP)
    assert after["engine"]["obligations"][KEY]["confirmed"] == "12.35"
    assert "advertising_closed_day_correction_required" not in codes(after)
    assert not any(key.startswith("ad_correction:") for key in after["engine"]["facts"])


def test_same_total_hybrid_part_change_still_requires_approval():
    original = snapshot(amount="12.3451", funding_type="hybrid", wallet_fraction="0.5")
    before = closed_state(original)
    changed = {**original, "amount": "12.3549", "observed_at": STAMP}
    after = reconcile(before, {"ad_snapshots": [changed]}, STAMP)
    assert after["engine"]["ad_days"][KEY]["amount"] == "12.35"
    assert "advertising_closed_day_correction_required" in codes(after)
    assert after["engine"]["obligations"] == before["engine"]["obligations"]
    approved = {**changed, "correction_approved": True, "correction_id": "synthetic-split-review"}
    after = reconcile(before, {"ad_snapshots": [approved]}, STAMP)
    assert after["engine"]["obligations"][KEY + ":wallet"]["confirmed"] == "6.18"
    assert after["engine"]["obligations"][KEY + ":credit"]["confirmed"] == "6.17"


@pytest.mark.parametrize("changes", [
    {"complete": False}, {"closed": False}, {"day_ended": False},
    {"correction_approved": "true"}, {"correction_id": ""},
])
def test_approval_requires_a_real_complete_close_and_explicit_boolean(changes):
    before = closed_state()
    changed = {**snapshot(amount="24.56", observed_at=STAMP, correction_approved=True,
                         correction_id="synthetic-reviewed-change"), **changes}
    after = reconcile(before, {"ad_snapshots": [changed]}, STAMP)
    assert after["engine"]["obligations"] == before["engine"]["obligations"]
    assert after["engine"]["ad_days"] == before["engine"]["ad_days"]
    assert after["engine"]["facts"] == before["engine"]["facts"]
    assert codes(after)


@pytest.mark.parametrize("changes", [
    {"funding_type": "invalid"}, {"funding_type": "prepaid"},
    {"currency": "USD"}, {"timezone": "UTC"}, {"sar_amount": "bad", "funding_type": "hybrid", "wallet_fraction": "0.5"},
])
def test_failed_approved_snapshot_never_leaves_a_correction_fact(changes):
    before = closed_state()
    pristine = deepcopy(before)
    changed = {**snapshot(amount="24.56", observed_at=STAMP, correction_approved=True,
                          correction_id="synthetic-invalid-change"), **changes}
    after = reconcile(before, {"ad_snapshots": [changed]}, STAMP)
    assert after["engine"]["obligations"] == before["engine"]["obligations"]
    assert after["engine"]["ad_days"] == before["engine"]["ad_days"]
    assert after["engine"]["facts"] == before["engine"]["facts"]
    assert before == pristine


def test_same_native_cent_changed_sar_cent_is_a_financial_correction():
    original = snapshot(amount="12.3451", currency="USD", sar_amount="45.68", fx_rate="3.7",
        fx_source="synthetic_owner_review", fx_at="2030-04-03T04:00:00+00:00", fx_date=DAY,
        fx_snapshot_id="synthetic-fx", fx_evidence="synthetic_fx_evidence")
    before = closed_state(original)
    changed = {**original, "amount": "12.3549", "sar_amount": "45.71", "observed_at": STAMP}
    held = reconcile(before, {"ad_snapshots": [changed]}, STAMP)
    assert held["engine"]["obligations"] == before["engine"]["obligations"]
    assert "advertising_closed_day_correction_required" in codes(held)
    approved = reconcile(before, {"ad_snapshots": [{**changed,
        "correction_approved": True, "correction_id": "synthetic-sar-review"}]}, STAMP)
    assert approved["engine"]["obligations"][KEY]["confirmed"] == "12.35"
    assert approved["engine"]["obligations"][KEY]["sar_amount"] == "45.71"
    assert "ad_correction:synthetic-sar-review" in approved["engine"]["facts"]


def test_retiring_malformed_part_rejects_before_fact_day_or_obligation_changes():
    before = closed_state()
    before["engine"]["obligations"][KEY + ":wallet"] = {"confirmed": "bad", "expected": "0.00"}
    changed = snapshot(amount="24.56", observed_at=STAMP, correction_approved=True, correction_id="synthetic-retiring-review")
    after = reconcile(before, {"ad_snapshots": [changed]}, STAMP)
    for field in ("facts", "ad_days", "obligations"):
        assert after["engine"][field] == before["engine"][field]
    assert "amount_invalid" in codes(after)


SELECTION = {"account_id": ACCOUNT, "date_from": DAY, "date_to": DAY}


def native_context(spend="24.56", *, platform="meta", currency="SAR", timezone_name="Asia/Riyadh", source_changes=None, proof_changes=None):
    provider, collection = {"meta": ("meta_ads", "mezan_meta_performance_daily_v2"),
        "snapchat": ("snapchat_ads", "mezan_snapchat_daily_projections_v2"),
        "tiktok": ("tiktok_ads", "mezan_tiktok_performance_daily_v2"),
        "google_ads": ("google_ads", "mezan_google_ads_performance_daily_v2")}[platform]
    proof = {"version": 1, "complete": True, "complete_response": True, "explicit_spend_present": True,
        "account_id": "synthetic-external", "business_date": DAY, "timezone": timezone_name, "currency": currency,
        "spend_native": spend, "observed_at": STAMP, "zero_confirmed": spend == "0", **(proof_changes or {})}
    proof["fingerprint"] = sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    row = {"_id": "synthetic-source", "user_id": OWNER, "provider": provider, "ad_account_id": "synthetic-external",
        "date": DAY, "spend_native": spend, "currency_native": currency, "account_timezone": timezone_name,
        "observed_at": STAMP, "source_close_proof": proof, **(source_changes or {})}
    if platform == "snapchat":
        row = {"_id": "synthetic-source", "user_id": OWNER, "provider": provider, "ad_account_id": "synthetic-external",
            "report_date": DAY, "base_spend_native": spend, "currency": currency, "projection_timezone": timezone_name,
            "source_latest_updated_at": STAMP, "source_close_proof": proof, "amount_complete": True,
            "data_state": "confirmed_zero" if spend == "0" else "confirmed_data", **(source_changes or {})}
    binding = {"_id": ACCOUNT, "user_id": OWNER, "platform": platform, "integration_account_id": "synthetic-integration",
        "platform_account_id": "synthetic-external", "funding_mode": "postpaid", "confirmed_by": OWNER,
        "confirmed_at": "2030-04-01T00:00:00+00:00", "currency": currency, "timezone": timezone_name, "status": "active"}
    account = {"_id": "synthetic-account", "user_id": OWNER, "provider": provider,
        "mezan_integration_account_id": "synthetic-integration", "external_account_id": "synthetic-external",
        "connection_status": "connected", "currency": currency, "timezone": timezone_name, "display_name": "Synthetic Ads"}
    fx = [] if currency == "SAR" else [{"id": "synthetic-fx", "user_id": OWNER, "currency": currency, "business_date": DAY,
        "status": "active", "fx_rate_to_sar": "3.7", "fx_source": "synthetic_owner_review", "fx_at": "2030-04-03T12:00:00+00:00",
        "confirmed_by": OWNER, "confirmed_at": "2030-04-03T12:00:00+00:00", "evidence": "synthetic_fx_evidence"}]
    return {"binding": binding, "account": account, "collection": collection, "date_field": "report_date" if platform == "snapchat" else "date", "data": {
        "mz2_ad_account_bindings_v2": [binding], "mezan_integration_accounts_v2": [account],
        "mz2_ad_automation_policies_v2": [], "mz2_ad_fx_snapshots_v2": fx, "mezan_ad_account_cost_settings_v2": [], collection: [row]}}


def approved_payload(review, **changes):
    return {"selection": review.get("selection", SELECTION), "days": [DAY], "expected_revision": review["revision"],
        "expected_preview_hash": review["preview_hash"], "expected_session_scope": digest([OWNER, OWNER]),
        "request_id": "synthetic-request-123", "reason": "Reviewed native platform restatement", "confirm_reviewed": True, **changes}


def review_context(state=None, native=None):
    state = state if state is not None else closed_state()
    native = native if native is not None else native_context()
    reviewed, private = build_preview(state, native, SELECTION, STAMP)
    return state, native, reviewed, private


def test_review_is_read_only_binds_native_and_old_parts_and_accepts_exact_changed_day():
    state, native = closed_state(), native_context()
    pristine = deepcopy((state, native))
    reviewed, private = build_preview(state, native, SELECTION, STAMP)
    assert reviewed["can_approve"] and reviewed["items"][0]["can_approve"]
    assert reviewed["items"][0]["before_amount"] == "12.35"
    assert reviewed["items"][0]["after_amount"] == "24.56"
    assert reviewed["items"][0]["difference"] == "12.21"
    assert reviewed["read_only"] and not reviewed["posting_allowed"]
    assert reviewed["source_completeness_verified"] is False
    assert (state, native) == pristine
    assert list(private[DAY]["projected"]) == [KEY]


def test_google_identical_high_precision_source_has_no_approvable_difference():
    _, _, reviewed, _ = review_context(native=native_context("12.345678", platform="google_ads"))
    assert not reviewed["can_approve"]
    assert reviewed["items"][0]["status"] == "unchanged"
    assert reviewed["items"][0]["difference"] == "0.00"


def test_hybrid_preview_compares_the_full_part_vector_even_with_zero_total_difference():
    native = native_context("12.3549")
    native["binding"].update(funding_mode="hybrid", hybrid_policy="explicit_split", version=1)
    native["data"]["mz2_ad_automation_policies_v2"] = [{"user_id": OWNER, "id": "synthetic-policy",
        "platform": "meta", "integration_account_id": "synthetic-integration", "version": 1,
        "binding_version": 1, "status": "active", "confirmed_by": OWNER, "confirmed_at": "2030-04-01T00:00:00+00:00",
        "start_date": "2030-04-01", "wallet_fraction": "0.5", "evidence": "synthetic review"}]
    state = closed_state(snapshot(amount="12.3451", funding_type="hybrid", wallet_fraction="0.5"))
    reviewed, _ = build_preview(state, native, SELECTION, STAMP)
    assert reviewed["can_approve"] and reviewed["items"][0]["difference"] == "0.00"
    assert {part["difference"] for part in reviewed["items"][0]["parts"]} == {"0.01", "-0.01"}


def test_native_zero_decrease_requires_explicit_zero_evidence():
    reviewed = review_context(native=native_context("0"))[2]
    assert reviewed["can_approve"] and reviewed["items"][0]["difference"] == "-12.35"
    assert not review_context(native=native_context("0", proof_changes={"zero_confirmed": False}))[2]["can_approve"]


@pytest.mark.parametrize("change", [None, "rate", "missing", "duplicate"])
def test_foreign_currency_review_pins_its_existing_fx_evidence(change):
    native = native_context("24.56", source_changes={"currency_native": "USD"}, proof_changes={"currency": "USD"})
    native["account"]["currency"] = native["binding"]["currency"] = "USD"
    fx = {"id": "synthetic-fx", "user_id": OWNER, "currency": "USD", "business_date": DAY,
        "status": "active", "fx_rate_to_sar": "3.7", "fx_source": "synthetic_owner_review", "fx_at": "2030-04-03T04:00:00+00:00",
        "confirmed_by": OWNER, "confirmed_at": "2030-04-03T04:00:00+00:00", "evidence": "synthetic_fx_evidence"}
    native["data"]["mz2_ad_fx_snapshots_v2"] = [fx]
    state = closed_state(snapshot(currency="USD", fx_rate="3.7", fx_source=fx["fx_source"], fx_date=DAY,
        fx_at=fx["fx_at"], fx_snapshot_id=fx["id"], fx_evidence=fx["evidence"], sar_amount="45.68"))
    if change == "rate": fx["fx_rate_to_sar"] = "3.8"
    if change == "missing": native["data"]["mz2_ad_fx_snapshots_v2"] = []
    if change == "duplicate": native["data"]["mz2_ad_fx_snapshots_v2"].append(deepcopy(fx))
    reviewed, _ = build_preview(state, native, SELECTION, STAMP)
    assert reviewed["can_approve"] is (change is None)
    if change is None:
        assert reviewed["items"][0]["currency"] == "USD" and reviewed["items"][0]["difference"] == "12.21"


@pytest.mark.parametrize("old_partial", [{}, {"fx_rate": "3.7", "sar_amount": "45.68"},
    {"fx_rate": "0", "sar_amount": "0.00", "fx_at": "2030-04-03T04:00:00+00:00"}])
def test_both_missing_or_partial_old_and_new_fx_cannot_be_an_approved_native_only_change(old_partial):
    native = native_context("24.56", source_changes={"currency_native": "USD"}, proof_changes={"currency": "USD"})
    native["account"]["currency"] = native["binding"]["currency"] = "USD"
    state = closed_state(snapshot(currency="USD", **old_partial))
    reviewed, _ = build_preview(state, native, SELECTION, STAMP)
    assert not reviewed["can_approve"]
    assert "ad_correction_fx_evidence_incomplete" in reviewed["items"][0]["issues"]


def test_same_native_cent_sar_change_is_visible_in_preview_with_unchanged_fx_proof():
    native = native_context("12.3549", source_changes={"currency_native": "USD"}, proof_changes={"currency": "USD"})
    native["account"]["currency"] = native["binding"]["currency"] = "USD"
    fx = {"id": "synthetic-fx", "user_id": OWNER, "currency": "USD", "business_date": DAY,
        "status": "active", "fx_rate_to_sar": "3.7", "fx_source": "synthetic_owner_review", "fx_at": "2030-04-03T04:00:00+00:00",
        "confirmed_by": OWNER, "confirmed_at": "2030-04-03T04:00:00+00:00", "evidence": "synthetic_fx_evidence"}
    native["data"]["mz2_ad_fx_snapshots_v2"] = [fx]
    state = closed_state(snapshot(amount="12.3451", currency="USD", fx_rate="3.7", fx_source=fx["fx_source"], fx_date=DAY,
        fx_at=fx["fx_at"], fx_snapshot_id=fx["id"], fx_evidence=fx["evidence"], sar_amount="45.68"))
    reviewed, _ = build_preview(state, native, SELECTION, STAMP)
    row = reviewed["items"][0]
    assert row["can_approve"] and row["difference"] == "0.00"
    assert row["parts"][0]["before_sar"] == "45.68" and row["parts"][0]["after_sar"] == "45.71"
    assert row["parts"][0]["sar_difference"] == "0.03" and row["fx_basis"]["fx_at"] == fx["fx_at"]


def test_sar_identity_observation_timestamp_alone_is_nonfinancial():
    original = snapshot(fx_rate="1", fx_source="SAR_identity", fx_date=DAY,
        fx_at="2030-04-03T04:00:00+00:00", sar_amount="12.345678")
    before = closed_state(original)
    after = reconcile(before, {"ad_snapshots": [{**original, "fx_at": STAMP, "observed_at": STAMP}]}, STAMP)
    assert after["engine"]["obligations"][KEY]["confirmed"] == "12.35"
    assert not any(key.startswith("ad_correction:") for key in after["engine"]["facts"])
    assert not codes(after)


@pytest.mark.parametrize("source,proof", [
    ({}, {"complete": False}), ({}, {"complete_response": False}), ({}, {"explicit_spend_present": False}),
    ({}, {"account_id": "foreign"}), ({}, {"currency": "USD"}), ({}, {"business_date": "2030-04-03"}),
    ({}, {"spend_native": "99.99"}), ({"account_timezone": "UTC"}, {}),
    ({"observed_at": "2030-04-05T00:00:00+00:00"}, {}),
])
def test_incomplete_or_unbound_native_close_proof_is_never_approvable(source, proof):
    _, _, reviewed, _ = review_context(native=native_context(source_changes=source, proof_changes=proof))
    assert not reviewed["can_approve"]
    assert reviewed["items"][0]["status"] == "blocked"


def test_duplicate_native_day_and_duplicate_or_malformed_old_identity_fail_closed():
    native = native_context()
    native["data"][native["collection"]].append(deepcopy(native["data"][native["collection"]][0]))
    assert not review_context(native=native)[2]["can_approve"]
    state = closed_state()
    state["engine"]["obligations"]["unexpected"] = deepcopy(state["engine"]["obligations"][KEY])
    assert not review_context(state=state)[2]["can_approve"]
    state = closed_state()
    state["engine"]["obligations"][KEY]["id"] = "wrong-identity"
    assert not review_context(state=state)[2]["can_approve"]


@pytest.mark.parametrize("field,value", [
    ("currency", "USD"), ("direction", "receivable"), ("business_date", "2030-04-03"),
    ("wallet_fraction", "0.1"), ("baseline_amount", "1"), ("expected", "1.00"),
])
def test_old_financial_contract_is_not_reinterpreted(field, value):
    state = closed_state()
    state["engine"]["obligations"][KEY][field] = value
    assert not review_context(state=state)[2]["can_approve"]


def test_review_digest_changes_with_exact_native_proof_old_part_and_revision():
    state, native, reviewed, _ = review_context()
    later = "2030-04-04T05:00:00+00:00"
    # Passing time alone does not make an approval stale.
    assert build_preview(state, native, SELECTION, later)[0]["preview_hash"] == reviewed["preview_hash"]
    state2 = deepcopy(state)
    state2["revision"] += 1
    assert build_preview(state2, native, SELECTION, later)[0]["preview_hash"] != reviewed["preview_hash"]
    state2 = deepcopy(state)
    state2["engine"]["obligations"][KEY]["source_cumulative_amount"] = "12.345679"
    assert build_preview(state2, native, SELECTION, later)[0]["preview_hash"] != reviewed["preview_hash"]
    assert build_preview(state, native_context("24.57"), SELECTION, later)[0]["preview_hash"] != reviewed["preview_hash"]


@pytest.mark.parametrize("payload_change", [
    {"amount": "99.99"}, {"correction_approved": True}, {"correction_id": "client-id"},
    {"confirm_reviewed": "true"}, {"confirm_reviewed": 1}, {"expected_revision": True},
    {"days": [DAY, DAY]}, {"days": ["2030-04-03"]}, {"days": []},
    {"selection": {**SELECTION, "date_to": "2030-05-03"}},
])
def test_input_rejects_client_amounts_flags_and_unbounded_or_ambiguous_days(payload_change):
    reviewed = review_context()[2]
    with pytest.raises(ValidationError):
        AdCorrectionApproval.model_validate(approved_payload(reviewed, **payload_change))


def test_stale_preview_nonowner_and_unreviewed_day_rejected():
    state, _, reviewed, _ = review_context()
    for payload, actor in [(approved_payload(reviewed, expected_revision=8), OWNER),
        (approved_payload(reviewed, expected_preview_hash="f" * 64), OWNER),
        (approved_payload(reviewed), "employee"), (approved_payload(reviewed, confirm_reviewed=False), OWNER)]:
        with pytest.raises(HTTPException):
            _check_approval(state, {"actor_id": actor, "payload": payload}, reviewed)


def test_applied_parts_receipt_audit_and_replay_preserve_manual_history():
    before, _, reviewed, private = review_context()
    before["movements"] = [{"id": "manual-synthetic", "amount": "3.00", "occurred_at": "2030-04-01T03:00:00+00:00"}]
    before["openings"] = {"synthetic-opening": {"amount": "4.00"}}
    before["requests"] = {"old-request": {"hash": "old-hash", "result": {"ok": True}}}
    private[DAY]["correction_id"] = "synthetic-approved"
    source = {**private[DAY]["snapshot"], "correction_approved": True, "correction_id": "synthetic-approved"}
    result = reconcile(before, {"ad_snapshots": [source]}, STAMP)
    approval = {"actor_id": OWNER, "payload": approved_payload(reviewed)}
    receipt = record_approval(before, result, approval, {"reviewed": reviewed, "private": private}, STAMP)
    assert approval_receipt(result, approval) == receipt
    assert receipt["calculation_revision"] == 8
    assert receipt["days"][0]["business_date"] == DAY
    assert list(receipt["days"][0]["after"]) == [KEY]
    assert result["movements"] == before["movements"] and result["openings"] == before["openings"]
    assert result["requests"]["old-request"] == before["requests"]["old-request"]
    assert result["audit"][-1]["actor_id"] == OWNER
    changed = {**approval, "payload": {**approval["payload"], "reason": "Different repeated request"}}
    with pytest.raises(HTTPException, match="409"):
        approval_receipt(result, changed)


@pytest.mark.parametrize("damage", ["amount", "date", "fact", "manual"])
def test_a_false_success_cannot_create_an_approval_receipt(damage):
    before, _, reviewed, private = review_context()
    before["movements"] = [{"id": "manual-synthetic", "amount": "3.00"}]
    private[DAY]["correction_id"] = "synthetic-approved"
    source = {**private[DAY]["snapshot"], "correction_approved": True, "correction_id": "synthetic-approved"}
    result = reconcile(before, {"ad_snapshots": [source]}, STAMP)
    if damage == "amount": result["engine"]["obligations"][KEY]["confirmed"] = "99.00"
    if damage == "date": result["engine"]["obligations"][KEY]["business_date"] = "2030-04-04"
    if damage == "fact": result["engine"]["facts"].clear()
    if damage == "manual": result["movements"].clear()
    with pytest.raises(HTTPException):
        record_approval(before, result, {"actor_id": OWNER, "payload": approved_payload(reviewed)},
                        {"reviewed": reviewed, "private": private}, STAMP)
    assert not result["requests"] and not result["audit"]
