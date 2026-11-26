"""One operational courier base fee from a newly created Salla delivered order.

This writer never claims an original delivery date from an observed status.
It records the first observed status date as such, independently of customer
cash, COD, provider charges and the existing native shipment/fee ledger.
"""
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from operational_delivery_evidence import STATUS_BASIS, STATUS_SCHEMA, _instant


PROOF = "observed_courier_base:"
SCHEMA = "operational_courier_observed_fee_v1"
RIYADH = ZoneInfo("Asia/Riyadh")


def _positive_sar(value):
    if isinstance(value, bool):
        raise ValueError("operational_courier_observed_fee_amount_invalid")
    try:
        number = Decimal(str(value))
        if (not number.is_finite() or number <= 0
                or number != number.quantize(Decimal("0.01"))):
            raise ValueError("operational_courier_observed_fee_amount_invalid")
        return number
    except (InvalidOperation, ArithmeticError, TypeError):
        raise ValueError("operational_courier_observed_fee_amount_invalid") from None


def apply_observed_courier_base(state, order, start, now, put, fact, issue, allocations):
    """Admit a fee only for a post-opening, status-proven courier order.

    The surrounding source reader already proved one canonical Salla order,
    its courier identity and one approved tariff covering the entire possible
    delivery interval. This independent boundary repeats critical identifiers
    and never grants a financial date, COD or settlement from the status.
    """
    event = order.get("delivery_event") or {}
    if not isinstance(event, dict) or event.get("schema") != STATUS_SCHEMA:
        return False
    if event.get("recognition_basis") != STATUS_BASIS:
        return False
    oid, number = str(order.get("id") or ""), str(order.get("order_number") or "")
    carrier = order.get("carrier") or {}
    if not isinstance(carrier, dict):
        return False
    if (not oid or not number or event.get("party_type") != "courier"
            or carrier.get("kind", carrier.get("party_type")) != "courier"
            or not carrier.get("id") or carrier.get("id") != event.get("party_id")
            or order.get("prepaid_shipping") is True
            or carrier.get("payment_mode") == "prepaid"
            or carrier.get("fee_recognition_held") is True
            or order.get("shipping_evidence_held") is True):
        return False

    facts = state["engine"]["facts"]
    obligations = state["engine"]["obligations"]
    root, key = "shipping:" + oid, "shipping:" + oid + ":base_shipping"
    proof_key = PROOF + oid

    # A prior charge from any existing route or a settlement never becomes
    # a second new fee. Other COD commission components do not count as base.
    if (facts.get("completed_shipping:" + oid) is not None
            or facts.get("prepaid_shipping:" + oid) is not None
            or facts.get("historical_base_fee:" + oid) is not None
            or facts.get("shipping_fee:" + oid) is not None
            or (facts.get(root) or {}).get("cost") is not None):
        return True
    existing = obligations.get(key)
    if existing and Decimal(str(existing.get("confirmed", "0"))) > 0:
        # Keep the exact old amount/date. Source changes need their own
        # review; a fresh calculator must never rewrite a paid fee.
        if existing.get("party_id") != carrier["id"] or existing.get("currency") != "SAR":
            issue("operational_courier_observed_fee_saved_conflict", oid)
        return True
    if (root in allocations or key in allocations
            or (existing and Decimal(str(existing.get("confirmed", "0"))) != 0)
            or facts.get(key) is not None):
        issue("operational_courier_observed_fee_saved_conflict", oid)
        return False

    try:
        created = _instant(order["created_at"])
        observed = _instant(event["recorded_at"])
        if (not start < created <= observed <= now
                or event.get("id") != "salla-delivery:" + oid
                or event.get("order_id") != oid
                or event.get("order_number") != number
                or _instant(event.get("order_created_at")) != created
                or event.get("delivered_at") is not None
                or order.get("delivered_at") is not None
                or order.get("status") not in {"delivered", "تم التوصيل"}
                or not state.get("owner_id") or not carrier.get("contract_id")
                or carrier.get("currency") != "SAR"):
            raise ValueError("operational_courier_observed_fee_evidence_invalid")
        parts = [p for p in carrier.get("fee_components", ())
                 if isinstance(p, dict) and p.get("id") == "base_shipping"]
        if len(parts) != 1 or parts[0].get("complete") is not True:
            raise ValueError("operational_courier_observed_fee_amount_invalid")
        amount = _positive_sar(parts[0].get("amount"))
        fee = {"cost": format(amount, ".2f"), "currency": "SAR"}
        payload = {"schema": SCHEMA, "owner_id": state["owner_id"],
                   "order_id": oid, "order_number": number,
                   "party_type": "courier", "party_id": carrier["id"],
                   "currency": "SAR", "amount": fee["cost"],
                   "order_created_at": created.isoformat(),
                   "first_observed_at": observed.isoformat(),
                   "business_date_basis": "first_observed_delivered_status",
                   "original_delivery_time_verified": False,
                   "contract_id": carrier["contract_id"]}
        previous = facts.get(proof_key)
        if previous is not None and previous != payload:
            raise ValueError("operational_courier_observed_fee_saved_conflict")
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        code = str(exc)
        if not code.startswith("operational_courier_observed_fee_"):
            code = "operational_courier_observed_fee_evidence_invalid"
        issue(code, oid)
        return False

    fact(proof_key, payload)
    fact(key, fee)
    if root in obligations:
        obligations[root] = {**obligations[root], "expected": "0.00"}
    put(key, "shipping", carrier["id"], "courier", "SAR",
        confirmed=amount,
        business_date=observed.astimezone(RIYADH).date(),
        fee_component="base_shipping",
        business_date_basis="first_observed_delivered_status",
        recognition_basis=STATUS_BASIS,
        recorded_at=observed.isoformat(), delivered_at=None,
        order_id=oid, order_number=number,
        contract_id=carrier["contract_id"])
    return True
