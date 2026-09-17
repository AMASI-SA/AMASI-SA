"""Consumer checks for measured campaign attribution, separate from retrieval."""
from typing import Any


def campaign_attribution_complete(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    total = value.get("evaluated_orders")
    matched = value.get("matched_orders")
    gap = value.get("unmatched_orders")
    if not all(type(n) is int and n >= 0 for n in (total, matched, gap)):
        return False
    return bool(
        value.get("status") == "complete"
        and value.get("complete_population") is True
        and value.get("date_scope") == "account_timezone"
        and total == matched + gap
        and gap == 0
        and value.get("reason_counts") == {}
        and value.get("coverage_pct") == (100 if total else None)
    )
