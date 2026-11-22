"""Identity-based projection of accountant-recorded customer receipts.

These records attest money already received. They are not Salla transactions
and never manufacture a provider fact. A later canonical receipt replaces the
manual projection only when its source, reference, order and amount agree.
"""
from decimal import Decimal, InvalidOperation


ZERO = Decimal("0.00")


def _amount(value):
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError("customer_collection_evidence_conflict") from None
    if not result.is_finite() or result < 0 or result != result.quantize(Decimal(".01")):
        raise ValueError("customer_collection_evidence_conflict")
    return result


def bank_movement_effect(movement, bank_id, currency="SAR"):
    """The signed bank effect used by the canonical balance report.

    The party leg of a transfer is opposite to its cash-source leg. A plain
    bank counterparty without a bank leg is not an incoming cash receipt.
    """
    cash_leg = (movement.get("bank_kind"), movement.get("bank_id"),
                movement.get("bank_currency") or movement.get("currency")) == ("bank", bank_id, currency)
    party_leg = (movement.get("party_type"), movement.get("party_id"), movement.get("currency")) == ("bank", bank_id, currency)
    if not cash_leg and not party_leg:
        return ZERO
    if movement.get("direction") not in {"incoming", "outgoing"}:
        raise ValueError("customer_collection_evidence_conflict")
    sign = 1 if movement["direction"] == "incoming" else -1
    effect = _amount(movement.get("bank_amount") or movement.get("amount")) * sign if cash_leg else ZERO
    if party_leg and movement.get("kind") == "transfer":
        effect -= _amount(movement.get("amount")) * sign
    elif party_leg and movement.get("kind") == "correction" and not movement.get("bank_id"):
        effect += _amount(movement.get("amount")) * sign
    return effect


def collection_reference_match(state, case):
    """Return an exact canonical effect identity, or None while unlinked.

Matching on amount/date alone is deliberately unsupported. An update to the
same original transaction additionally needs its reference and a cumulative
principal covering this immutable receipt's non-overlapping paid boundary.
"""
    if case.get("status") != "confirmed":
        return None
    order_id, reference = str(case["order_id"]), case["reference"]
    kind, identity = case["destination_type"], case["destination_id"]
    amount = _amount(case["amount"])
    if kind == "provider":
        facts = state.get("engine", {}).get("facts", {})
        key = "capture:" + reference
        fact = facts.get(key)
        if fact is not None:
            if (str(fact.get("order_id")), fact.get("provider"), _amount(fact.get("amount"))) != (
                    order_id, identity, amount):
                raise ValueError("customer_collection_evidence_conflict")
            return key
        page_key = "provider_order_balance:" + order_id
        page = facts.get(page_key) or {}
        original = case.get("original_reference")
        if case.get("fee_mode") == "same_transaction" and original and page.get("transaction_reference") == original:
            if (page.get("provider"), page.get("currency")) != (identity, case["currency"]):
                raise ValueError("customer_collection_evidence_conflict")
            boundary = _amount(case.get("destination_paid_basis", case["paid_basis"])) + amount
            if _amount(page.get("paid")) >= boundary:
                return page_key
    elif kind == "bank":
        matches = [movement for movement in state.get("movements", [])
                   if reference in {movement.get("reference"), movement.get("bank_transaction_reference")}
                   and movement.get("id") != case.get("id")
                   and bank_movement_effect(movement, identity, case["currency"]) > ZERO]
        if matches:
            if len(matches) != 1:
                raise ValueError("customer_collection_evidence_conflict")
            movement = matches[0]
            if (str(movement.get("order_id")), bank_movement_effect(movement, identity, case["currency"])) != (order_id, amount):
                raise ValueError("customer_collection_evidence_conflict")
            return "bank-movement:" + str(movement["id"])
    return None


def provider_recognition(state, order_id, provider, currency="SAR"):
    """Prove canonical principal/refunds have reached one provider claim.

    Arrival of a fact is not enough: a guarded refresh may retain the previous
    claim. Suppressing a manual bridge in that state would erase real cash.
    """
    order_id = str(order_id)
    engine = state.get("engine", {})
    facts = engine.get("facts", {})
    capture_total = sum((_amount(fact["amount"]) for key, fact in facts.items()
        if key.startswith("capture:") and isinstance(fact, dict)
        and (str(fact.get("order_id")), fact.get("provider")) == (order_id, provider)), ZERO)
    page = facts.get("provider_order_balance:" + order_id) or {}
    page_paid = (_amount(page["paid"]) if (page.get("provider"), page.get("currency")) == (provider, currency) else ZERO)
    split_fact = facts.get("provider_split:" + order_id) or {}
    split = split_fact.get("evidence") or {}
    split_paid = ZERO
    if provider in split_fact.get("booked_providers", []) and split.get("currency") == currency:
        split_paid = sum((_amount(leg["amount"]) for leg in split.get("tenders", [])
                          if leg.get("provider") == provider), ZERO)
    gross = max(capture_total, page_paid, split_paid)
    refunded = sum((_amount(fact["amount"]) for key, fact in facts.items()
        if key.startswith("refund:" + provider + ":") and isinstance(fact, dict)
        and str(fact.get("order_id")) == order_id), ZERO)
    keys = {"provider:" + order_id, "provider:" + order_id + ":" + provider}
    claims = [claim for key, claim in engine.get("obligations", {}).items() if key in keys
              and (claim.get("party_type"), claim.get("party_id"), claim.get("currency")) == ("provider", provider, currency)]
    if len(claims) != 1 or gross <= ZERO or refunded > gross:
        return None
    claim = claims[0]
    if (claim.get("incomplete") or claim.get("direction") != "receivable"
            or _amount(claim.get("confirmed")) != gross - refunded):
        return None
    return {"gross": gross, "refunded": refunded, "confirmed": gross - refunded}


