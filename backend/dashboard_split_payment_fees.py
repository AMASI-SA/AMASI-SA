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
from operational_payment_evidence import _instant, provider_for
from operational_provider_fees import provider_projection
from order_currency import _stored_original_total, currency_code, order_total_sar, money_decimal


SOURCE = "dated_provider_policy_split_tenders"
SOURCE_CAPTURE = "native_capture_dated_provider_policy_split_tenders"
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
    # A historical report has two clocks: transaction eligibility at its
    # report-day cutoff, and the latest verified source snapshot available to
    # restate that day. Late fulfillment must not invalidate a proven earlier
    # payment, but a later paid event must not enter an older report.
    source_cutoff = _cutoff(context.get("split_fee_source_as_of")
                            or context.get("evidence_as_of") or context.get("as_of"))
    cutoff = min(cutoff, source_cutoff)
    if not start <= end <= source_cutoff or start > cutoff:
        raise ValueError("provider_order_page_version_conflict")
    if payment.get("paid_at") is not None and not start <= _instant(payment["paid_at"]) <= end:
        raise ValueError("provider_capture_timestamp_conflict")

    from salla_payment_collection import provider_order_page_balance
    from operational_split_payment import split_payment_evidence

    balance = provider_order_page_balance(raw, currency, total)
    # External activity rows are loaded from the already owner-scoped,
    # bounded Salla transaction reader. The canonical split validator checks
    # transaction IDs, amounts, status, binding, native refs and collisions.
    row_index = context.get("split_fee_transaction_rows") or {}
    if not isinstance(row_index, dict):
        raise ValueError("provider_transaction_history_conflict")
    scoped_rows = row_index.get(str(order.get("order_number")), ())
    if not isinstance(scoped_rows, (list, tuple)) or len(scoped_rows) > 800:
        raise ValueError("provider_transaction_history_conflict")
    evidence = split_payment_evidence(raw, currency, total, balance,
        owner=owner, order_number=str(order.get("order_number") or ""),
        provider_order_id=str(order.get("order_id") or order.get("salla_order_id") or ""),
        rows=scoped_rows, source=order, created_at=created, as_of=source_cutoff)
    if evidence is None:
        return None
    split, issues = evidence
    if not split or issues:
        raise ValueError(issues[0]["code"] if issues else "provider_split_payment_review_required")
    tenders = split["split"]["tenders"]
    conflicts = context.get("split_fee_reference_conflicts")
    if not isinstance(conflicts, frozenset):
        # Direct helper callers may still supply the original list contract.
        conflicts = frozenset(conflicts or [])
    if any(leg["reference"] in conflicts for leg in tenders):
        raise ValueError("provider_payment_reference_conflict")
    # A single externally funded provider plus the internal wallet is a valid
    # split shape for card rails and BNPL alike. Multiple external providers
    # still require explicit allocation evidence beyond this bounded reader.
    external = {leg["provider"] for leg in tenders if leg["method"] != "customer_wallet"}
    expected = provider_for(order.get("payment_method"))
    if (len(external) != 1 or expected not in external
            or not external.issubset({"salla", "tabby", "tamara", "emkan"})):
        raise ValueError("provider_split_payment_review_required")
    # Contradictory dates on the same page are a negative evidence signal.
    # Matching dates are NOT independent proof and must not shorten the window.
    observed_paid = _instant(payment["paid_at"]) if payment.get("paid_at") is not None else None
    for row in methods:
        if _is_wallet(row) or row.get("paid_at") is None:
            continue
        tender_paid = _instant(row["paid_at"])
        if not start <= tender_paid <= end or (observed_paid is not None and tender_paid != observed_paid):
            raise ValueError("provider_capture_timestamp_conflict")
    # Never date the fee using a mutable order page. Only fully bound
    # *independent* native capture rows can narrow an individual tender's
    # historical tariff date; missing legs retain the original full interval.
    captures = split.get("split_captures") or []
    dated = {}
    external_refs = {leg["reference"] for leg in tenders if leg["method"] != "customer_wallet"}
    for capture in captures:
        ref = capture.get("id")
        if ref not in external_refs or ref in dated:
            raise ValueError("provider_transaction_binding_conflict")
        instant = _instant(capture.get("captured_at"))
        if not start <= instant <= end or instant > cutoff:
            raise ValueError("provider_capture_timestamp_conflict")
        dated[ref] = instant
    # Partial capture evidence is never used to establish a cheaper tariff.
    if set(dated) != external_refs:
        dated.clear()
    # An order-page observation made after the report day can only restate
    # that old day when EVERY externally funded tender proves an earlier
    # native capture. Otherwise the current paid status is not dated evidence.
    if end > cutoff and not dated:
        raise ValueError("provider_fee_transaction_date_required")
    return tenders, start, end, cutoff, dated


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
    tenders, start, end, cutoff, native_dates = basis
    net, vat = Decimal(0), Decimal(0)
    for leg in tenders:
        if leg["method"] == "customer_wallet":
            continue
        provider = leg["provider"]
        rail = fee_rail(leg["method"]) if provider == "salla" else leg["method"]
        if not provider or rail is None:
            raise ValueError("provider_payment_method_required_for_fee")
        captured = native_dates.get(leg["reference"])
        effective = captured or end
        earliest = captured or start
        projection = provider_projection({"provider": provider, "currency": "SAR",
            "gross": leg["amount"], "payment_method": rail, "plan_id": leg.get("plan_id"),
            "event_type": "capture"}, context["policies"], effective.astimezone(RIYADH).date(),
            earliest_date=earliest.astimezone(RIYADH).date(), as_of_date=cutoff.astimezone(RIYADH).date())
        net += Decimal(projection["estimated_fee_net"])
        vat += Decimal(projection["estimated_fee_tax"])
    return net, vat, SOURCE_CAPTURE if native_dates else SOURCE
