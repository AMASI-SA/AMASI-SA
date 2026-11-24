"""Initial native Salla capture -> canonical operational provider claim.

The one-step posting gate is deliberately narrow. It admits ONLY a first,
FULL, verified SAR provider capture newer than the last successful operational
source snapshot, after the operational start boundary, with an unambiguous
saved fee policy and no existing provider/order effects. Additions, refunds,
COD, cross-currency, pre-existing/historical captures and source conflicts
remain under their existing reviewed workflows.

This file does NOT create a second ledger, bank movement, or GL/Qoyod entry.
The caller must use operational_balance_store.mutate() (owner-scoped CAS)
and revalidate the signed order/merchant origin before write admission.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

from operational_provider_fees import provider_projection

RIYADH = timezone(timedelta(hours=3))
PROVIDERS = frozenset({"salla", "tabby", "tamara", "emkan", "paypal"})


class InitialCaptureHold(ValueError):
    """A deterministic admission refusal: never convert it to an empty receipt."""


def _instant(raw):
    if not isinstance(raw, str):
        raise InitialCaptureHold("capture_clock_invalid")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise InitialCaptureHold("capture_clock_invalid") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise InitialCaptureHold("capture_clock_invalid")
    return value.astimezone(timezone.utc)


def _cash(raw):
    if isinstance(raw, bool):
        raise InitialCaptureHold("capture_amount_invalid")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise InitialCaptureHold("capture_amount_invalid") from None
    if not value.is_finite() or value <= 0 or value > Decimal("999999999999.99"):
        raise InitialCaptureHold("capture_amount_invalid")
    if value != value.quantize(Decimal("0.01")):
        raise InitialCaptureHold("capture_amount_invalid")
    return value


def apply_first_provider_capture(state, *, order_id, order_number, normalized, as_of):
    """Apply exactly one new canonical capture & provider claim to a CAS COPY.

    This function intentionally does not handle fees separately: provider
    projection and obligation are the same shape as the refresh engine uses.
    Calling it without authenticated Salla evidence and CAS is unsafe.
    """
    if state.get("status") != "active" or not state.get("started_at"):
        raise InitialCaptureHold("operational_start_unavailable")
    if not isinstance(order_id, str) or not order_id.strip() or not isinstance(order_number, str) or not order_number.strip():
        raise InitialCaptureHold("order_identity_incomplete")
    if not isinstance(normalized, dict):
        raise InitialCaptureHold("capture_source_missing")
    provider, currency = normalized.get("provider"), normalized.get("currency")
    if provider not in PROVIDERS or currency != "SAR":
        raise InitialCaptureHold("provider_currency_unverified")
    captures = normalized.get("captures")
    if not isinstance(captures, list) or len(captures) != 1:
        raise InitialCaptureHold("initial_capture_requires_one_transaction")
    if normalized.get("refunds") or normalized.get("cancelled") not in (None, "0", "0.00"):
        raise InitialCaptureHold("initial_capture_refund_requires_review")
    payment = captures[0]
    if not isinstance(payment, dict) or not isinstance(payment.get("id"), str) or not payment["id"].strip():
        raise InitialCaptureHold("capture_reference_missing")
    native_ref = payment["id"]
    gross, captured = _cash(normalized.get("gross")), _cash(payment.get("amount"))
    if gross != captured:
        raise InitialCaptureHold("initial_capture_partial_requires_review")
    captured_at, at = _instant(payment.get("captured_at")), _instant(as_of)
    started_at = _instant(state["started_at"])
    if captured_at < started_at or captured_at > at:
        raise InitialCaptureHold("initial_capture_outside_operational_period")
    engine = state.get("engine")
    if not isinstance(engine, dict) or not engine.get("as_of"):
        raise InitialCaptureHold("prior_source_snapshot_required")
    last_source = _instant(engine["as_of"])
    if captured_at <= last_source:
        raise InitialCaptureHold("historical_capture_requires_reconciliation")

    facts = engine.get("facts")
    obligations = engine.get("obligations")
    reports = engine.get("provider_reports")
    fee_inputs = engine.get("provider_fee_inputs")
    if not all(isinstance(unit, dict) for unit in (facts, obligations, reports, fee_inputs)):
        raise InitialCaptureHold("canonical_ledger_source_incomplete")
    obligation_key, capture_key = "provider:" + order_id, "capture:" + native_ref
    old = facts.get(capture_key)
    if old is not None:
        if (isinstance(old, dict) and str(old.get("order_id")) == order_id
                and old.get("provider") == provider and _cash(old.get("amount")) == captured
                and isinstance(obligations.get(obligation_key), dict)
                and obligations[obligation_key].get("party_id") == provider
                and _cash(obligations[obligation_key].get("confirmed")) >= captured):
            return {"status": "already_posted", "provider": provider, "reference": native_ref}
        raise InitialCaptureHold("capture_identity_conflict")
    if (obligation_key in obligations or obligation_key in reports or
            any(isinstance(fact, dict) and str(fact.get("order_id")) == order_id
                and str(key).startswith(("capture:", "provider_order_balance:", "refund:"))
                for key, fact in facts.items())):
        raise InitialCaptureHold("preexisting_order_financial_effect")
    if engine.get("order_versions", {}).get(order_id):
        raise InitialCaptureHold("already_scanned_order_requires_refresh")
    if any(isinstance(m, dict) and any(isinstance(a, dict)
            and a.get("obligation_id") in (obligation_key, "credit:" + obligation_key)
            for a in m.get("allocations", [])) for m in state.get("movements", [])):
        raise InitialCaptureHold("settled_provider_requires_review")

    policies = fee_inputs.get("policies")
    if not isinstance(policies, list) or not policies:
        raise InitialCaptureHold("provider_fee_policy_missing")
    policy_day = captured_at.astimezone(RIYADH).date()
    terms = {key: normalized[key] for key in
             ("provider", "currency", "gross", "plan_id", "payment_method")
             if key in normalized}
    terms["refunds"] = []
    try:
        projection = provider_projection(terms, policies, policy_day,
                                         earliest_date=policy_day,
                                         as_of_date=at.astimezone(RIYADH).date())
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise InitialCaptureHold("provider_fee_policy_ambiguous") from None
    expected = Decimal(projection["expected_receivable"])
    if not expected.is_finite():
        raise InitialCaptureHold("provider_fee_projection_invalid")

    # FINANCIAL WRITE: same canonical capture key and provider obligation as
    # operational_balance_engine.reconcile. A later refresh recognizes the
    # identical fact; no separate addition is ever made to the provider total.
    facts[capture_key] = {"order_id": order_id, "provider": provider,
                          "amount": format(captured, ".2f")}
    projection["confirmed_receivable"] = format(captured, ".2f")
    reports[obligation_key] = projection
    obligations[obligation_key] = {
        "id": obligation_key, "kind": "provider", "party_id": provider,
        "party_type": "provider", "currency": currency,
        "expected": format(max(expected - captured, Decimal(0))
                           if expected >= 0 else abs(expected), ".2f"),
        "confirmed": format(captured, ".2f"),
        "direction": "receivable" if expected >= 0 or captured else "payable",
        "business_date": str(policy_day), "projection": projection,
    }
    return {"status": "posted", "provider": provider, "reference": native_ref,
            "order_number": order_number, "amount": format(captured, ".2f")}
