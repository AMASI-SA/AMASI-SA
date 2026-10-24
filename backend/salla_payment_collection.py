"""Honor an explicit Salla collection-clear without inferring settlement."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation


def settled_payment_snapshot(payload):
    actions = payload.get("payment_actions")
    remaining = actions.get("remaining_action") if isinstance(actions, dict) else None
    if not isinstance(remaining, dict) or remaining.get("has_remaining_amount") is not False:
        return payload
    paid = remaining.get("paid_amount")
    if isinstance(paid, dict):
        paid = paid.get("amount")
    try:
        amount = Decimal(str(paid))
    except (InvalidOperation, ValueError, TypeError):
        return payload
    if not amount.is_finite() or amount <= 0:
        return payload
    result = deepcopy(payload)
    result["payment_actions"]["remaining_action"].update(
        remaining_amount=0, has_remaining_amount=False, checkout_url="")
    result.update(paid_amount=float(amount), remaining_amount=0,
                  has_remaining_amount=False, payment_collection_status="paid",
                  payment_checkout_url="", checkout_url="")
    return result
