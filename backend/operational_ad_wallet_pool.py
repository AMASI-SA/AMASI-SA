"""Current operational wallet/debt balances from evidenced cumulative totals.

Daily spend is not assigned an invented transaction time. Spend and payment
commute for the final balances under wallet-first/debt-first rules; historical
per-payment allocation remains the snapshot saved when that payment was entered.
"""
from decimal import Decimal

from operational_ad_wallet_events import _money


def enabled(state, account_id, currency):
    policy = state.get("ad_funding", {}).get(account_id, {})
    return policy.get("mode") == "wallet_first" and policy.get("currency") == currency


def project_pool(state, account_id, currency):
    if not enabled(state, account_id, currency):
        raise ValueError("operational_wallet_policy_required")
    wallet = debt = spend = pending = paid = Decimal("0.00")
    for opening in state.get("openings", {}).values():
        if (opening["party_type"], opening["party_id"], opening["currency"]) != ("ad_account", account_id, currency):
            continue
        value = _money(opening["amount"])
        if opening["direction"] == "for_us":
            wallet += value
        else:
            debt += value
    for obligation in state.get("engine", {}).get("obligations", {}).values():
        if obligation.get("superseded") or (obligation.get("party_type"), obligation.get("party_id"), obligation.get("currency")) != ("ad_account", account_id, currency):
            continue
        if obligation.get("kind") != "advertising" or obligation.get("funding_policy") != "wallet_first":
            raise ValueError("operational_wallet_policy_history_conflict")
        spend += _money(obligation.get("confirmed", "0"))
        pending += _money(obligation.get("expected", "0"))
    for movement in state.get("movements", []):
        if (movement["party_type"], movement["party_id"], movement["currency"]) != ("ad_account", account_id, currency):
            continue
        if movement["kind"] not in {"payment", "wallet_funding"} or movement["direction"] != "outgoing" or movement.get("allocations"):
            raise ValueError("operational_wallet_movement_unsupported")
        paid += _money(movement["amount"])
    # Opening wallet and opening debt remain separate until actual spend or
    # payment consumes them. Never net two owner-entered starting balances.
    gross_debt = debt + max(spend - wallet, Decimal(0))
    result = {
        "opening_wallet": wallet, "opening_debt": debt,
        "spend": spend, "expected_spend": pending, "payments": paid,
        "gross_debt": gross_debt,
        "debt_settled": min(paid, gross_debt),
        "wallet_balance": max(wallet-spend, Decimal(0)) + max(paid-gross_debt, Decimal(0)),
        "payable": max(gross_debt-paid, Decimal(0)),
        "wallet_spent": min(spend, wallet + max(paid-debt, Decimal(0))),
    }
    for value in result.values():
        _money(value)
    return result
