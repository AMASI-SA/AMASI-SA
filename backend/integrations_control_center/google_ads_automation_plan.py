"""Read-only, owner-scoped Google automation readiness; never dispatch authority.

The route reads existing policy, saved analysis and complete bounded proposal
history. Native inventory is deliberately unsegmented: a date-filtered metrics
report can omit zero-metric campaigns and cannot prove total budget commitment.
No model, mutation gateway, provider writes or fabricated principal is used.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Query

from .google_ads_automation_policy import AUTOMATION_POLICIES, _account_scope, _owner
from .google_ads_autonomy_planner import plan_google_autonomy
from .google_ads_client import GoogleAdsClient, problem
from .google_ads_management import ANALYSES, PROPOSALS, account_client
from .google_ads_workspace import campaign_item, compatible_read

HISTORY_PROJECTION = {"_id": 0, **{key: 1 for key in (
    "user_id", "account_id", "campaign_id", "status", "submission_started",
    "action", "created_campaign_id", "verified", "created_at", "submitted_at", "executed_at")}}
INVENTORY_FIELDS = ["campaign.id", "campaign.name", "campaign.status",
    "campaign_budget.period", "campaign_budget.amount_micros", "campaign_budget.total_amount_micros"]


def now():
    return datetime.now(timezone.utc)


def _blocked(account_id, code):
    return {"provider": "google_ads", "account_id": account_id, "mode": "dry_run",
        "executor_available": False, "executable": False, "provider_writes": 0,
        "budget_semantics": "average_daily_budget", "blockers": [code], "candidates": []}


async def read_google_automation_inventory(db, actor, *, account_id, client_factory=GoogleAdsClient):
    """Read all non-removed campaign settings, including zero-delivery campaigns.

    No report cache is updated. Missing native budget-period values stay unknown
    rather than being inferred to mean DAILY by a generic display projection.
    """
    client, _ = await account_client(db, actor["user_id"], account_id, client_factory=client_factory)
    try:
        metadata = await client.account_metadata()
        if metadata.get("identity_verified") is not True or metadata.get("account_id") != account_id:
            raise problem("google_automation_account_metadata_unproved", "تعذر إثبات هوية حساب جوجل.", 409)
        rows, complete, _ = await compatible_read(client, INVENTORY_FIELDS, "campaign",
            "campaign.status != 'REMOVED'", required=("campaign.id", "campaign.status", "campaign_budget.period", "campaign_budget.amount_micros"), limit=501)
        observed = now().isoformat()
        campaigns = []
        for row in rows[:500]:
            campaign = campaign_item(row, metadata, observed)
            if (row.get("campaignBudget") or {}).get("period") not in ("DAILY", "CUSTOM_PERIOD"):
                campaign["budget_mode"] = None
            campaigns.append(campaign)
        return {"account_id": account_id, "currency_code": metadata["currency_code"], "time_zone": metadata["time_zone"],
            "observed_at": observed, "source": "google_ads_api", "campaigns": campaigns,
            "complete": complete is True and len(rows) <= 500}
    finally:
        await client.close()


def attach_google_ads_automation_plan_routes(router, db, current_user, *,
        reader=read_google_automation_inventory, client_factory=GoogleAdsClient):
    """GET is a plan only; caller input contains no evidence or authorization.

    Use the default unsegmented native inventory reader, rather than injecting a
    date-filtered performance report. The optional reader seam is for contract
    testing or equivalent complete native settings inventories.
    """
    async def build(account_id, analysis_id, user):
        actor = await _owner(db, user)
        scope = await _account_scope(db, actor, account_id)
        account_id = scope["account_id"]
        policy = await db[AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
        # Fail closed before native/storage reads when policy cannot authorize
        # even a prospective plan. This never changes the stored enabled value.
        policy = policy or {**scope, "enabled": False}
        gate = plan_google_autonomy(user_id=actor["user_id"], account_id=account_id,
            policy=policy, analysis={}, workspace={}, history={}, current=now())
        if gate["blockers"] != ["analysis_scope_unproved"]:
            return gate
        if not analysis_id:
            return _blocked(account_id, "analysis_required")
        analysis = await db[ANALYSES].find_one({**scope, "analysis_id": analysis_id}, {"_id": 0})
        if not analysis:
            raise problem("google_ai_analysis_not_found", "التحليل غير متاح لهذا الحساب في متجرك.", 404)
        analysis_gate = plan_google_autonomy(user_id=actor["user_id"], account_id=account_id,
            policy=policy, analysis=analysis, workspace={}, history={}, current=now())
        if analysis_gate["blockers"] != ["workspace_incomplete"]:
            return analysis_gate
        # Read all scoped history, not just recent rows: an old uncertain write
        # must still fence the account. Overflow cannot establish completeness.
        rows = await db[PROPOSALS].find(scope, HISTORY_PROJECTION).sort("created_at", -1).to_list(length=1001)
        if len(rows) > 1000:
            return _blocked(account_id, "history_incomplete")
        inventory = await reader(db, actor, account_id=account_id, client_factory=client_factory)
        return plan_google_autonomy(user_id=actor["user_id"], account_id=account_id,
            policy=policy, analysis=analysis, workspace=inventory,
            history={**scope, "items": rows, "complete": True}, current=now())

    @router.get("/google_ads/automation/plan/{account_id}")
    async def read_plan(account_id: str,
            analysis_id: str | None = Query(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
            user=Depends(current_user)):
        try:
            return await asyncio.wait_for(build(account_id, analysis_id, user), timeout=90.0)
        except asyncio.TimeoutError:
            raise problem("google_automation_plan_timeout", "انتهت مهلة فحص جاهزية جوجل. لم تُرسل أي عملية تنفيذ.", 504) from None
        except HTTPException:
            raise
        except Exception:
            raise problem("google_automation_plan_unavailable", "تعذر فحص جاهزية الإدارة الآلية. لم تُرسل أي عملية تنفيذ.", 503) from None
