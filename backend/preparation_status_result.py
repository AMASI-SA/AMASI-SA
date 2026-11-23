"""Read-only presentation of status checks for an already saved preparation file."""
from __future__ import annotations

import hashlib
from typing import Any


def preparation_status_sync_view(row: dict[str, Any]) -> dict[str, Any] | None:
    numbers = row.get("status_sync_order_numbers")
    if not isinstance(numbers, list):
        return row.get("order_status_sync")
    results = row.get("status_sync_results") or {}
    outcomes = []
    checked = []
    for number in numbers:
        key = hashlib.sha256(number.encode("utf-8")).hexdigest()
        saved = results.get(key) or {"order_number": number, "state": "pending",
                                     "reason": "preparation_status_not_checked"}
        outcomes.append({key: value for key, value in saved.items() if key != "checked_at"})
        if saved.get("checked_at"):
            checked.append(saved["checked_at"])
    confirmed = sum(item["state"] == "confirmed" for item in outcomes)
    partial = sum(item["state"] == "partial" for item in outcomes)
    pending = sum(item["state"] == "pending" for item in outcomes)
    return {"state": "pending" if pending or not outcomes else "partial" if partial else "confirmed",
            "confirmed_count": confirmed, "partial_count": partial, "pending_count": pending,
            "orders": outcomes, "checked_at": max(checked) if checked else None}

