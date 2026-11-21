"""Proof-only financial cohort exclusion; no order/ledger/database mutations."""
from copy import deepcopy

import pytest

from awaiting_payment_eligibility import (
    AWAITING_PAYMENT_RAW_PROJECTION, ELIGIBILITY_FIELD, PROOF_SHAPE_FIELD, PRIOR_AMOUNT_FIELDS,
    PRIOR_REFERENCE_FIELDS, PRIOR_STATUS_FIELDS, attach_financial_cohort_eligibility,
    financial_cohort_eligibility,
)


VERSION = "2026-11-21T09:00:00+00:00"
AS_OF = "2026-11-21T10:00:00+00:00"


def fixture(method="waiting", number="300000001"):
    raw = {
        "id": "native-" + number, "reference_id": number, "currency": "SAR",
        "total": {"amount": "100.00", "currency": "SAR"},
        "payment_method": method, "payment_methods": [{"payment_method": method, "amount": "0.00"}],
        "status": {"slug": "awaiting_payment"}, "is_pending_payment": True,
        "created_at": "2026-11-20T09:00:00+00:00", "updated_at": VERSION,
        "payment_actions": {"remaining_action": {
            "paid_amount": {"amount": "0.00", "currency": "SAR"},
            "remaining_amount": {"amount": "100.00", "currency": "SAR"},
            "has_remaining_amount": True,
        }},
    }
    source = {"user_id": "owner", "order_number": number, "order_id": raw["id"],
              "payment_method": method, "paid_amount": 0, "payment_collection_status": "unpaid",
              "g47_salla_snapshot": {"source_updated_at": VERSION}}
    return source, raw


def evaluate(source, raw, **kwargs):
    original = deepcopy((source, raw))
    decision = financial_cohort_eligibility(source, raw, owner="owner", as_of=AS_OF, **kwargs)
    assert (source, raw) == original
    return decision


def set_path(row, path, value):
    parts = path.split(".")
    for key in parts[:-1]:
        row = row.setdefault(key, {})
    row[parts[-1]] = value


def test_native_salla_pending_payment_date_objects_and_refund_action_are_zero_paid_proof():
    # Sanitized shape observed from the authorized native read; identities and
    # amounts are anonymous fixtures, not customer records.
    source, raw = fixture()
    raw["id"], raw["reference_id"] = 123400001, 300000001
    source["order_id"] = 123400001
    raw["date"] = {"date": "2026-11-01 00:03:02.000000", "timezone_type": 3, "timezone": "Asia/Riyadh"}
    raw["updated_at"] = {"date": "2026-11-01 00:03:03.000000", "timezone_type": 3, "timezone": "Asia/Riyadh"}
    raw.pop("created_at")
    source["g47_salla_snapshot"]["source_updated_at"] = "2026-10-31T21:03:03+00:00"
    raw["status"] = {"slug": "payment_pending"}
    raw.pop("is_pending_payment")
    raw["payment_methods"] = [{"amount": 0, "payment_method": "waiting", "transaction_reference": None, "provider": None}]
    raw["amounts"] = {"total": raw.pop("total")}
    raw["payment_actions"]["refund_action"] = {"paid_amount": {"amount": 0, "currency": "SAR"},
        "refund_amount": {"amount": 0, "currency": "SAR"}, "refunded_transactions": [], "pending_refund_amount": None}
    assert evaluate(source, raw)["eligible"] is False
    source["g47_salla_snapshot"]["source_updated_at"] = "2026-10-31T21:03:02+00:00"
    assert evaluate(source, raw)["review_required"] is True


