"""Narrow, read-only exclusion of proved unpaid Salla payment placeholders.

``eligible=True`` preserves the caller's existing cohort decision; it does not
certify revenue, collection, a fee, or any ledger entry. Only a current, bound
native zero-paid/full-due observation for waiting/wating can return False.
Neither order status nor COD policy is changed. Callers retain the full order
registry and may filter a separate financial cohort using the attached result.

This module cannot inspect stored capture/receipt ledgers. A caller with such
facts must pass ``known_payment=True`` (or known_payment_order_ids to attach).
The bounded projected order markers below are conservative additional guards,
not a claim that no independent financial history exists.
"""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from operational_payment_evidence import _amount, _instant
from payment_methods import is_unselected_payment_method
from salla_payment_collection import order_page_source_current, provider_order_page_balance


ELIGIBILITY_FIELD = "awaiting_payment_eligibility"
PROOF_SHAPE_FIELD = "awaiting_payment_proof_shape_valid"
_RAW = "raw_by_source.salla_direct."
_PAID_STATES = frozenset({"paid", "captured", "settled", "partial", "partially_paid",
                         "fully_captured", "partially_captured", "refunded", "partially_refunded", "مدفوع", "تم الدفع"})
# Any positive amount, explicit paid marker or payment reference retains the
# order for review. None of these markers alone is used to book money.
PRIOR_AMOUNT_FIELDS = (
    "paid_amount", "captured_amount", "refunded_amount", "actual_refund_amount",
    "actual_partial_refund_amount", "qoyod_paid_amount", "payment.paid_amount",
    "accounting.paid_amount", "salla_paid_sar_evidence.paid_sar", "bank_credit.amount",
    "pending_refund_amount", "payment.pending_refund_amount", "payment_actions.refund_action.pending_refund_amount",
    "actual_payment_fee",
)
PRIOR_REFERENCE_FIELDS = ("qoyod_payment_id", "accounting.payment_id", "payment.reference",
                          "payment.transaction_reference", "payment.paid_at", "payment.captured_at", "payment.transaction_id")
PRIOR_STATUS_FIELDS = ("payment_status", "payment_collection_status", "payment_status_provider",
                       "qoyod_status", "accounting.status", "payment.status", "payment.collection_status")
SOURCE_FIELDS = (
    "user_id", "order_number", "order_id", "salla_order_id", "payment_method", "actual_payment_method",
    "currency", "original_currency",
    "g47_salla_snapshot", "last_salla_direct_status_source_updated_at", "payment_verified",
    "payment_fee_status", "actual_payment_fee", *PRIOR_AMOUNT_FIELDS, *PRIOR_REFERENCE_FIELDS, *PRIOR_STATUS_FIELDS,
)
RAW_FIELDS = (
    "id", "reference_id", "order_number", "currency", "amounts.total", "amounts.currency", "total", "total_amount",
    "payment_method", "payment_methods", "status", "updated_at", "date_updated", "date", "created_at",
    "is_pending_payment", "paid_amount", "remaining_amount", "has_remaining_amount", "refunded_amount",
    "payment_status", "payment_collection_status", "payment.method", "payment.status", "payment.collection_status",
    "payment.paid_amount", "payment.remaining_amount", "payment.has_remaining_amount", "payment.currency",
    "payment.reference", "payment.paid_at", "payment.refunded_amount",
    "payment_actions.remaining_action.paid_amount", "payment_actions.remaining_action.remaining_amount",
    "payment_actions.remaining_action.has_remaining_amount", "payment_actions.remaining_action.currency",
    "payment_actions.refund_action.paid_amount", "payment_actions.refund_action.refund_amount",
    "payment_actions.refund_action.refunded_transactions", "payment_actions.refund_action.pending_refund_amount",
    "payment_actions.refund_action.currency",
    *PRIOR_AMOUNT_FIELDS, *PRIOR_REFERENCE_FIELDS, *PRIOR_STATUS_FIELDS,
    "payment_verified", "payment_fee_status", "actual_payment_fee",
)
# No customer, address, bank account, receipt media or checkout-link fields.
AWAITING_PAYMENT_RAW_PROJECTION = {"_id": 0, **{field: 1 for field in SOURCE_FIELDS},
                                  **{_RAW + field: 1 for field in RAW_FIELDS}}
# Mongo drops malformed scalar parents under dotted inclusion projection. A
# server-computed type attestation preserves that contradiction without reading
# receipt/checkout URLs or other unrelated object fields.
_GUARD_PARENTS = {".".join(field.split(".")[:index]) for field in PRIOR_AMOUNT_FIELDS + PRIOR_REFERENCE_FIELDS + PRIOR_STATUS_FIELDS
                  for index in range(1, len(field.split(".")))}
