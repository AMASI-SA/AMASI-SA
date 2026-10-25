"""Bounded TikTok observations in the existing five-hour Campaign AI cycle.

Reuses native facts, native analysis and the shared recommendation store. It
never promotes TikTok conversions or unknown profit to executable financial
advice. ADR-001: additive/flagged, tenant-scoped, bounded and idempotent.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import os
from zoneinfo import ZoneInfo

ANALYSES = "mezan_tiktok_campaign_analyses_v1"
CURSORS = "mezan_tiktok_campaign_ai_cursors_v1"
MAX_CAMPAIGNS = 3
TIMEOUT_SECONDS = 160


async def candidates(db, user, start, end):
    from integrations_control_center.tiktok_native_hierarchy import tiktok_workspace
    scope = {"_id": user, "user_id": user}
    cursor = await db[CURSORS].find_one(scope, {"page": 1}, max_time_ms=1500) or {}
    page = cursor.get("page")
    page = page if type(page) is int and page > 0 else 1
    result = await tiktok_workspace(db, user, from_date=start, to_date=end,
        entity_type="campaign", page=page, limit=MAX_CAMPAIGNS)
    rows = [r for r in result.get("entities") or [] if r.get("data_complete") is True and r.get("spend_sar") is not None]
    coverage = result.get("campaign_pagination") or {}
    pages = max(1, int(coverage.get("pages") or 1))
    actual = max(1, int(coverage.get("page") or 1))
    await db[CURSORS].update_one(scope, {"$set": {"page": actual % pages + 1}}, upsert=True)
    return rows, {**coverage, "scope": "selected_page_only", "rotation_enabled": True}


async def analyze(db, user, row, start, end):
    from integrations_control_center.tiktok_native_insights import TikTokCampaignAnalysisInput, analyze_tiktok_campaign
    return await analyze_tiktok_campaign(db, user, TikTokCampaignAnalysisInput(
        account_id=row["account_id"], campaign_id=row["entity_id"], from_date=start, to_date=end))


def project(analysis, row, snapshot_id):
    context = analysis.get("context") or {}
    campaign = context.get("campaign") or {}
    if campaign.get("account_id") != row["account_id"] or campaign.get("campaign_id") != row["entity_id"]:
        raise ValueError("tiktok_periodic_identity_mismatch")
    note = analysis["recommendation"]
    if note.get("focus") not in {"monitor", "tracking", "creative"} or not all(isinstance(note.get(k), str) and note[k].strip() for k in ("summary", "next_step")):
        raise ValueError("tiktok_periodic_invalid_observation")
    identifier = hashlib.sha256(f"{snapshot_id}:{row['account_id']}:{row['entity_id']}".encode()).hexdigest()
    return {"recommendation_id": "tiktok:" + identifier, "provider": "tiktok",
        "account_id": row["account_id"], "account_name": row.get("account_name"),
        "entity_level": "campaign", "entity_id": row["entity_id"], "entity_name": row.get("entity_name"),
        "campaign_id": row["entity_id"], "campaign_name": row.get("entity_name"),
        "action": "monitor", "recommended_action": {"monitor": "OBSERVE", "tracking": "REVIEW_TRACKING", "creative": "REFRESH_CREATIVE"}[note["focus"]],
        "action_type": "creative" if note["focus"] == "creative" else "diagnostic",
        "diagnosis": note["summary"], "rationale": note["summary"], "why": note["summary"],
        "observation_plan": note["next_step"], "priority": "medium", "confidence": "medium",
        "recommended_wait_hours": 5, "generated_at": analysis.get("analyzed_at"),
        "recommendation_source": "tiktok_native_ai", "executable": False, "approval_available": False,
        "execution_status": "recommendation_only", "provider_writes": 0,
        "financial_orders": None, "sales_sar": None, "profit_sar": None,
        "financial_coverage": "not_verified", "review_route": {"provider": "tiktok", "account_id": row["account_id"], "campaign_id": row["entity_id"]}}


async def append_tiktok_periodic_recommendations(db, user, snapshot, *, current=None, loader=None, analyzer=None, pause=None):
    if os.environ.get("MEZAN_TIKTOK_AI_PERIODIC_ENABLED", "false").lower() not in {"1", "true", "yes", "on"} or not isinstance(snapshot, dict) or not snapshot.get("snapshot_id"):
        return snapshot
    from campaign_ai_monitor_legacy import RECOMMENDATION_COLLECTION
    source = db[RECOMMENDATION_COLLECTION]
    scope = {"user_id": user, "snapshot_id": snapshot["snapshot_id"]}
    stamp = (current or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Claim before billable work. A crash is visible as incomplete rather than
    # silently causing another model request for this same source snapshot.
    claim = await source.update_one({**scope, "tiktok_periodic": {"$exists": False}}, {"$set": {"tiktok_periodic": {"status": "running", "started_at": stamp.isoformat(), "provider_writes": 0}}})
    if claim.modified_count != 1:
        return snapshot
    end = stamp.astimezone(ZoneInfo("Asia/Riyadh")).date() - timedelta(days=1)
    start = end - timedelta(days=2)
    output, statuses, coverage = [], [], {}
    deadline = asyncio.get_running_loop().time() + TIMEOUT_SECONDS
    try:
        rows, coverage = await (loader or candidates)(db, user, start.isoformat(), end.isoformat())
        for index, row in enumerate(rows[:MAX_CAMPAIGNS]):
            if index:
                # Honor native per-owner model cadence; never bypass the same
                # limiter used by the owner-facing campaign analysis route.
                await (pause or asyncio.sleep)(15)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                statuses.append({"status": "cycle_timeout"}); break
            try:
                analysis = await asyncio.wait_for((analyzer or analyze)(db, user, row, start.isoformat(), end.isoformat()), timeout=min(45, remaining))
                projected = project(analysis, row, snapshot["snapshot_id"])
                identifier = projected["recommendation_id"]
                await db[ANALYSES].update_one({"user_id": user, "analysis_id": identifier}, {"$setOnInsert": {
                    "user_id": user, "analysis_id": identifier, "snapshot_id": snapshot["snapshot_id"],
                    "account_id": row["account_id"], "campaign_id": row["entity_id"],
                    "date_from": start.isoformat(), "date_to": end.isoformat(),
                    "created_at": stamp.isoformat(), "result": {"recommendation": analysis["recommendation"], "model": analysis.get("model")}}}, upsert=True)
                output.append(projected)
                statuses.append({"account_id": row["account_id"], "campaign_id": row["entity_id"], "status": "analyzed"})
            except Exception:
                statuses.append({"account_id": row["account_id"], "campaign_id": row["entity_id"], "status": "analysis_unavailable"})
    except Exception:
        statuses.append({"status": "source_unavailable"})
    metadata = {"status": "complete" if output else "unavailable", "source": "campaign_ai_global_cycle",
        "decision_interval_hours": 5, "campaign_limit": MAX_CAMPAIGNS, "coverage": coverage,
        "campaigns": statuses, "recommendations": len(output), "provider_writes": 0,
        "financial_writes": 0, "financial_coverage": "not_verified", "autonomous_execution": False,
        "coverage_exhaustive": False}
    await source.update_one({**scope, "tiktok_periodic.status": "running"}, {"$set": {"tiktok_periodic": metadata},
        "$push": {"recommendations": {"$each": output}}, "$addToSet": {"providers": "tiktok"}})
    return {**snapshot, "recommendations": [*(snapshot.get("recommendations") or []), *output],
        "providers": list(dict.fromkeys([*(snapshot.get("providers") or []), "tiktok"])), "tiktok_periodic": metadata}
