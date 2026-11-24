"""Read-only tariff estimates for fully evidenced Salla split payments.

An order-page split proves its amounts and references, not a capture date or an
accounting entry. Price each distinct external transaction only when its saved
capture tariff is unchanged over the entire possible payment interval. Internal
customer-wallet money is part of the sale, but is not a new gateway transaction.
"""
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from dashboard_cpu_budget import CPUWorkBudget, COST_PROFIT_BUDGET_MS
from operational_fee_methods import fee_rail
from operational_payment_evidence import _instant
from operational_provider_fees import provider_projection
from order_currency import _stored_original_total, currency_code, order_total_sar, money_decimal


SOURCE = "dated_provider_policy_split_tenders"
RIYADH = ZoneInfo("Asia/Riyadh")


def _raw(order):
    source = order.get("_dashboard_fee_source")
    if source is None:
        source = (order.get("raw_by_source") or {}).get("salla_direct")
    return source if isinstance(source, dict) else {}


def _is_wallet(row):
    method = row.get("payment_method") if isinstance(row, dict) else None
    return isinstance(method, str) and method.strip().lower() == "customer_wallet"


async def split_reference_conflicts(orders, owner):
    """Bound duplicate-reference checks to the already selected owner cohort."""
    budget = CPUWorkBudget(COST_PROFIT_BUDGET_MS)
    candidates = set()
    for order in orders:
        await budget.checkpoint()
        if not owner or order.get("user_id") != owner:
            continue
        methods = _raw(order).get("payment_methods")
        if not isinstance(methods, list) or not 2 <= len(methods) <= 100:
            continue
        for method in methods:
            await budget.checkpoint()
            reference = method.get("transaction_reference") if isinstance(method, dict) else None
            if isinstance(reference, (str, int)) and not isinstance(reference, bool) and str(reference).strip():
                candidates.add(str(reference).strip())
    if not candidates:
        return []
    references, conflicts = {}, set()
    for order in orders:
        await budget.checkpoint()
        if order.get("user_id") != owner:
            continue
        identity = (str(order.get("order_id") or ""), str(order.get("order_number") or ""))
        methods = _raw(order).get("payment_methods")
        if not isinstance(methods, list) or len(methods) > 100:
            continue
        for method in methods:
            await budget.checkpoint()
            reference = method.get("transaction_reference") if isinstance(method, dict) else None
            if not isinstance(reference, (str, int)) or isinstance(reference, bool):
                continue
            reference = str(reference).strip()
            if reference in candidates and references.setdefault(reference, identity) != identity:
                conflicts.add(reference)
    return sorted(conflicts)


def _cutoff(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.max, tzinfo=RIYADH)
    if isinstance(value, str) and len(value) == 10:
        return datetime.combine(date.fromisoformat(value), time.max, tzinfo=RIYADH)
    return _instant(value)