_SHAPE_PATHS = _GUARD_PARENTS | {_RAW + path for path in _GUARD_PARENTS | {
    "amounts", "payment", "payment_actions", "payment_actions.remaining_action", "payment_actions.refund_action"}}
AWAITING_PAYMENT_RAW_PROJECTION[PROOF_SHAPE_FIELD] = {
    "$and": [{"$in": [{"$type": "$" + path}, ["object", "missing", "null"]]} for path in sorted(_SHAPE_PATHS)]}


def _result(reason=None, *, excluded=False, review=False):
    return {"eligible": not excluded, "review_required": review, "reason_code": reason}


def _get(row, path):
    for key in path.split("."):
        if not isinstance(row, dict):
            return None
        row = row.get(key)
    return row


def _method(value):
    if value in (None, ""):
        return "missing"
    if isinstance(value, dict):
        kinds = {_method(value[key]) for key in ("code", "slug") if value.get(key) not in (None, "")}
        return kinds.pop() if len(kinds) == 1 else "conflict"
    if not isinstance(value, str):
        return "conflict"
    return "placeholder" if is_unselected_payment_method(value) else "real"


def _has_prior_payment(source):
    for field in PRIOR_AMOUNT_FIELDS:
        value = source
        for key in field.split("."):
            if value is None:
                break
            if not isinstance(value, dict):
                return True
            value = value.get(key)
        if isinstance(value, dict):
            if "amount" not in value:
                return True
            value = value.get("amount")
            if value is None:
                return True
        if value not in (None, ""):
            if isinstance(value, bool):
                return True
            try:
                amount = Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                return True
            if not amount.is_finite() or amount != 0:
                return True
    if source.get("payment_verified") not in (None, False):
        return True
    if any(_get(source, field) not in (None, "", False) for field in PRIOR_REFERENCE_FIELDS):
        return True
    for field in PRIOR_STATUS_FIELDS:
        status = _get(source, field)
        if status not in (None, "") and (not isinstance(status, str) or status.strip().casefold() in _PAID_STATES):
            return True
    # Actual fees must remain visible even when the order's method is stale.
    return source.get("payment_fee_status") == "actual"