def test_native_only_placeholder_is_review_even_when_a_mixed_batch_loads_full_proof():
    normalized, normal_raw = fixture(number="300000001")
    missing, missing_raw = fixture(number="300000002")
    missing.pop("payment_method")
    assert evaluate(missing, missing_raw)["reason_code"] == "awaiting_payment_normalized_method_missing"
    projected = [{**row, "raw_by_source": {"salla_direct": raw}, PROOF_SHAPE_FIELD: True}
                 for row, raw in ((normalized, normal_raw), (missing, missing_raw))]
    result = attach_financial_cohort_eligibility([normalized, missing], projected, owner="owner", as_of=AS_OF)
    assert result[0][ELIGIBILITY_FIELD]["eligible"] is False
    assert result[1][ELIGIBILITY_FIELD]["eligible"] is True
    assert result[1][ELIGIBILITY_FIELD]["review_required"] is True


@pytest.mark.parametrize("method", ["waiting", "wating", " WATING ", {"code": "waiting", "slug": "wating"}])
@pytest.mark.parametrize("status", ["awaiting_payment", "waiting", "completed"])
def test_explicit_zero_paid_full_due_excludes_only_financial_cohort(method, status):
    source, raw = fixture(method)
    raw["status"] = {"slug": status}
    result = evaluate(source, raw)
    assert result == {"eligible": False, "review_required": False, "reason_code": "awaiting_payment_zero_paid",
                      "currency": "SAR", "paid": "0.00", "remaining": "100.00", "source_updated_at": VERSION}


@pytest.mark.parametrize("method", ["mada", "tabby", "tamara_installment", "emkan", "paypal", "bank", "cod",
                                   "cash_on_delivery", "customer_wallet", "waiting_gateway", "unknown_rail"])
def test_real_methods_cod_and_order_status_waiting_are_unchanged(method):
    source, raw = fixture(method)
    source["order_status"] = "waiting"
    raw["status"] = {"slug": "waiting"}
    assert evaluate(source, raw) == {"eligible": True, "review_required": False, "reason_code": None}


@pytest.mark.parametrize(("paid", "due"), [("25.00", "75.00"), ("100.00", "0.00")])
def test_stale_placeholder_never_removes_positive_or_partial_paid_order(paid, due):
    source, raw = fixture("wating")
    action = raw["payment_actions"]["remaining_action"]
    action.update(paid_amount={"amount": paid, "currency": "SAR"},
                  remaining_amount={"amount": due, "currency": "SAR"}, has_remaining_amount=due != "0.00")
    raw["payment_methods"][0]["amount"] = paid
    raw["is_pending_payment"] = False
    raw["status"] = {"slug": "completed"}
    result = evaluate(source, raw)
    assert result["eligible"] and result["review_required"]


@pytest.mark.parametrize("case", ["normalized-real", "actual-real", "native-real", "detail-real", "missing-native",
                                   "conflicting-codes", "real-tender", "missing-tender-method"])
def test_method_disagreement_is_review_not_exclusion(case):
    source, raw = fixture()
    if case == "normalized-real": source["payment_method"] = "mada"
    elif case == "actual-real": source["actual_payment_method"] = "cod"
    elif case == "native-real": raw["payment_method"] = "bank"
    elif case == "detail-real": raw["payment"] = {"method": "paypal"}
    elif case == "missing-native": raw.pop("payment_method")
    elif case == "conflicting-codes": raw["payment_method"] = {"code": "waiting", "slug": "cod"}
    elif case == "real-tender": raw["payment_methods"][0]["payment_method"] = "cash_on_delivery"
    else: raw["payment_methods"][0].pop("payment_method")
    result = evaluate(source, raw)
    assert result["eligible"] and result["review_required"]


@pytest.mark.parametrize("path", PRIOR_AMOUNT_FIELDS)
def test_positive_prior_amount_markers_retain_order(path):
    source, raw = fixture()
    set_path(source, path, "10.00")
    assert evaluate(source, raw)["reason_code"] == "awaiting_payment_prior_payment_evidence"


@pytest.mark.parametrize("path", PRIOR_REFERENCE_FIELDS)
def test_prior_payment_reference_retains_even_when_native_amount_is_zero(path):
    source, raw = fixture()
    set_path(source, path, "previous-proof")
    result = evaluate(source, raw)
    assert result["eligible"] and result["reason_code"] == "awaiting_payment_prior_payment_evidence"


