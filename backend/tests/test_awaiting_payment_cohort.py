from copy import deepcopy

from awaiting_payment_cohort import split_financial_cohort
from awaiting_payment_eligibility import ELIGIBILITY_FIELD


def row(number, **decision):
    return {"user_id": "owner", "order_number": number, "order_id": "native-" + number,
            ELIGIBILITY_FIELD: decision}


def test_checked_unpaid_exclusion_preserves_registry_count_and_original_rows():
    orders = [row("paid", eligible=True),
              row("unpaid", eligible=False, history_checked=True, reason_code="awaiting_payment_zero_paid")]
    before = deepcopy(orders)
    financial, coverage = split_financial_cohort(orders)
    assert [order["order_number"] for order in financial] == ["paid"]
    assert coverage["total_orders"] == 2 and coverage["financial_eligible_orders"] == 1
    assert coverage["excluded_unpaid_orders"] == 1 and coverage["review_orders"] == 0
    assert coverage["complete"] and orders == before


def test_unchecked_exclusion_and_unknown_evidence_stay_for_review():
    orders = [row("unverified", eligible=False),
              row("unknown", eligible=True, review_required=True, reason_code="source_missing")]
    before = deepcopy(orders)
    financial, coverage = split_financial_cohort(orders)
    assert len(financial) == 2 and coverage["excluded_unpaid_orders"] == 0
    assert coverage["review_orders"] == 2 and not coverage["complete"]
    assert financial[0][ELIGIBILITY_FIELD]["eligible"] is True
    assert coverage["review_reason_counts"] == {"awaiting_payment_history_unverified": 1, "source_missing": 1}
    assert orders == before


def test_complete_empty_financial_cohort_and_digest_tracks_guard_decision():
    orders = [row("one", eligible=False, history_checked=True), row("two", eligible=False, history_checked=True)]
    financial, coverage = split_financial_cohort(orders)
    assert financial == [] and coverage["complete"]
    assert coverage["total_orders"] == coverage["excluded_unpaid_orders"] == 2
    assert coverage == split_financial_cohort(list(reversed(orders)))[1]
    orders[0][ELIGIBILITY_FIELD].update(eligible=True, review_required=True, reason_code="prior_capture")
    assert coverage["eligibility_digest"] != split_financial_cohort(orders)[1]["eligibility_digest"]


def test_nonwaiting_and_cod_without_derived_decision_keep_existing_policy():
    orders = [{"order_number": "mada", "payment_method": "mada"},
              {"order_number": "cod", "payment_method": "cod", "order_status": "waiting"}]
    financial, coverage = split_financial_cohort(orders)
    assert financial == orders and coverage["total_orders"] == coverage["financial_eligible_orders"] == 2
    assert coverage["complete"] and coverage["review_orders"] == coverage["excluded_unpaid_orders"] == 0


def test_review_conflict_never_authorizes_an_exclusion_even_with_checked_history():
    source = row("contradiction", eligible=False, history_checked=True, review_required=True,
                 reason_code="prior_financial_history")
    financial, coverage = split_financial_cohort([source])
    assert financial[0][ELIGIBILITY_FIELD]["eligible"] is True
    assert coverage["excluded_unpaid_orders"] == 0 and coverage["review_orders"] == 1
    assert not coverage["complete"]