def financial_cohort_eligibility(source, raw, *, owner, known_payment=False, as_of=None):
    """Return a conservative cohort decision; never modify either input.

    ``raw`` must be the full native proof, or be used through the attach helper
    with AWAITING_PAYMENT_RAW_PROJECTION (which checks projection shape loss).
    ``as_of`` optionally supplies the caller's source cutoff. Absence leaves
    period selection with that caller; source/watermark equality is mandatory.
    ``known_payment`` preserves independently loaded capture/receipt history.
    """
    raw = raw if isinstance(raw, dict) else {}
    source = source if isinstance(source, dict) else {}
    native_method = _method(raw.get("payment_method"))
    source_methods = [_method(source.get(field)) for field in ("payment_method", "actual_payment_method")]
    payment = raw.get("payment") or {}
    detail_method = _method(payment.get("method")) if isinstance(payment, dict) else "conflict"
    candidates = [native_method, detail_method, *source_methods]
    if "placeholder" not in candidates:
        return _result()
    if any(kind in {"real", "conflict"} for kind in candidates):
        return _result("awaiting_payment_method_conflict", review=True)
    if native_method != "placeholder":
        return _result("awaiting_payment_native_method_missing", review=True)
    if "placeholder" not in source_methods:
        return _result("awaiting_payment_normalized_method_missing", review=True)
    if known_payment is not False or _has_prior_payment(source) or _has_prior_payment(raw):
        return _result("awaiting_payment_prior_payment_evidence", review=True)
    identities = [source.get("order_number"), raw.get("id"), raw.get("reference_id") or raw.get("order_number")]
    identities.extend(source[field] for field in ("salla_order_id", "order_id") if source.get(field) is not None)
    if (not owner or source.get("user_id") != owner
            or any(isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip() for value in identities)
            or not (source.get("salla_order_id") or source.get("order_id"))
            or any(str(source[field]) != str(raw.get("id")) for field in ("salla_order_id", "order_id")
                   if source.get(field) not in (None, ""))):
        return _result("awaiting_payment_identity_incomplete", review=True)
    watermark = source.get("g47_salla_snapshot")
    try:
        if (not isinstance(watermark, dict) or not watermark.get("source_updated_at")
                or not (raw.get("updated_at") or raw.get("date_updated"))
                or not order_page_source_current(source, raw)):
            return _result("awaiting_payment_source_not_current", review=True)
        updated = _instant(raw.get("updated_at") or raw.get("date_updated"))
        created = raw.get("date") or raw.get("created_at")
        status_version = source.get("last_salla_direct_status_source_updated_at")
        if ((created is not None and _instant(created) > updated)
                or (status_version is not None and _instant(status_version) > updated)
                or (as_of is not None and updated > _instant(as_of))):
            return _result("awaiting_payment_source_not_current", review=True)
        if any(raw.get(field) is not None and not isinstance(raw[field], dict) for field in ("payment", "amounts", "payment_actions")):
            return _result("awaiting_payment_source_conflict", review=True)
        amounts = raw.get("amounts") or {}
        totals = [value for value in (amounts.get("total"), raw.get("total"), raw.get("total_amount")) if value is not None]
        currency = raw.get("currency") or amounts.get("currency")
        if not currency and totals and isinstance(totals[0], dict):
            currency = totals[0].get("currency")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha() or not currency.isupper():
            return _result("awaiting_payment_amount_evidence_missing", review=True)
        if any(source.get(field) not in (None, "", currency) for field in ("currency", "original_currency")):
            return _result("awaiting_payment_source_conflict", review=True)
        gross = _amount(totals[0], currency) if totals else Decimal(0)
        if gross <= 0:
            return _result("awaiting_payment_amount_evidence_missing", review=True)
        methods = raw.get("payment_methods") if raw.get("payment_methods") is not None else []
        if (not isinstance(methods, list) or len(methods) > 100 or any(not isinstance(row, dict) for row in methods)
                or any(_method(row.get("payment_method")) != "placeholder" for row in methods)):
            return _result("awaiting_payment_method_conflict", review=True)
        if any(row.get("currency") not in (None, currency) for row in methods):
            return _result("awaiting_payment_source_conflict", review=True)
        if any(row.get("transaction_reference") or _amount(row.get("amount"), currency) != 0 for row in methods):
            return _result("awaiting_payment_prior_payment_evidence", review=True)
        remaining = _get(raw, "payment_actions.remaining_action") or {}
        observations = (raw, payment, remaining)
        # Do not let the shared reader's inferred zero/due certify this exclusion.
        if not all(any(row.get(field) is not None for row in observations) for field in ("paid_amount", "remaining_amount")):
            return _result("awaiting_payment_amount_evidence_missing", review=True)
        page = provider_order_page_balance(raw, currency, gross)
        if page is None:
            return _result("awaiting_payment_amount_evidence_missing", review=True)
        if _amount(page["paid"], currency) != 0 or _amount(page["remaining"], currency) != gross:
            return _result("awaiting_payment_positive_payment", review=True)
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return _result("awaiting_payment_source_conflict", review=True)
    return {**_result("awaiting_payment_zero_paid", excluded=True), "currency": currency,
            "paid": "0.00", "remaining": format(gross, ".2f"), "source_updated_at": updated.isoformat()}


def attach_financial_cohort_eligibility(orders, projected_rows, *, owner, known_payment_order_ids=(), as_of=None):
    """Return same-length/order copies with decisions; never remove registry rows.

    Projected rows use AWAITING_PAYMENT_RAW_PROJECTION in an owner-scoped read.
    Both snapshots must permit exclusion; duplicate, conflicting or missing
    source evidence preserves the row for review. IDs in known_payment_order_ids
    are Salla order IDs from independently loaded financial facts.
    """
    grouped = defaultdict(list)
    for row in projected_rows:
        grouped[(row.get("user_id"), str(row.get("order_number") or ""))].append(row)
    counts = Counter((row.get("user_id"), str(row.get("order_number") or "")) for row in orders)
    known = {str(identity) for identity in known_payment_order_ids}
    result = []
    for order in orders:
        key = (order.get("user_id"), str(order.get("order_number") or ""))
        matches = grouped[key]
        raw = _get(matches[0], "raw_by_source.salla_direct") if len(matches) == 1 else None
        prior = any(str(order.get(field)) in known for field in ("salla_order_id", "order_id") if order.get(field))
        decision = financial_cohort_eligibility(order, raw, owner=owner, known_payment=prior, as_of=as_of)
        if len(matches) == 1:
            projected = financial_cohort_eligibility(matches[0], raw, owner=owner, known_payment=prior, as_of=as_of)
            if projected["review_required"] or (decision["eligible"] is False and projected["eligible"]):
                decision = projected
        if decision["eligible"] is False and matches[0].get(PROOF_SHAPE_FIELD) is not True:
            decision = _result("awaiting_payment_projection_shape_unverified", review=True)
        if counts[key] != 1 and (decision["eligible"] is False or decision["review_required"]):
            decision = _result("awaiting_payment_duplicate_identity", review=True)
        result.append({**order, ELIGIBILITY_FIELD: decision})
    return result
