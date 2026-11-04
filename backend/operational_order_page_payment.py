"""Order-detail balances are cumulative observations, independent of bank settlements."""
from decimal import Decimal

from operational_payment_evidence import _amount, _instant
from operational_provider_history import late_payment_blocks


def corroborated_refund_basis(previous, payment, facts, order_id):
    """A recorded refund still uses its real capture and normal fee/settlement gates."""
    if not previous or payment["provider"] != previous["provider"] or payment["currency"] != previous["currency"]:
        return False
    currency = previous["currency"]
    records = {}
    for capture in payment.get("captures", []):
        key = "capture:" + str(capture["id"])
        saved = facts.get(key, {})
        if ((saved.get("order_id"), saved.get("provider")) != (order_id, previous["provider"])
                or _amount(capture["amount"], currency) != _amount(saved["amount"], currency)):
            return False
        records[key] = _amount(saved["amount"], currency)
    return bool(records) and sum(records.values(), Decimal(0)) == _amount(previous["paid"], currency)


def observed_balance(page, payment, order, state, *, now, start):
    """Return one current observation, or withhold it without changing any ledger object."""
    facts = state.get("engine", {}).get("facts", {})
    oid = str(order["id"])
    key = "provider:" + oid
    previous = facts.get("provider_order_balance:" + oid)
    claim = state.get("engine", {}).get("obligations", {}).get(key, {})
    party, currency = payment["provider"], order["currency"]
    if not page or (page.get("order_id"), page.get("order_number"), page.get("provider"), page.get("currency")) != (
            oid, order.get("order_number"), party, currency) or not state.get("owner_id") or page.get("owner_id") != state["owner_id"]:
        raise ValueError("provider_order_page_identity_conflict")
    gross, paid, due = (_amount(page[k], currency) for k in ("gross", "paid", "remaining"))
    if gross != _amount(payment["gross"], currency) or paid + due != gross or gross <= 0:
        raise ValueError("provider_payment_amount_conflict")
    if any(payment.get(k) for k in ("refunds", "cancellations")) or _amount(payment.get("cancelled", 0), currency):
        raise ValueError("provider_refund_execution_evidence_required")
    created = _instant(order["created_at"])
    version = _instant(page["source_updated_at"]) if page.get("source_updated_at") else None
    if not start < created <= now or (version and (version != _instant(order["updated_at"]) or not created <= version <= now)):
        raise ValueError("provider_order_page_version_conflict")
    if claim and (claim.get("party_id"), claim.get("currency"), claim.get("direction")) != (party, currency, "receivable"):
        raise ValueError("provider_capture_identity_conflict")
    known = _amount(claim.get("confirmed", 0), currency)
    captures = {}
    for fact_key, fact in facts.items():
        if fact_key.startswith("refund:") and fact.get("order_id") == oid:
            raise ValueError("provider_refund_execution_evidence_required")
        if fact_key.startswith("capture:") and fact.get("order_id") == oid:
            if fact.get("provider") != party:
                raise ValueError("provider_capture_identity_conflict")
            captures[fact_key] = _amount(fact["amount"], currency)
    frozen = sum(captures.values(), Decimal(0))
    if previous:
        if any(previous.get(k) != page[k] for k in ("owner_id", "order_id", "order_number", "provider", "currency", "gross")):
            raise ValueError("provider_order_page_identity_conflict")
        if known != _amount(previous["paid"], currency) or frozen > known:
            raise ValueError("provider_capture_amount_conflict")
        last_version = state.get("engine", {}).get("order_versions", {}).get(oid) or previous["observed_at"]
        if paid < known or (paid != known and (not version or version <= _instant(last_version))):
            raise ValueError("provider_order_page_version_conflict")
    elif known != frozen or paid < known:
        raise ValueError("provider_capture_amount_conflict")
    cutoff = state.get("engine", {}).get("as_of")
    if not previous and known and paid > known:
        last_version = state.get("engine", {}).get("order_versions", {}).get(oid)
        if not last_version or not version or version <= _instant(last_version):
            raise ValueError("provider_order_page_version_conflict")
    if paid > known and not known and not frozen:
        late = cutoff and created <= _instant(cutoff)
        if (late or state.get("movements")) and late_payment_blocks(state, order, payment, through=now):
            raise ValueError("provider_historical_capture_review_required" if late else "provider_settled_capture_review_required")
    elif paid > known:
        allocated = Decimal(0)
        for movement in state.get("movements", []):
            if (movement.get("party_type"), movement.get("party_id"), movement.get("currency")) != ("provider", party, currency):
                continue
            allocations = movement.get("allocations", [])
            allocated += sum((_amount(a["amount"], currency) for a in allocations if a.get("obligation_id") == key), Decimal(0))
            if (allocated > known or any(a.get("obligation_id") == "credit:" + key for a in allocations)
                    or sum((_amount(a["amount"], currency) for a in allocations), Decimal(0))
                    != _amount(movement["amount"], currency) + _amount(movement.get("actual_fee_amount", 0), currency)):
                raise ValueError("provider_settled_capture_review_required")
    # A later real transaction may corroborate this same evidence. It is never
    # added on top of the cumulative order amount.
    for capture in payment.get("captures", []):
        capture_key = "capture:" + str(capture["id"])
        value = _amount(capture["amount"], currency)
        if (not created <= _instant(capture["captured_at"]) <= now or
                (capture_key in captures and captures[capture_key] != value)):
            raise ValueError("provider_capture_identity_conflict")
        captures[capture_key] = value
    if payment.get("captures") and sum(captures.values(), Decimal(0)) != paid:
        raise ValueError("provider_capture_amount_conflict")
    if previous:
        old_ref = previous.get("transaction_reference")
        new_ref = page.get("transaction_reference")
        if old_ref and new_ref and old_ref != new_ref:
            raise ValueError("provider_capture_identity_conflict")
        if paid == known:
            if new_ref and not old_ref:
                return {**previous, "transaction_reference": new_ref}
            return previous
        if old_ref and not new_ref:
            page = {**page, "transaction_reference": old_ref}
    return {**page, "observed_at": now.isoformat()}
