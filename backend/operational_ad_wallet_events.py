"""Pure, evidence-ordered operational wallet/debt calculation.

This is a candidate calculation seam, NOT a daily-source adapter or a writer.
Callers must supply independently evidenced spend/payment events. A daily total
must not be assigned an invented event time to bypass missing chronology.
No existing fixed-split accounts or previously confirmed days are reinterpreted.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation


ZERO = Decimal("0.00")
CENT = Decimal("0.01")
LIMIT = Decimal("999999999999.99")


def _money(value):
    if isinstance(value, (bool, float)) or value is None:
        raise ValueError("wallet_event_amount_invalid")
    try:
        result = Decimal(str(value))
        if (not result.is_finite() or not ZERO <= result <= LIMIT
                or result != result.quantize(CENT)):
            raise ValueError("wallet_event_amount_invalid")
        return result
    except (InvalidOperation, TypeError):
        raise ValueError("wallet_event_amount_invalid") from None


def _instant(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise ValueError("wallet_event_time_invalid") from None


def project_wallet_events(*, owner_id, account_id, currency, opening_at,
                          opening_wallet, opening_debt, events):
    """Spend wallet first; pay debt first. Return an immutable-input projection.

    Required event fields: owner_id, account_id, currency, id, evidence_id,
    kind (spend/payment), amount and occurred_at (aware ISO instant).
    Simultaneous distinct events require unique nonnegative sequence numbers
    from the evidence source; list order and IDs never decide financial order.
    Identical event retries are deduplicated; conflicting identities fail.
    """
    if not all(isinstance(v, str) and v.strip() for v in (owner_id, account_id)):
        raise ValueError("wallet_event_scope_invalid")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isupper() or not currency.isalpha():
        raise ValueError("wallet_event_currency_invalid")
    start = _instant(opening_at)
    wallet, debt = _money(opening_wallet), _money(opening_debt)
    unique, instants, evidence_ids = {}, {}, {}
    for raw in events:
        if not isinstance(raw, dict):
            raise ValueError("wallet_event_invalid")
        if (raw.get("owner_id"), raw.get("account_id"), raw.get("currency")) != (owner_id, account_id, currency):
            raise ValueError("wallet_event_scope_mismatch")
        if not all(isinstance(raw.get(k), str) and raw[k].strip() for k in ("id", "evidence_id")):
            raise ValueError("wallet_event_evidence_required")
        if raw.get("kind") not in {"spend", "payment"}:
            raise ValueError("wallet_event_kind_unsupported")
        at = _instant(raw.get("occurred_at"))
        if at < start:
            raise ValueError("wallet_event_before_opening")
        seq = raw.get("sequence")
        if seq is not None and (type(seq) is not int or seq < 0):
            raise ValueError("wallet_event_sequence_invalid")
        event = {"id": raw["id"], "evidence_id": raw["evidence_id"],
                 "kind": raw["kind"], "amount": _money(raw.get("amount")),
                 "at": at, "sequence": seq}
        previous = unique.get(event["id"])
        if previous is not None:
            if previous != event:
                raise ValueError("wallet_event_identity_conflict")
            continue
        if event["evidence_id"] in evidence_ids:
            raise ValueError("wallet_event_evidence_reused")
        evidence_ids[event["evidence_id"]] = event["id"]
        unique[event["id"]] = event
        instants.setdefault(at, []).append(event)
    for group in instants.values():
        if len(group) > 1:
            sequences = [event["sequence"] for event in group]
            if None in sequences or len(set(sequences)) != len(sequences):
                raise ValueError("wallet_event_chronology_required")
    spend = paid = ZERO
    allocations = []
    for event in sorted(unique.values(), key=lambda e: (e["at"], e["sequence"] or 0)):
        value = event["amount"]
        if event["kind"] == "spend":
            from_wallet = min(wallet, value)
            to_debt = value - from_wallet
            wallet -= from_wallet
            debt += to_debt
            spend += value
            parts = {"wallet_consumed": from_wallet, "debt_added": to_debt}
        else:
            settled = min(debt, value)
            funded = value - settled
            debt -= settled
            wallet += funded
            paid += value
            parts = {"debt_settled": settled, "wallet_funded": funded}
        if wallet > LIMIT or debt > LIMIT or spend > LIMIT or paid > LIMIT:
            raise ValueError("wallet_event_total_exceeds_limit")
        allocations.append({"id": event["id"], "evidence_id": event["evidence_id"],
                            "kind": event["kind"],
                            **{key: format(v, ".2f") for key, v in parts.items()},
                            "wallet_balance": format(wallet, ".2f"),
                            "payable": format(debt, ".2f")})
    return {"owner_id": owner_id, "account_id": account_id, "currency": currency,
            "wallet_balance": format(wallet, ".2f"), "payable": format(debt, ".2f"),
            "spend": format(spend, ".2f"), "payments": format(paid, ".2f"),
            "allocations": allocations}