@pytest.mark.parametrize("path", PRIOR_STATUS_FIELDS)
@pytest.mark.parametrize("status", ["paid", "fully_captured", {"slug": "paid"}, ["paid"]])
def test_paid_status_marker_retains_even_when_native_amount_is_zero(path, status):
    source, raw = fixture()
    set_path(source, path, status)
    assert evaluate(source, raw)["reason_code"] == "awaiting_payment_prior_payment_evidence"


@pytest.mark.parametrize("case", ["known-ledger", "verified", "actual-fee", "bad-paid", "nonfinite-paid", "negative-paid",
                                   "native-paid", "native-paid-status", "tender-reference", "malformed-paid-object",
                                   "malformed-source-payment", "native-transaction-reference", "pending-refund"])
def test_other_prior_or_conflicting_financial_evidence_is_never_silently_removed(case):
    source, raw = fixture()
    options = {}
    if case == "known-ledger": options["known_payment"] = True
    elif case == "verified": source["payment_verified"] = True
    elif case == "actual-fee": source.update(payment_fee_status="actual", actual_payment_fee=0)
    elif case == "bad-paid": source["paid_amount"] = "unknown"
    elif case == "nonfinite-paid": source["paid_amount"] = "NaN"
    elif case == "negative-paid": source["paid_amount"] = -1
    elif case == "native-paid": raw["paid_amount"] = 10
    elif case == "native-paid-status": raw["payment_status"] = "captured"
    elif case == "tender-reference": raw["payment_methods"][0]["transaction_reference"] = "historic-payment"
    elif case == "malformed-paid-object": source["paid_amount"] = {"value": "50.00"}
    elif case == "malformed-source-payment": source["payment"] = True
    elif case == "native-transaction-reference": raw["payment"] = {"transaction_reference": "historic-payment"}
    else: raw["payment_actions"]["refund_action"] = {"pending_refund_amount": "10.00"}
    result = evaluate(source, raw, **options)
    assert result["eligible"] and result["review_required"]