def destination_paid_basis(state, order_id, provider, currency="SAR"):
    """Recognized destination principal plus non-overlapping manual receipts."""
    recognized = provider_recognition(state, order_id, provider, currency)
    original = recognized["gross"] if recognized else ZERO
    return original + sum((collection_projection(state, case)["principal_delta"] for case in state.get("customer_collections", [])
        if case.get("status") == "confirmed" and str(case.get("order_id")) == str(order_id)
        and (case.get("destination_type"), case.get("destination_id"), case.get("currency")) == ("provider", provider, currency)
        and collection_match(state, case) is None), ZERO)


def collection_match(state, case):
    """Return a link only when the exact receipt already affects the account."""
    key = collection_reference_match(state, case)
    if key is None or case.get("destination_type") != "provider":
        return key
    recognized = provider_recognition(state, case["order_id"], case["destination_id"], case["currency"])
    if recognized is None:
        return None
    boundary = _amount(case.get("destination_paid_basis", case["paid_basis"])) + _amount(case["amount"])
    return key if recognized["gross"] >= boundary else None


def provider_refund_recognized(state, case):
    """An exact provider refund is applied only after its claim is reduced."""
    recognized = provider_recognition(state, case["order_id"], case["refund_source_id"], case["currency"])
    return bool(recognized and recognized["refunded"] >= _amount(case["amount"]))


def _identified_captures(state, order_id, provider):
    return {key.removeprefix("capture:"): _amount(fact["amount"])
            for key, fact in state.get("engine", {}).get("facts", {}).items()
            if key.startswith("capture:") and isinstance(fact, dict)
            and (str(fact.get("order_id")), fact.get("provider")) == (str(order_id), provider)}


def collection_projection(state, case):
    """Minimum proved additional principal without amount-only matching.

    A cumulative source update may contain an accountant's receipt without its
    reference. Keep the larger proved cumulative amount and flag the overlap;
    never sum the two observations or falsely mark their identity as matched.
    """
    key = collection_match(state, case)
    amount = _amount(case["amount"])
    result = {"matched_evidence_id": key, "principal_delta": ZERO if key else amount,
              "source_overlap_amount": ZERO, "review_code": None}
    if key or case.get("destination_type") != "provider":
        return result
    recognized = provider_recognition(state, case["order_id"], case["destination_id"], case["currency"])
    if not recognized:
        return result
    captures = _identified_captures(state, case["order_id"], case["destination_id"])
    if sum(captures.values(), ZERO) == recognized["gross"] and case["reference"] not in captures:
        return result  # Fully identified different receipts establish disjoint cash.
    baseline = _amount(case.get("destination_paid_basis", case["paid_basis"]))
    overlap = min(max(recognized["gross"] - baseline, ZERO), amount)
    if overlap:
        result.update(principal_delta=amount - overlap, source_overlap_amount=overlap,
                      review_code="customer_collection_source_overlap")
    return result


def unmatched_collected_amount(state, order_id):
    return sum((_amount(case["amount"]) for case in state.get("customer_collections", [])
                if str(case.get("order_id")) == str(order_id) and case.get("status") == "confirmed"
                and collection_match(state, case) is None), ZERO)


def source_uncovered_collected_amount(state, order):
    """Customer coverage is distinct from account-ledger matching.

    A bank statement can already contain a manually recorded receipt while
    Salla still shows the original platform payment. Keep that customer money
    in coverage. Remove the addition only when the order's own paid proof
    explicitly includes its identity (or the same transaction's paid boundary).
    """
    payment = order.get("_payment_snapshot") or {}
    captures = {str(row.get("id")): row for row in payment.get("captures", [])}
    page = payment.get("order_page_balance") or {}
    total = ZERO
    for case in state.get("customer_collections", []):
        if str(case.get("order_id")) != str(order["id"]) or case.get("status") != "confirmed":
            continue
        # Check a conflicting canonical identity even if it has not propagated
        # into the order view; silently choosing one source could double money.
        collection_reference_match(state, case)
        included = False
        if case.get("destination_type") == "provider" and case.get("destination_id") == order.get("provider"):
            capture = captures.get(case["reference"])
            if capture:
                if _amount(capture.get("amount")) != _amount(case["amount"]):
                    raise ValueError("customer_collection_evidence_conflict")
                included = True
            if (not included and case.get("fee_mode") == "same_transaction" and case.get("original_reference")
                    and page.get("transaction_reference") == case["original_reference"]):
                included = _amount(order["paid_amount"]) >= _amount(case.get("source_paid_basis", case["paid_basis"])) + _amount(case["amount"])
        if not included:
            amount = _amount(case["amount"])
            if case.get("destination_type") == "provider" and case.get("destination_id") == order.get("provider") and page:
                captured = {str(row.get("id")): _amount(row["amount"]) for row in payment.get("captures", [])}
                fully_identified = (sum(captured.values(), ZERO) == _amount(order["paid_amount"])
                                    and case["reference"] not in captured)
                if not fully_identified:
                    baseline = _amount(case.get("destination_paid_basis", case["paid_basis"]))
                    amount -= min(max(_amount(order["paid_amount"]) - baseline, ZERO), amount)
            total += amount
    return total


def provider_collection_effects(state, provider, currency="SAR"):
    """Records plus the exact source match, for fee/principal projection."""
    return [{**case, **collection_projection(state, case)}
            for case in state.get("customer_collections", [])
            if case.get("status") == "confirmed" and case.get("destination_type") == "provider"
            and case.get("destination_id") == provider and case.get("currency") == currency]
