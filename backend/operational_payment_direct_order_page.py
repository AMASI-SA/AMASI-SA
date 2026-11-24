"""First signed Salla paid-order page -> existing operational provider claim.

Unlike a capture, an order page provides a cumulative paid observation, not an
original gateway capture timestamp. Reuse the canonical order-page fact key
and the existing observed_balance() safety gates. Never invent captured_at.

This operation is strictly for NEW first orders after the last successful
operational snapshot. Historical orders, split payments, refunds, bank
transfers, COD, stale source snapshots and uncertain fees are withheld.
It must run on a CAS COPY after authenticated merchant/source admission.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

from operational_payment_salla_order_page import native_paid_order_page
from operational_provider_fees import provider_projection

RIYADH = timezone(timedelta(hours=3))


class FirstPageHold(ValueError):
    pass


def _time(value):
    from operational_payment_evidence import _instant
    try:
        dt = _instant(value)
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise FirstPageHold("page_time_invalid") from None


def apply_first_paid_order_page(state, *, raw, source, owner, order_number, as_of):
    if state.get("owner_id") != owner or state.get("status") != "active" or not state.get("started_at"):
        raise FirstPageHold("operational_owner_or_start_incomplete")
    page = native_paid_order_page(raw, owner=owner, order_number=order_number, source=source)
    if not page:
        raise FirstPageHold("salla_paid_order_page_unverified")
    now, start, created = (_time(value) for value in
                           (as_of, state["started_at"], raw.get("date") or raw.get("created_at")))
    version = _time(raw.get("updated_at") or raw.get("date_updated"))
    if not start < created <= version <= now:
        raise FirstPageHold("paid_page_outside_operational_period")
    engine = state.get("engine")
    if not isinstance(engine, dict) or not engine.get("as_of"):
        raise FirstPageHold("source_snapshot_incomplete")
    last = _time(engine["as_of"])
    if not all(isinstance(engine.get(key), dict) for key in
               ("facts", "obligations", "provider_reports", "provider_fee_inputs")):
        raise FirstPageHold("canonical_payment_engine_incomplete")

    oid, provider = page["order_id"], page["provider"]
    facts, obligations = engine["facts"], engine["obligations"]
    reports = engine["provider_reports"]
    key, page_key = "provider:" + oid, "provider_order_balance:" + oid
    previous = facts.get(page_key)
    if previous is not None:
        # Original page evidence is cumulative. Never add it to a capture.
        same = (isinstance(previous, dict)
                and all(previous.get(k) == page.get(k) for k in (
                    "owner_id","order_id","order_number","provider","currency",
                    "gross","paid","remaining","transaction_reference"))
                and isinstance(obligations.get(key), dict)
                and obligations[key].get("party_id") == provider
                and obligations[key].get("confirmed") == page["paid"])
        if same:
            return {"status":"already_posted","provider":provider,
                    "reference":page["transaction_reference"]}
        raise FirstPageHold("paid_page_reference_conflict")
    # Existing exact receipts may replay after a later successful refresh,
    # whereas a NEW historical first receipt must never bypass the cutoff.
    if created <= last:
        raise FirstPageHold("historical_page_requires_reconciliation")
    if key in obligations or key in reports or engine.get("order_versions", {}).get(oid):
        raise FirstPageHold("preexisting_order_provider_effect")
    if any(str(fact_key).startswith("capture:") and isinstance(fact,dict)
            and str(fact.get("order_id")) == oid for fact_key,fact in facts.items()):
        raise FirstPageHold("native_capture_already_recognized")
    if any(isinstance(m,dict) and any(isinstance(a,dict)
            and a.get("obligation_id") in {key, "credit:" + key}
            for a in m.get("allocations",[])) for m in state.get("movements",[])):
        raise FirstPageHold("provider_settlement_requires_review")

    fees = engine["provider_fee_inputs"].get("policies")
    if not isinstance(fees, list) or not fees:
        raise FirstPageHold("provider_fee_policy_missing")
    payment = {"provider": provider, "currency": "SAR", "gross": page["gross"],
               "refunds": [], "captures": [], "cancelled": "0"}
    if provider == "salla":
        # The actual Salla rail matters for the commission contract.
        from operational_fee_methods import fee_rail
        rail = fee_rail(raw.get("payment_method"))
        if not rail:
            raise FirstPageHold("salla_payment_rail_unverified")
        payment["payment_method"] = rail

    # Reuse *existing* source reconciliation checks, including paid/balance
    # proof, previous payout conflicts, cutoff and cumulative identity.
    from operational_order_page_payment import observed_balance
    order = {"id":oid, "order_number":str(order_number), "currency":"SAR",
             "created_at":raw.get("date") or raw.get("created_at"),
             "updated_at":raw.get("updated_at") or raw.get("date_updated")}
    try:
        observed = observed_balance(page, payment, order, state, now=now, start=start)
        policy_day = version.astimezone(RIYADH).date()
        projection = provider_projection(
            payment, fees, policy_day,
            earliest_date=created.astimezone(RIYADH).date(),
            as_of_date=now.astimezone(RIYADH).date())
    except (ValueError, KeyError, TypeError, InvalidOperation):
        raise FirstPageHold("paid_page_provider_projection_unverified") from None
    gross = Decimal(page["paid"])
    estimated = Decimal(projection["expected_receivable"])
    projection["confirmed_receivable"] = page["paid"]

    # One canonical cumulative page fact and one provider claim: refresh
    # recognizes the same keys and can only replace them from stronger source
    # evidence, not add an independent second credit.
    facts[page_key] = observed
    reports[key] = projection
    obligations[key] = {
        "id": key, "kind": "provider", "party_id":provider,
        "party_type":"provider", "currency":"SAR",
        "confirmed":page["paid"],
        "expected":format(max(estimated-gross,Decimal(0)) if estimated >= 0
                          else abs(estimated),".2f"),
        "direction":"receivable" if estimated >= 0 or gross else "payable",
        "business_date":str(policy_day), "projection":projection,
    }
    return {"status":"posted","order_number":str(order_number),
            "provider":provider, "reference":page["transaction_reference"],
            "amount":page["paid"], "evidence":"cumulative_salla_order_page"}
