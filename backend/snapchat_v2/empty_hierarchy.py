"""Provider-confirmed empty hierarchy evidence; never infer it from zero spend."""
from datetime import date, timedelta
from typing import Any

from .models import (SNAPCHAT_PROVIDER, DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
                     DEFAULT_VIEW_ATTRIBUTION_WINDOW, build_attribution_key)

LEVELS = ("campaign", "ad_squad", "ad")
VERSION = "snapchat_empty_hierarchy_v1"


def complete_empty_response(coverage: dict) -> bool:
    expected = coverage.get("expected_requests")
    return (coverage.get("status") == "complete"
            and coverage.get("data_state") == "confirmed_no_data"
            and type(expected) is int and expected > 0
            and type(coverage.get("completed_requests")) is int
            and coverage["completed_requests"] == expected
            and type(coverage.get("rows_received")) is int
            and coverage["rows_received"] == 0)


def empty_hierarchy_proof(account: dict, report_dates: list[date],
                          action: str, campaign: dict, identities: dict) -> dict | None:
    """Require complete unfiltered catalogs AND the bounded account DAY response."""
    if not report_dates or len(report_dates) > 7 or not complete_empty_response(campaign.get("coverage") or {}):
        return None
    if any(not complete_empty_response((identities.get(level) or {}).get("coverage") or {})
           for level in LEVELS):
        return None
    account_id, currency, zone = (str(account.get(key) or "")
                                 for key in ("ad_account_id", "currency", "timezone"))
    if not account_id or not currency or not zone:
        return None
    days = sorted({day.isoformat() for day in report_dates})
    if len(days) != (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1:
        return None
    return {"contract_version": VERSION, "ad_account_id": account_id,
            "currency": currency, "account_timezone": zone,
            "report_dates": days, "action_report_time": action,
            "attribution_key": build_attribution_key(action, {
                "swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
                "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW}),
            "campaign_response": dict(campaign["coverage"]),
            "catalog_responses": {level: dict(identities[level]["coverage"]) for level in LEVELS}}


def valid_proof(proof: dict, *, account_id: str, currency: str,
                timezone_name: str, action: str, days: set[str]) -> bool:
    try:
        rebuilt = empty_hierarchy_proof(
            {"ad_account_id": account_id, "currency": currency, "timezone": timezone_name},
            [date.fromisoformat(day) for day in proof.get("report_dates", [])], action,
            {"coverage": proof.get("campaign_response")},
            {level: {"coverage": (proof.get("catalog_responses") or {}).get(level)} for level in LEVELS})
    except (TypeError, ValueError, AttributeError):
        return False
    return rebuilt is not None and rebuilt == proof and days <= set(proof["report_dates"])


async def empty_period_complete(db: Any, *, user_id: str, account_id: str,
                                currency: str, timezone_name: str, date_from: date,
                                date_to: date, action: str) -> bool:
    """Accept immutable committed proof only, never a latest-run substitute."""
    from .committed_totals import COLLECTION, _rows
    from .sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
    days = {(date_from + timedelta(days=i)).isoformat()
            for i in range((date_to - date_from).days + 1)}
    if db is None or not days or not currency or not timezone_name:
        return False
    scope = {"user_id": str(user_id), "provider": SNAPCHAT_PROVIDER,
             "ad_account_id": str(account_id)}
    attribution = build_attribution_key(action, {"swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
                                                "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW})
    snapshots = await _rows(db[COLLECTION].find({**scope,
        "account_timezone": timezone_name, "action_report_time": action,
        "attribution_key": attribution,
        "report_date": {"$gte": date_from.isoformat(), "$lte": date_to.isoformat()}},
        {"_id": 0}), len(days))
    if {row.get("report_date") for row in snapshots} != days:
        return False
    runs = {}
    for row in snapshots:
        proof = row.get("empty_hierarchy_proof") or {}
        if (any((row.get("rows") or {}).get(level) != [] for level in LEVELS)
                or not valid_proof(proof, account_id=account_id, currency=currency,
                    timezone_name=timezone_name, action=action, days={row["report_date"]})):
            return False
        run_id = row.get("sync_run_id")
        if not run_id:
            return False
        if run_id not in runs:
            runs[run_id] = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one({**scope,
                "sync_run_id": run_id, "status": "complete", "financial_sync_status": "complete",
                "identity_sync_status": "complete", **{f"{level}_sync_status": "complete" for level in LEVELS}}, {"_id": 0})
        run = runs[run_id] or {}
        if (run.get("summary", {}).get("empty_hierarchy_proof") != proof
                or (run.get("request_window") or {}).get("action_report_time") != action
                or not (run.get("request_window") or {}).get("date_from", "~") <= row["report_date"] <= (run.get("request_window") or {}).get("date_to", "")):
            return False
    return True
