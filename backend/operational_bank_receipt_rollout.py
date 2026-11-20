"""Bound newly eligible bank tenders and identify posted receipts needing review."""
from operational_payment_evidence import _instant


MARKER = "bank_receipt_in_progress_policy"
HISTORICAL_REVIEW = "operational_order_bank_historical_receipt_review"
POSTED_REVIEW = "operational_order_bank_posted_milestone_review"


def initialize_admission(state, as_of):
    """Call inside the committed start or refresh. Never replace an existing boundary."""
    if state.get("status") != "active" or not state.get("owner_id"):
        return
    engine = state.setdefault("engine", {})
    if MARKER in engine:
        return
    start, at = _instant(state["started_at"]), _instant(as_of)
    if at < start:
        raise ValueError("operational_order_bank_activation_before_start")
    engine[MARKER] = {"version": 1, "owner_id": state["owner_id"],
                      "started_at": start.isoformat(), "activated_at": at.isoformat()}


def needs_historical_review(state, *, order_created_at, reached_at=None):
    """Newly supported tenders need a new order or an independently dated transition.

    An edit, attachment, synchronization, or delayed read of an old order does not
    make its original payment a new receipt. Call only after validating the Salla
    order identity and any supplied transition proof.
    """
    marker = ((state or {}).get("engine") or {}).get(MARKER)
    if (not isinstance(marker, dict) or type(marker.get("version")) is not int
            or marker["version"] != 1 or not state.get("owner_id")
            or marker.get("owner_id") != state["owner_id"]):
        return True
    try:
        start, at = _instant(state["started_at"]), _instant(marker["activated_at"])
        created = _instant(order_created_at)
        if _instant(marker["started_at"]) != start or created < start or at < start:
            return True
        if reached_at is None:
            return created <= at
        reached = _instant(reached_at)
        return reached < created or reached <= at
    except (KeyError, ValueError, TypeError):
        return True


def recorded_bank_credits(state):
    """Index only automatic incoming bank credits in this owner's operational state."""
    from operational_financial_activity_runtime import checked_rows
    records = {}
    for movement in checked_rows((state or {}).get("movements", ())):
        if (movement.get("automatic_order_bank") is True and movement.get("direction") == "incoming"
                and movement.get("party_type") == "bank" and movement.get("order_number")):
            records.setdefault(str(movement["order_number"]), movement)
    return records


def posted_review_required(movement, *, reached, reached_at=None):
    """A missing milestone or a later proven first transition requires review only."""
    if movement is None:
        return False
    if not reached:
        return True
    if reached_at is None:
        return False
    try:
        return _instant(movement.get("occurred_at")) < _instant(reached_at)
    except (ValueError, TypeError):
        return True
