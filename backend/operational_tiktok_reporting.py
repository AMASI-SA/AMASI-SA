"""Read-only TikTok source spend for operational balance account review.

Never posts financial movements: approved funding bindings continue to be
processed exclusively by the established operational balance engine.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from dashboard_tiktok_coverage import tiktok_reporting_coverage


def build_tiktok_reported_account_rows(
    accounts: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    start: str,
    end: str,
) -> dict[str, Any]:
    expected_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    seen_ids: set[str] = set()
    items = []
    known_subtotal_sar = 0.0
    all_complete = True
    for account in accounts:
        account_id = str(account.get("ad_account_id") or account.get("external_account_id") or "")
        if not account_id or account_id in seen_ids:
            all_complete = False
            continue
        seen_ids.add(account_id)
        own = [fact for fact in facts if fact.get("ad_account_id") == account_id]
        coverage = tiktok_reporting_coverage([account_id], start, end, own)
        coverage_ok = coverage["coverage_complete"]
        # Sum only when exactly one verified fact exists per day.
        spend_sar = round(sum(float(fact["spend_sar"]) for fact in own), 2) if coverage_ok else None
        matches = [
            binding for binding in bindings
            if binding.get("platform") == "tiktok"
            and binding.get("integration_account_id") == account.get("mezan_integration_account_id")
            and binding.get("platform_account_id") == account_id
            and binding.get("confirmed_by") and binding.get("confirmed_at")
        ]
        binding_state = (
            "approved" if len(matches) == 1
            else "ambiguous" if len(matches) > 1 else "setup_required"
        )
        if spend_sar is None:
            all_complete = False
        else:
            known_subtotal_sar += spend_sar
        items.append({
            "account_id": account_id,
            "name": account.get("display_name") or account_id,
            "currency": account.get("currency"),
            "timezone": account.get("timezone"),
            "last_sync_at": account.get("last_sync_at"),
            "source_spend_sar": spend_sar,
            "binding_state": binding_state,
            "covered_days": coverage["covered_account_days"],
            "expected_days": expected_days,
            "data_state": coverage["data_state"],
        })
    items.sort(key=lambda item: (item["name"], item["account_id"]))
    if not items:
        all_complete = False
    return {
        "items": items,
        "accounts": len(items),
        "known_subtotal_sar": round(known_subtotal_sar, 2),
        "total_source_spend_sar": round(known_subtotal_sar, 2) if all_complete else None,
        "coverage_complete": all_complete,
        "source_only": True,
        "posted_to_operational_balances": False,
        "currency": "SAR",
        "period": {"from_date": start, "to_date": end},
        "scope": "owner_connected_tiktok_advertisers",
    }
