"""Native Google evidence in the existing globally gated Campaign AI cycle.

Google recommendations remain read-only and use the native approval gateway.
No Google fact is converted into a synthetic Salla sale or SAR profit figure.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from typing import Any
from zoneinfo import ZoneInfo

MAX_ACCOUNTS = 4
CYCLE_TIMEOUT_SECONDS = 120
ACCOUNT_TIMEOUT_SECONDS = 90
logger = logging.getLogger(__name__)


async def _account_analysis(db, user_id, account, current):
    from integrations_control_center.google_ads_ai import GoogleAIAnalysisInput, analyze_google_campaigns
    from integrations_control_center.google_ads_management import account_client
    from integrations_control_center.google_ads_workspace import read_google_workspace

    account_id = str(account["account_id"])
    client, _ = await account_client(db, user_id, account_id)
    try:
        metadata = await client.account_metadata()
        end = current.astimezone(ZoneInfo(metadata["time_zone"])).date()
        start = end - timedelta(days=2)
    finally:
        await client.close()
    # A worker principal has no marketer/owner write permission. Account access
    # is still verified by the same tenant-bound native reader used by the UI.
    actor = {"user_id": user_id, "actor_id": user_id, "owner": False, "can_draft": False}
    facts = await read_google_workspace(db, actor, from_date=start.isoformat(),
        to_date=end.isoformat(), account_id=account_id)
    if facts.get("complete") is not True:
        return None
    request = GoogleAIAnalysisInput(account_id=account_id,
        date_from=start.isoformat(), date_to=end.isoformat())
    facts = {**facts, "date_from": request.date_from, "date_to": request.date_to,
        "permitted_actions": [], "limits": {}}
    return await analyze_google_campaigns(db, user_id, request, facts=facts)


def recommendation_rows(analysis, account, snapshot_id, generated_at):
    """Project validated native advice into the shared display contract only."""
    account_id = str(account["account_id"])
    if analysis.get("provider") != "google_ads" or str(analysis.get("account_id")) != account_id:
        raise ValueError("google_periodic_account_mismatch")
    period = analysis.get("period") or {}
    rows = []
    for item in analysis.get("recommendations") or []:
        if str(item.get("account_id")) != account_id:
            raise ValueError("google_periodic_recommendation_account_mismatch")
        action = item["action"]
        rows.append({
            "recommendation_id": f"google:{snapshot_id}:{item['recommendation_id']}",
            "provider": "google", "account_id": account_id,
            "account_name": account.get("name") or "حساب جوجل",
            "entity_level": "campaign", "entity_id": item["campaign_id"],
            "entity_name": item.get("title") or item.get("campaign_name"),
            "campaign_id": item["campaign_id"], "campaign_name": item.get("campaign_name"),
            "recommended_action": action, "action": "monitor",
            "action_type": "creative" if action in {"REFRESH_CREATIVE", "CREATE_TEST_DRAFT"} else "product_change" if action in {"REVIEW_PRODUCT_PRICE", "REVIEW_PRODUCT_PAGE"} else "diagnostic",
            "diagnosis": item["rationale"], "rationale": item["rationale"],
            "why": item["rationale"], "priority": item["priority"],
            "confidence": item["confidence"], "observation_plan": item["observation_plan"],
            "recommended_wait_hours": 5, "generated_at": generated_at,
            "recommendation_source": "google_ads_native_ai",
            "evidence": item.get("evidence") or [], "limitations": item.get("limitations") or [],
            "period_native": period,
            "executable": False, "approval_available": False,
            "execution_status": "recommendation_only", "provider_writes": 0,
            "review_route": {"provider": "google", "account_id": account_id,
                "campaign_id": item["campaign_id"]},
        })
    return rows


async def append_google_periodic_recommendations(db: Any, user_id: str, snapshot: dict,
    *, now=None, account_loader=None, account_analyzer=None) -> dict:
    from integrations_control_center.google_ads_management import ANALYSES, available_accounts
    from campaign_ai_monitor_legacy import RECOMMENDATION_COLLECTION

    snapshot_id = snapshot.get("snapshot_id")
    if not snapshot_id:
        return snapshot
    try:
        accounts = await (account_loader or available_accounts)(db, user_id)
    except Exception:
        # An unavailable Google discovery must not fail other providers' cycle.
        logger.warning("google_periodic_account_discovery_unavailable")
        return snapshot
    if not accounts:
        return snapshot
    scope = {"user_id": user_id, "snapshot_id": snapshot_id}
    prior = await db[RECOMMENDATION_COLLECTION].find_one(scope, {"_id": 0,
        "google_periodic": 1, "recommendations": 1, "providers": 1})
    if prior is None:
        return snapshot
    if prior.get("google_periodic") is not None:
        return {**snapshot, **prior}
    current = (now() if now else datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = current.isoformat()
    rows, statuses = [], []
    # Prefer the merchant's selected link; cap model work without claiming that
    # accounts beyond this bounded cycle have been analyzed.
    accounts = sorted(accounts, key=lambda item: not item.get("selected", False))
    deadline = asyncio.get_running_loop().time() + CYCLE_TIMEOUT_SECONDS
    for account in accounts[:MAX_ACCOUNTS]:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            statuses.append({"account_id": account["account_id"], "status": "cycle_timeout"})
            continue
        try:
            analysis = await asyncio.wait_for((account_analyzer or _account_analysis)(db, user_id, account, current),
                timeout=min(ACCOUNT_TIMEOUT_SECONDS, remaining))
            if analysis is None:
                statuses.append({"account_id": account["account_id"], "status": "incomplete_provider_evidence"})
                continue
            generated_at = (now() if now else datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
            projected = recommendation_rows(analysis, account, snapshot_id, generated_at)
            period = analysis["period"]
            analysis_id = hashlib.sha256(f"{user_id}:{snapshot_id}:{account['account_id']}".encode()).hexdigest()
            await db[ANALYSES].update_one({"user_id": user_id, "analysis_id": analysis_id}, {"$setOnInsert": {
                "analysis_id": analysis_id, "user_id": user_id, "actor_id": user_id,
                "account_id": account["account_id"], "date_from": period["date_from"],
                "date_to": period["date_to"], "created_at": generated_at,
                "source": "campaign_ai_global_cycle", "snapshot_id": snapshot_id, "result": analysis,
            }}, upsert=True)
            rows.extend(projected)
            statuses.append({"account_id": account["account_id"], "status": "analyzed",
                "recommendations": len(projected), "period": period})
        except asyncio.TimeoutError:
            statuses.append({"account_id": account["account_id"], "status": "analysis_timeout"})
        except Exception:
            # Do not put provider/model error bodies or credentials in a shared
            # recommendation snapshot. Other providers/accounts keep operating.
            statuses.append({"account_id": account["account_id"], "status": "analysis_unavailable"})
    for account in accounts[MAX_ACCOUNTS:]:
        statuses.append({"account_id": account["account_id"], "status": "cycle_account_limit"})
    metadata = {"source": "campaign_ai_global_cycle", "decision_interval_hours": 5,
        "accounts": statuses, "recommendations": len(rows), "provider_writes": 0,
        "financial_writes": 0, "native_gateway_required": True}
    updated = await db[RECOMMENDATION_COLLECTION].update_one(
        {**scope, "google_periodic": {"$exists": False}},
        {"$push": {"recommendations": {"$each": rows}},
         "$addToSet": {"providers": "google"}, "$set": {"google_periodic": metadata}})
    if not getattr(updated, "modified_count", 0):
        return snapshot
    return {**snapshot, "recommendations": [*(snapshot.get("recommendations") or []), *rows],
        "providers": list(dict.fromkeys([*(snapshot.get("providers") or []), "google"])),
        "google_periodic": metadata}