def _split_basis(order, raw, context):
    """Validate the bounded native split contract without pricing or posting.

Unsupported shapes stay on the caller's original guarded single-payment path.
In particular, an Apple Pay wrapper and card rail sharing one reference are
already one transaction there and must not acquire a second fixed charge.
"""
    methods = raw.get("payment_methods")
    if not isinstance(methods, list) or not 2 <= len(methods) <= 100 or not all(isinstance(row, dict) for row in methods):
        return None
    references = {str(row.get("transaction_reference")) for row in methods}
    wallet = any(_is_wallet(row) for row in methods)
    if len(references) == 1 and not wallet:
        return None
    # Native identities and SAR page proof are a narrower contract than an
    # arbitrary legacy order, so neither is synthesized from promoted fields.
    if not raw.get("id") or not (raw.get("reference_id") or raw.get("order_number")):
        return None
    currency = currency_code(order.get("original_currency") or order.get("currency"), default="SAR")
    if currency != "SAR":
        return None
    payment = raw.get("payment") or {}
    if not isinstance(payment, dict):
        raise ValueError("provider_payment_shape_invalid")
    if payment.get("captures") or payment.get("split"):
        return None  # Preserve the existing dated-capture/ambiguous split guards.
    owner = context.get("owner_id")
    if not owner or order.get("user_id") != owner:
        raise ValueError("provider_order_page_identity_conflict")
    total = _stored_original_total(order)
    if total is None or Decimal(str(order_total_sar(order))) != total:
        raise ValueError("provider_payment_amount_conflict")
    for container, fields, reason, scalar in (
        (order, ("actual_canceled_amount",), "provider_cancellation_execution_evidence_required", True),
        (order, ("actual_refund_amount", "actual_partial_refund_amount"), "provider_refund_execution_evidence_required", True),
        (payment, ("cancelled",), "provider_cancellation_execution_evidence_required", True),
        (payment, ("refunded",), "provider_refund_execution_evidence_required", False),
        (raw, ("refunded",), "provider_refund_execution_evidence_required", False),
    ):
        for field in fields:
            if field in container and (scalar or container[field] is not None):
                if not scalar and isinstance(container[field], dict) and currency_code(
                        container[field].get("currency"), default=currency) != currency:
                    raise ValueError("provider_refund_currency_conversion_required")
                value = money_decimal(container[field])
                if value is None or value != 0 or scalar and isinstance(container[field], dict):
                    raise ValueError(reason)
    created = raw.get("created_at") or raw.get("date")
    updated = raw.get("updated_at") or raw.get("date_updated")
    if not created or not updated:
        raise ValueError("provider_fee_transaction_date_required")
    start, end, cutoff = _instant(created), _instant(updated), _cutoff(context.get("as_of"))
    if context.get("evidence_as_of") is not None:
        cutoff = min(cutoff, _cutoff(context["evidence_as_of"]))
    if not start <= end <= cutoff:
        raise ValueError("provider_order_page_version_conflict")
    if payment.get("paid_at") is not None and not start <= _instant(payment["paid_at"]) <= end:
        raise ValueError("provider_capture_timestamp_conflict")

    from salla_payment_collection import provider_order_page_balance
    from operational_split_payment import split_payment_evidence

    balance = provider_order_page_balance(raw, currency, total)
    evidence = split_payment_evidence(raw, currency, total, balance,
        owner=owner, order_number=str(order.get("order_number") or ""),
        provider_order_id=str(order.get("order_id") or order.get("salla_order_id") or ""),
        rows=(), source=order, created_at=created, as_of=cutoff)
    if evidence is None:
        return None
    split, issues = evidence
    if not split or issues:
        raise ValueError(issues[0]["code"] if issues else "provider_split_payment_review_required")
    tenders = split["split"]["tenders"]
    conflicts = set(context.get("split_fee_reference_conflicts") or [])
    if any(leg["reference"] in conflicts for leg in tenders):
        raise ValueError("provider_payment_reference_conflict")
    if any(leg["provider"] != "salla" and leg["method"] != "customer_wallet" for leg in tenders):
        raise ValueError("provider_split_payment_review_required")
    return tenders, start, end, cutoff


def internal_wallet_paid_sar(order, context):
    """Retain wallet principal independently of actual-versus-estimated fees.

Unknown native wallet evidence must not become zero cash. Actual statement
fees remain valid, while the caller can withhold only transfer/cash totals.
"""
    raw = _raw(order)
    methods = raw.get("payment_methods")
    if not isinstance(methods, list) or not any(_is_wallet(row) for row in methods):
        return 0.0
    try:
        basis = _split_basis(order, raw, context or {})
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return None
    if basis is None:
        return None
    return float(sum((Decimal(leg["amount"]) for leg in basis[0]
                      if leg["method"] == "customer_wallet"), Decimal(0)))


def estimate_split_fees(order, raw, context):
    """Price each external transaction with its saved, unchanged capture tariff."""
    basis = _split_basis(order, raw, context)
    if basis is None:
        return None
    tenders, start, end, cutoff = basis
    net, vat = Decimal(0), Decimal(0)
    for leg in tenders:
        if leg["method"] == "customer_wallet":
            continue
        rail = fee_rail(leg["method"])
        if rail is None:
            raise ValueError("provider_payment_method_required_for_fee")
        projection = provider_projection({"provider": "salla", "currency": "SAR",
            "gross": leg["amount"], "payment_method": rail, "plan_id": leg.get("plan_id"),
            "event_type": "capture"}, context["policies"], end.astimezone(RIYADH).date(),
            earliest_date=start.astimezone(RIYADH).date(), as_of_date=cutoff.astimezone(RIYADH).date())
        net += Decimal(projection["estimated_fee_net"])
        vat += Decimal(projection["estimated_fee_tax"])
    return net, vat, SOURCE
