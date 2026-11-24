"""Native driver earnings can date their fee without inventing a delivery time."""
from decimal import Decimal
from zoneinfo import ZoneInfo

from operational_payment_evidence import _amount, _instant


PREFIX = "native_driver_earned:"
SCHEMA = "operational_native_driver_earning_v1"


def earning_fact(owner, order, earning):
    """Stable, already authenticated source fields; never observation metadata."""
    oid, number = str(order.get("id") or ""), str(order.get("order_number") or "")
    if (not owner or not oid or not number or earning.get("order_id") != oid
            or earning.get("order_number") != number or earning.get("currency") != "SAR"
            or any(not isinstance(earning.get(key), str) or not earning[key]
                   for key in ("party_id", "assignment_id", "earning_id"))):
        raise ValueError("driver_fee_evidence_conflict")
    amount, at = _amount(earning.get("candidate_amount"), "SAR"), _instant(earning.get("earned_at"))
    if amount < 0:
        raise ValueError("driver_fee_amount_conflict")
    return {"schema": SCHEMA, "owner_id": owner, "order_id": oid, "order_number": number,
        "party_type": "store_driver", "party_id": earning["party_id"], "currency": "SAR",
        "assignment_id": earning["assignment_id"], "earning_id": earning["earning_id"],
        "amount": format(amount, ".2f"), "earned_at": at.isoformat()}


def apply_undated_fee(state, order, start, now, put, fact, issue, allocations):
    """Admit only the exact native base fee; no delivery/COD/cash mutation.

    The source adapter attaches native_driver_earning only after its existing
    owner, assignment, earning, payout, tariff and history checks all pass.
    This boundary retains those checks and independently binds the resulting
    identity, amount and original earned_at before writing a financial row.
    """
    earning = order.get("native_driver_earning")
    if not isinstance(earning, dict):
        return False
    oid, root = str(order.get("id") or ""), "shipping:" + str(order.get("id") or "")
    key = root + ":base_shipping"
    obligations, facts = state["engine"]["obligations"], state["engine"]["facts"]
    try:
        payload = earning_fact(state.get("owner_id"), order, earning)
        at, amount = _instant(payload["earned_at"]), Decimal(payload["amount"])
        carrier, event = order.get("carrier") or {}, order.get("delivery_event") or {}
        if (not max(start, _instant(order["created_at"])) <= at <= now
                or earning.get("delivered_at") is not None or event.get("delivered_at") is not None
                or event.get("party_type") != "store_driver" or event.get("party_id") != payload["party_id"]
                or carrier.get("party_type", carrier.get("kind")) != "store_driver"
                or carrier.get("id") != payload["party_id"] or carrier.get("fee_recognition_held") is True):
            raise ValueError("driver_fee_evidence_conflict")
        previous_fact = facts.get(PREFIX + oid)
        if previous_fact is not None and previous_fact != payload:
            raise ValueError("driver_fee_saved_conflict")
        previous = [obligations[name] for name in (root, key)
                    if name in obligations and _amount(obligations[name].get("confirmed", "0"), "SAR") > 0]
        if previous:
            if (len(previous) != 1 or previous[0].get("party_type") != "store_driver"
                    or previous[0].get("party_id") != payload["party_id"] or previous[0].get("currency") != "SAR"
                    or _amount(previous[0].get("confirmed"), "SAR") != amount):
                raise ValueError("driver_fee_saved_conflict")
            return True  # Existing financial rows retain their exact content/date.
        if root in allocations or key in allocations:
            raise ValueError("driver_fee_saved_conflict")
        fee = {"cost": payload["amount"], "currency": "SAR"}
        if facts.get(key) is not None and facts[key] != fee:
            raise ValueError("driver_fee_saved_conflict")
        parts = [part for part in carrier.get("fee_components", []) if part.get("id") == "base_shipping"]
        if (len(parts) != 1 or parts[0].get("complete") is not True
                or _amount(parts[0].get("amount"), "SAR") != amount or carrier.get("currency") != "SAR"):
            raise ValueError("driver_fee_amount_conflict")
    except (ValueError, TypeError, KeyError, ArithmeticError) as error:
        code = str(error) if str(error).startswith("driver_fee_") else "driver_fee_evidence_conflict"
        issue(code, oid)
        return False
    fact(PREFIX + oid, payload)
    fact(key, fee)
    if root in obligations:
        obligations[root] = {**obligations[root], "expected": "0.00"}
    put(key, "shipping", payload["party_id"], "store_driver", "SAR", confirmed=amount,
        business_date=at.astimezone(ZoneInfo("Asia/Riyadh")).date(), fee_component="base_shipping",
        business_date_basis="native_driver_earned_at", earned_at=payload["earned_at"],
        source_earning_id=payload["earning_id"], delivered_at=None)
    return True
