"""Staff bank receipt vs canonical Mezan order, no automatic GL or Salla paid."""

from types import SimpleNamespace as Obj
import pytest

from salla_order_creation.bank_review_staff import (
    StaffBankReviewDenied, reconcile_bank_receipt_with_mezan_order,
    validate_human_bank_decision,
)


def row(**changes):
    data = {
        "owner_id": "owner", "intent_id": "intent-0001",
        "state": "awaiting_human_bank_review",
        "salla_order_id": 90001,
        "salla_reference_id": 301234567,
        "store_bank_id": 123,
        "claimed_amount_sar": "200.00",
        "bank_credit_confirmed": False,
        "payment_marked_paid": False,
    }
    data.update(changes)
    return data


def order(total=200.0,method="bank",paid=0.0,reference=301234567):
    return Obj(
        order_id="90001", order_number=str(reference),
        status="under_review", status_native="بإنتظار المراجعة",
        totals=Obj(total=total,currency="SAR"),
        payment=Obj(method=method,paid_amount=paid,collection_status="unpaid"),
    )


def compare(r=None,o=None):
    return reconcile_bank_receipt_with_mezan_order(
        r or row(), o or order(),
    )


def test_matching_receipt_amount_is_reviewable_not_automatically_paid():
    result = compare()
    assert result["state"] == "ready_for_human_bank_review"
    assert result["amount_match"] is True
    assert result["bank_credit_confirmed"] is False
    assert result["fulfillment_released"] is False


def test_different_receipt_amount_never_marks_bank_transfer_paid():
    result = compare(o=order(total=210.0))
    assert result["state"] == "receipt_amount_differs_from_salla_order"
    assert result["amount_match"] is False


def test_wrong_salla_order_ref_cannot_use_customer_receipt():
    assert compare(o=order(reference=8888))["state"] == "wrong_salla_order"


@pytest.mark.parametrize("method,paid", [
    ("credit_card", 0), ("cod", 0), ("bank", 200), ("bank_transfer", 10),
])
def test_nonbank_or_already_paid_payment_rejected(method,paid):
    assert compare(o=order(method=method,paid=paid))["amount_match"] is False


def test_staff_verified_bank_credit_only_queues_accounting_not_gl():
    verification = validate_human_bank_decision(
        decision="bank_credit_seen",
        review=row(),
        reconciliation=compare(),
        reviewer_id="staff-01",
        statement_reference="SYNTHETIC0099",
        statement_amount_sar="200.00",
    )
    assert verification["state"] == "bank_credit_seen_accounting_hold"
    assert verification["bank_statement_amount_sar"] == "200.00"
    assert verification["accounting_posted"] is False
    assert verification["salla_marked_paid"] is False
    assert verification["fulfillment_released"] is False
    assert "SYNTHETIC0099" not in str(verification)


@pytest.mark.parametrize("changed", [
    {"statement_reference": None},
    {"statement_reference": "x"},
    {"statement_amount_sar": "201.00"},
    {"statement_amount_sar": "NaN"},
])
def test_bank_credit_seen_without_verified_bank_statement_denied(changed):
    args = {
        "decision": "bank_credit_seen", "review": row(),
        "reconciliation": compare(), "reviewer_id": "staff-01",
        "statement_reference": "SYNTHETIC0099",
        "statement_amount_sar": "200.00",
    }
    args.update(changed)
    with pytest.raises(StaffBankReviewDenied):
        validate_human_bank_decision(**args)


def test_screenshot_only_cannot_satisfy_independent_bank_statement_proof():
    with pytest.raises(StaffBankReviewDenied, match="independent_amount_match"):
        validate_human_bank_decision(
            decision="bank_credit_seen",review=row(),
            reconciliation=compare(o=order(total=201.00)),
            reviewer_id="staff-01",
            statement_reference="SYNTHETIC0099",
            statement_amount_sar="200.00",
        )


def test_reject_or_request_more_evidence_stays_unpaid():
    for decision in ("reject_receipt", "needs_customer_evidence"):
        result = validate_human_bank_decision(
            decision=decision,review=row(),
            reconciliation=compare(o=order(total=205)),
            reviewer_id="staff-01",
        )
        assert result["salla_marked_paid"] is False
        assert result["accounting_posted"] is False
        assert result["fulfillment_released"] is False


def test_second_review_action_does_not_reopen_confirmed_review():
    with pytest.raises(StaffBankReviewDenied, match="pending_human_review_required"):
        validate_human_bank_decision(
            decision="bank_credit_seen",
            review=row(state="bank_credit_seen_accounting_hold"),
            reconciliation=compare(),
            reviewer_id="staff-01",
            statement_reference="SYNTHETIC0099",
            statement_amount_sar="200.00",
        )
