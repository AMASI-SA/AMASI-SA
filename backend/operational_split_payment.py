"""Reconcile Salla's explicitly itemized, fully paid order with each provider's share."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from operational_fee_methods import fee_rail
from operational_payment_evidence import PaymentEvidenceError, _amount, _identity, _instant, provider_for, resolve_provider
from operational_provider_fees import provider_projection
from operational_provider_history import late_payment_blocks
from operational_provider_dates import DATE_ISSUES, UNPROVEN, recognition_day


REVIEW = "provider_split_payment_review_required"
WALLET = "customer_wallet"
RIYADH = ZoneInfo("Asia/Riyadh")


def _transaction_conflicts(rows, tenders, owner, number, oid, currency, created_at, as_of):
    transactions = {}
    for row in rows:
        if (row.get("user_id"), row.get("order_number"), row.get("provider_order_id"), row.get("source")) != (
                owner, number, oid, "salla_transactions"):
            continue
        group = row.get("native_transaction_evidence")
        if group is None:  # Older display rows do not prove a transaction.
            continue
        if not isinstance(group, list) or not 0 < len(group) <= 16:
            return True  # Includes the cross-order reference collision marker.
        for ev in group:
            if not isinstance(ev, dict) or ev.get("schema") != "salla_transaction_evidence_v1":
                return True
            leg = tenders.get(ev.get("reference"))
            if (not leg or ev.get("transaction_id") != row.get("transaction_id") or not row.get("transaction_id")
                    or ev.get("currency") != currency or ev.get("provider") != leg["provider"]
                    or ev.get("status") not in ("captured", "paid") or _amount(ev.get("amount"), currency) != Decimal(leg["amount"])):
                return True
            if ev["transaction_id"] in transactions and transactions[ev["transaction_id"]] != ev["reference"]:
                return True
            transactions[ev["transaction_id"]] = ev["reference"]
            if ev.get("unbound_order") is True:
                if "provider_order_id" in ev:
                    return True
            elif ev.get("provider_order_id") != oid or "unbound_order" in ev:
                return True
            if ev.get("paid_at") is not None and not _instant(created_at) <= _instant(ev["paid_at"]) <= _instant(as_of):
                return True
    return False


def _bound_capture_evidence(rows, tenders, owner, number, oid, currency):
    """Keep actual native paid times outside the immutable split page shape.

    An unbound activity row may corroborate a displayed amount, but cannot date
    a payment.  Neither transaction creation nor order/page update time is used.
    The preceding conflict check has validated amounts, states and time bounds.
    """
    dated = {}
    for row in rows:
        if (row.get("user_id"), row.get("order_number"), row.get("provider_order_id"), row.get("source")) != (
                owner, number, oid, "salla_transactions"):
            continue
        for ev in row.get("native_transaction_evidence") or []:
            if ev.get("provider_order_id") != oid or "unbound_order" in ev or not ev.get("paid_at"):
                continue
            ref = ev["reference"]
            observation = {"id": ref, "provider": tenders[ref]["provider"], "currency": currency,
                "amount": tenders[ref]["amount"], "captured_at": _instant(ev["paid_at"]).isoformat()}
            candidate = (ev["transaction_id"], observation)
            if ref in dated and dated[ref] != candidate:
                raise PaymentEvidenceError("provider_capture_timestamp_conflict")
            dated[ref] = candidate
    return [dated[key][1] for key in sorted(dated)]


def split_payment_evidence(raw, currency, total, amounts, *, owner, order_number, provider_order_id,
                           rows, source, created_at, as_of):
    native = raw.get("payment_methods")
    if not isinstance(native, list) or len(native) < 2:
        return None
    try:
        if len(native) > 100 or not all(isinstance(row, dict) for row in native):
            raise PaymentEvidenceError("provider_payment_shape_invalid")
        # Identical repeated references on a single tender belong to the ordinary reader.
        if all(row == native[0] for row in native):
            return None
        if not amounts or _amount(amounts["paid"], currency) != _amount(total, currency) or Decimal(amounts["remaining"]) != 0:
            raise PaymentEvidenceError(REVIEW)
        from salla_payment_collection import provider_order_page_current
        if not provider_order_page_current(source, raw, owner=owner, order_number=order_number, provider_order_id=provider_order_id):
            raise PaymentEvidenceError("provider_order_page_identity_conflict")
        p = raw.get("payment") or {}
        action = (raw.get("payment_actions") or {}).get("refund_action") or {}
        if (any(r.get(k) for r in (p, raw) for k in ("refunds", "cancellations", "refunded_transactions"))
                or any(_amount(r[k], currency) for r in (raw, p, action)
                       for k in ("pending_refund_amount", "cancelled") if r.get(k) is not None)):
            raise PaymentEvidenceError("provider_refund_execution_evidence_required")
        primary = resolve_provider(raw, require_known=True)
        tenders = {}
        for row in native:
            ref = _identity(row.get("transaction_reference"), "provider_payment_reference_missing")
            method = _identity(row.get("payment_method"), REVIEW).lower()
            provider = provider_for(method)
            if not provider and method != WALLET:
                raise PaymentEvidenceError(REVIEW)
            if row.get("currency") not in (None, currency):
                raise PaymentEvidenceError("provider_payment_currency_conflict")
            value = _amount(row.get("amount"), currency)
            if value <= 0:
                raise PaymentEvidenceError("provider_payment_amount_invalid")
            leg = {"reference": ref, "method": method, "provider": provider, "amount": format(value, ".2f")}
            if row.get("plan_id") is not None:
                leg["plan_id"] = _identity(row["plan_id"], "provider_payment_plan_invalid")
            if ref in tenders and tenders[ref] != leg:
                raise PaymentEvidenceError("provider_payment_reference_conflict")
            tenders[ref] = leg
        if not primary or primary not in {t["provider"] for t in tenders.values()}:
            raise PaymentEvidenceError("provider_payment_method_conflict")
        if sum(Decimal(t["amount"]) for t in tenders.values()) != _amount(total, currency):
            raise PaymentEvidenceError("provider_payment_amount_conflict")
        for ref in (p.get("reference"), p.get("transaction_reference")):
            if ref is not None and _identity(ref, REVIEW) not in tenders:
                raise PaymentEvidenceError("provider_payment_reference_conflict")
        if _transaction_conflicts(rows, tenders, owner, order_number, provider_order_id, currency, created_at, as_of):
            raise PaymentEvidenceError("provider_transaction_binding_conflict")
        if raw.get("updated_at") or raw.get("date_updated"):
            stamp = _instant(raw.get("updated_at") or raw["date_updated"])
            if not _instant(created_at) <= stamp <= _instant(as_of):
                raise PaymentEvidenceError("provider_order_page_version_conflict")
        dated = _bound_capture_evidence(rows, tenders, owner, order_number, provider_order_id, currency)
        return {"split": {"order_id": provider_order_id, "order_number": order_number, "owner_id": owner,
                          "currency": currency, "gross": format(_amount(total, currency), ".2f"),
                          "tenders": [tenders[key] for key in sorted(tenders)]},
                **({"split_captures": dated} if dated else {})}, []
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        code = str(exc) if isinstance(exc, PaymentEvidenceError) else REVIEW
        return None, [{"code": code}]


def provider_shares(split):
    shares = defaultdict(list)
    for tender in split["tenders"]:
        if tender["provider"]:
            shares[tender["provider"]].append(tender)
    return shares


def split_reference_owners(facts):
    """Include frozen source references even when their original order has disappeared."""
    owners = defaultdict(set)
    for key, fact in facts.items():
        if key.startswith("capture:") and fact.get("order_id"):
            owners[key.removeprefix("capture:")].add(fact["order_id"])
        elif key.startswith("provider_split:"):
            split = fact["evidence"]
            for tender in split["tenders"]:
                owners[tender["reference"]].add(split["order_id"])
        elif key.startswith("provider_order_balance:") and fact.get("transaction_reference"):
            owners[fact["transaction_reference"]].add(fact["order_id"])
    return owners


def _share_projection(party, legs, paid, currency, policies, first_day, last_day):
    # Multiple same-provider charges can each have a fixed fee; no blended estimate.
    if len(legs) != 1:
        return None
    payment = {"provider": party, "currency": currency, "gross": paid,
               "payment_method": fee_rail(legs[0]["method"]) if party == "salla" else None,
               "plan_id": legs[0].get("plan_id"), "fee_plan_issue": legs[0].get("fee_plan_issue")}
    try:
        result = provider_projection(payment, policies, last_day, earliest_date=first_day)
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return None
    return {**result, "confirmed_receivable": paid}


def apply_split_payment(state, order, now, put, issue, fee_policies, reference_owners):
    """Post distinct receivables only for verified tenders, never to a bank or wallet."""
    oid = str(order["id"])
    engine = state["engine"]
    facts, obligations = engine["facts"], engine["obligations"]
    parent = "provider:" + oid
    fact_key = "provider_split:" + oid
    saved = facts.get(fact_key)
    split = order["payment"].get("split")
    if split is None:
        if saved is None:
            return False
        issue("provider_order_page_identity_conflict", parent)
        return True
    if order["payment"].get("refund_allocation_unresolved"):
        issue("provider_refund_execution_evidence_required", parent)
        return True
    if (split.get("owner_id") != state.get("owner_id") or split.get("order_id") != oid
            or split.get("order_number") != order.get("order_number") or split.get("currency") != order["currency"]
            or (saved is not None and saved["evidence"] != split)):
        issue("provider_order_page_identity_conflict", parent)
        return True
    for tender in split["tenders"]:
        ref = tender["reference"]
        prior_capture = facts.get("capture:" + ref)
        if (reference_owners.get(ref, set()) - {oid}
                or (prior_capture is not None and prior_capture.get("order_id") != oid)):
            issue("provider_payment_reference_conflict", parent)
            return True
    if saved is None and (parent in obligations or any(
            f.get("order_id") == oid and key.startswith(("capture:", "refund:", "provider_order_balance:"))
            for key, f in facts.items())):
        issue("provider_capture_identity_conflict", parent)
        return True
    created = _instant(order["created_at"])
    if not _instant(state["started_at"]) < created <= now:
        return True
    if saved is None:
        observation = {"evidence": split, "booked_providers": [], "observed_at": now.isoformat()}
    else:
        observation = {**saved, "booked_providers": list(saved["booked_providers"])}
        if "capture_business_dates" in saved:
            observation["capture_business_dates"] = dict(saved["capture_business_dates"])
    observed = _instant(observation["observed_at"])
    for party, legs in provider_shares(split).items():
        key = parent + ":" + party
        paid = format(sum(Decimal(leg["amount"]) for leg in legs), ".2f")
        page = {k:split[k] for k in ("order_id", "order_number", "owner_id", "currency")}
        page.update(provider=party, source="canonical_salla_order_page", gross=paid, paid=paid, remaining="0.00")
        if (party not in observation["booked_providers"]
                and late_payment_blocks(state, order, {"provider":party, "currency":split["currency"],
                    "gross":paid, "order_page_balance":page}, obligation_key=key, through=now)):
            issue("provider_settled_capture_review_required", key)
            continue
        try:
            legs_by_ref = {leg["reference"]: leg for leg in legs}
            captures = []
            for capture in order["payment"].get("split_captures", []):
                if capture.get("provider") != party:
                    continue
                leg = legs_by_ref.get(capture.get("id"))
                if (not leg or capture.get("currency") != split["currency"]
                        or _amount(capture.get("amount"), split["currency"]) != Decimal(leg["amount"])):
                    raise ValueError(UNPROVEN)
                captures.append(capture)
            effective = recognition_day(obligations.get(key), captures, currency=split["currency"],
                confirmed=paid, captured=paid, created_at=created, as_of=now)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            code = str(exc) if str(exc) in DATE_ISSUES else UNPROVEN
            issue(code, key, provider=party, party_type="provider", party_id=party,
                  missing_fields=["capture_timestamp"])
            continue
        newly_booked = Decimal(paid) > Decimal(obligations.get(key, {}).get("confirmed", "0"))
        if newly_booked:
            observation.setdefault("capture_business_dates", {})[party] = effective.isoformat()
        capture_dated = observation.get("capture_business_dates", {}).get(party) == effective.isoformat()
        projection = _share_projection(party, legs, paid, split["currency"], fee_policies,
            effective if capture_dated else created.astimezone(RIYADH).date(),
            effective if capture_dated else observed.astimezone(RIYADH).date())
        metadata = {"order_id": oid, "evidence_ids": [fact_key]}
        if projection is not None:
            engine["provider_reports"][key] = projection
            metadata["projection"] = projection
        else:
            engine["provider_reports"].pop(key, None)
            metadata["fee_estimate_complete"] = False
            issue("provider_fee_estimate_unavailable", key)
        put(key, "provider", party, "provider", split["currency"], confirmed=Decimal(paid), direction="receivable",
            business_date=effective, **metadata)
        if party not in observation["booked_providers"]:
            observation["booked_providers"].append(party)
    facts[fact_key] = observation
    for tender in split["tenders"]:
        reference_owners.setdefault(tender["reference"], set()).add(oid)
    return True