@pytest.mark.parametrize("fault", [
    "owner", "missing-owner", "number", "native-number", "native-id", "source-ids-disagree", "missing-id", "boolean-id",
    "missing-version", "missing-watermark", "stale-watermark", "component-pending", "refresh", "cancelled-watermark",
    "newer-status", "future", "created-after-version", "naive-version", "paid-absent", "due-absent", "total-zero",
    "currency", "amount-currency", "conflicting-total", "due-mismatch", "false-remaining", "refund", "cancelled-status",
    "malformed-payment", "malformed-actions", "malformed-tenders", "tender-currency", "tender-amount-missing",
])
def test_incomplete_stale_or_conflicting_native_proof_preserves_order(fault):
    source, raw = fixture()
    action = raw["payment_actions"]["remaining_action"]
    if fault == "owner": source["user_id"] = "other"
    elif fault == "missing-owner": source.pop("user_id")
    elif fault == "number": source["order_number"] = "different"
    elif fault == "native-number": raw["order_number"] = "different"
    elif fault == "native-id": raw["id"] = "different"
    elif fault == "source-ids-disagree": source["salla_order_id"] = "different"
    elif fault == "missing-id": source.pop("order_id")
    elif fault == "boolean-id": source["order_id"] = raw["id"] = True
    elif fault == "missing-version": raw.pop("updated_at")
    elif fault == "missing-watermark": source.pop("g47_salla_snapshot")
    elif fault == "stale-watermark": source["g47_salla_snapshot"]["source_updated_at"] = "2026-11-20T09:00:00+00:00"
    elif fault == "component-pending": source["g47_salla_snapshot"]["component_pending"] = True
    elif fault == "refresh": source["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    elif fault == "cancelled-watermark": source["g47_salla_snapshot"]["cancelled"] = True
    elif fault == "newer-status": source["last_salla_direct_status_source_updated_at"] = AS_OF
    elif fault == "future": raw["updated_at"] = source["g47_salla_snapshot"]["source_updated_at"] = "2026-11-22T09:00:00+00:00"
    elif fault == "created-after-version": raw["created_at"] = AS_OF
    elif fault == "naive-version": raw["updated_at"] = source["g47_salla_snapshot"]["source_updated_at"] = "2026-11-21T09:00:00"
    elif fault == "paid-absent": action.pop("paid_amount")
    elif fault == "due-absent": action.pop("remaining_amount")
    elif fault == "total-zero": raw["total"]["amount"] = "0.00"
    elif fault == "currency": raw["currency"] = "S1R"
    elif fault == "amount-currency": action["paid_amount"]["currency"] = "QAR"
    elif fault == "conflicting-total": raw["total_amount"] = "200.00"
    elif fault == "due-mismatch": action["remaining_amount"]["amount"] = "99.00"
    elif fault == "false-remaining": action["has_remaining_amount"] = False
    elif fault == "refund": raw["payment_actions"]["refund_action"] = {"refund_amount": "1.00"}
    elif fault == "cancelled-status": raw["status"] = {"slug": "cancelled"}
    elif fault == "malformed-payment": raw["payment"] = False
    elif fault == "malformed-actions": raw["payment_actions"] = []
    elif fault == "malformed-tenders": raw["payment_methods"] = False
    elif fault == "tender-currency": raw["payment_methods"][0]["currency"] = "QAR"
    else: raw["payment_methods"][0].pop("amount")
    result = evaluate(source, raw)
    assert result["eligible"] and result["review_required"], (fault, result)


def test_projection_contains_proof_and_guards_without_customer_or_secret_links():
    projection = AWAITING_PAYMENT_RAW_PROJECTION
    assert all(projection[field] == 1 for field in PRIOR_AMOUNT_FIELDS + PRIOR_REFERENCE_FIELDS + PRIOR_STATUS_FIELDS)
    assert projection["raw_by_source.salla_direct.payment_actions.remaining_action.paid_amount"] == 1
    assert not any(any(fragment in field for fragment in ("customer", "address", "mobile", "email", "checkout", "receipt_url"))
                   for field in projection)


def test_attach_preserves_registry_order_counts_and_inputs_and_scopes_tenant():
    waiting, raw_waiting = fixture()
    cod, raw_cod = fixture("cod", "300000002")
    rows = [{**waiting, PROOF_SHAPE_FIELD: True, "raw_by_source": {"salla_direct": raw_waiting}},
            {**cod, PROOF_SHAPE_FIELD: True, "raw_by_source": {"salla_direct": raw_cod}}]
    original = deepcopy(([waiting, cod], rows))
    attached = attach_financial_cohort_eligibility([waiting, cod], rows, owner="owner", as_of=AS_OF)
    assert len(attached) == 2 and [row["order_number"] for row in attached] == [waiting["order_number"], cod["order_number"]]
    assert not attached[0][ELIGIBILITY_FIELD]["eligible"] and attached[1][ELIGIBILITY_FIELD]["eligible"]
    assert ([waiting, cod], rows) == original
    assert attached[0] is not waiting
    foreign_rows = [{**rows[0], "user_id": "other"}]
    assert attach_financial_cohort_eligibility([waiting], foreign_rows, owner="owner")[0][ELIGIBILITY_FIELD]["review_required"]


@pytest.mark.parametrize("fault", ["missing", "duplicate-source", "duplicate-registry", "projected-paid", "projected-method",
                                   "source-method", "source-version", "known-ledger", "missing-shape", "invalid-shape"])
def test_attach_never_excludes_ambiguous_or_independently_paid_identity(fault):
    source, raw = fixture()
    projected = {**deepcopy(source), PROOF_SHAPE_FIELD: True, "raw_by_source": {"salla_direct": raw}}
    orders, rows, options = [source], [projected], {}
    if fault == "missing": rows = []
    elif fault == "duplicate-source": rows.append(deepcopy(projected))
    elif fault == "duplicate-registry": orders.append(deepcopy(source))
    elif fault == "projected-paid": projected["paid_amount"] = 15
    elif fault == "projected-method": projected["payment_method"] = "mada"
    elif fault == "source-method": source["payment_method"] = "mada"
    elif fault == "source-version": source["g47_salla_snapshot"]["source_updated_at"] = "2026-11-20T09:00:00+00:00"
    elif fault == "known-ledger": options["known_payment_order_ids"] = [source["order_id"]]
    elif fault == "missing-shape": projected.pop(PROOF_SHAPE_FIELD)
    else: projected[PROOF_SHAPE_FIELD] = False
    original = deepcopy((orders, rows))
    attached = attach_financial_cohort_eligibility(orders, rows, owner="owner", as_of=AS_OF, **options)
    assert len(attached) == len(orders)
    assert all(row[ELIGIBILITY_FIELD]["eligible"] and row[ELIGIBILITY_FIELD]["review_required"] for row in attached)
    assert (orders, rows) == original


def test_real_mongo_projection_roundtrip_retains_financial_guards_and_invalid_shapes():
    """Actual Mongo dotted projections drop scalar parents; mocks do not suffice."""
    import os
    from uuid import uuid4
    from pymongo import MongoClient

    uri = os.environ.get("OPERATIONAL_TEST_MONGO_URI", "mongodb://127.0.0.1:27305")
    assert uri.startswith("mongodb://127.0.0.1:"), "Only dedicated local test Mongo is allowed"
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    db = client["awaiting_projection_" + uuid4().hex]
    try:
        client.admin.command("ping")
        cases = ("proved-unpaid", "raw-captured", "raw-verified", "raw-status", "transaction-reference", "pending-refund",
                 "raw-payment-scalar", "raw-amounts-scalar", "raw-actions-scalar", "raw-remaining-scalar",
                 "raw-refund-scalar", "source-payment-scalar", "source-accounting-scalar", "source-balance-scalar")
        for case in cases:
            source, raw = fixture()
            if case == "raw-captured": raw["captured_amount"] = "50.00"
            elif case == "raw-verified": raw["payment_verified"] = True
            elif case == "raw-status": raw["payment_status_provider"] = "paid"
            elif case == "transaction-reference": raw["payment"] = {"transaction_reference": "prior-payment"}
            elif case == "pending-refund": raw["payment_actions"]["refund_action"] = {"pending_refund_amount": "10.00"}
            elif case == "raw-payment-scalar": raw["payment"] = True
            elif case == "raw-amounts-scalar": raw["amounts"] = True
            elif case == "raw-actions-scalar":
                raw.update(payment_actions=True, paid_amount=0, remaining_amount=100)
            elif case == "raw-remaining-scalar":
                raw["payment_actions"]["remaining_action"] = True
                raw.update(paid_amount=0, remaining_amount=100)
            elif case == "raw-refund-scalar": raw["payment_actions"]["refund_action"] = True
            elif case == "source-payment-scalar": source["payment"] = True
            elif case == "source-accounting-scalar": source["accounting"] = True
            elif case == "source-balance-scalar": source["salla_paid_sar_evidence"] = True
            original = deepcopy((source, raw))
            db.orders.insert_one({"_id": case, **source, "raw_by_source": {"salla_direct": raw}})
            projected = db.orders.find_one({"user_id": "owner", "_id": case}, AWAITING_PAYMENT_RAW_PROJECTION)
            decision = attach_financial_cohort_eligibility([source], [projected], owner="owner", as_of=AS_OF)[0][ELIGIBILITY_FIELD]
            assert decision["eligible"] is (case != "proved-unpaid"), (case, decision)
            assert decision["review_required"] is (case != "proved-unpaid"), (case, decision)
            assert (source, raw) == original
    finally:
        client.drop_database(db.name)
        client.close()
