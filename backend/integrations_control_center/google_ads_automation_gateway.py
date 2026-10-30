"""Explicit standing-policy gateway; never a human principal impersonation.

Only server-owned advice and native reads build a preview. Native proposal CAS,
account fence, durable submission floor and readback remain in the shared core.
No unknown outcome is retried; unsubmitted recovery cancels the durable proposal
before settling its policy claim or releasing its account fence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import google_ads_management as management
from . import google_ads_automation_authority as authority
from .google_ads_automation_plan import INVENTORY_FIELDS
from .google_ads_automation_policy import AUTOMATION_POLICIES, _owner, _account_scope
from .google_ads_autonomy_planner import plan_google_autonomy
from .google_ads_client import GoogleAdsClient, identifier, problem
from .google_ads_workspace import campaign_item, compatible_read


def _actor(user_id):
    return {"user_id": user_id, "actor_id": authority.SERVICE_ACTOR_ID,
            "owner": False, "can_read": True, "can_draft": False}


def _enabled():
    if not authority.execution_enabled():
        raise problem("google_automation_execution_disabled", "التنفيذ الآلي لجوجل غير مفعّل.", 503)


async def _policy(db, user_id, account_id, revision=None):
    _enabled()
    if revision is not None and (type(revision) is not int or revision < 1):
        raise problem("google_automation_policy_changed", "إصدار التفويض غير صالح.", 409)
    actor = await _owner(db, {"id": user_id})
    scope = await _account_scope(db, actor, account_id)
    row = await db[AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
    expiry = authority._instant((row or {}).get("expires_at"))
    if (not row or row.get("enabled") is not True or not authority.activation_proof_valid(row)
        or expiry is None or expiry <= management.now()
        or (revision is not None and row.get("revision") != revision)):
        raise problem("google_automation_policy_changed", "تفويض جوجل غير متاح أو تغير إصداره.", 409)
    return row


async def _inventory(client, metadata):
    fields = [*INVENTORY_FIELDS, 'campaign_budget.resource_name', 'campaign_budget.explicitly_shared', 'campaign_budget.reference_count']
    rows, complete, _ = await compatible_read(client, fields, "campaign",
        "campaign.status != 'REMOVED'", required=("campaign.id", "campaign.status", "campaign_budget.period", "campaign_budget.amount_micros",
            'campaign_budget.resource_name', 'campaign_budget.explicitly_shared', 'campaign_budget.reference_count'), limit=501)
    observed = management.now().isoformat()
    campaigns = []
    for item in rows[:500]:
        campaign = campaign_item(item, metadata, observed)
        if (item.get("campaignBudget") or {}).get("period") not in ("DAILY", "CUSTOM_PERIOD"):
            campaign["budget_mode"] = None
        budget = item.get('campaignBudget') or {}
        campaign['budget_resource'] = budget.get('resourceName')
        # Missing values are unknown, never inferred as an unshared budget.
        campaign['shared_budget'] = (False if budget.get('explicitlyShared') is False
            and (type(budget.get('referenceCount')) is int and budget['referenceCount'] == 1
                or type(budget.get('referenceCount')) is str and budget['referenceCount'] == '1') else None)
        campaigns.append(campaign)
    return {"account_id": metadata["account_id"], "currency_code": metadata["currency_code"],
        "time_zone": metadata["time_zone"], "observed_at": observed, "source": "google_ads_api",
        "campaigns": campaigns, "complete": complete is True and len(rows) <= 500}


async def _history(db, user_id, account_id):
    scope = {"user_id": user_id, "account_id": account_id}
    projection = {"_id": 0, **{field: 1 for field in authority.HISTORY_FIELDS}}
    rows = await db[management.PROPOSALS].find(scope, projection).sort("created_at", -1).to_list(length=1001)
    return {**scope, "items": rows[:1000], "complete": len(rows) <= 1000}


async def preview_google_automation_recommendation(db, *, user_id, account_id, policy_revision,
        analysis_id, recommendation_id, client_factory=GoogleAdsClient):
    policy = await _policy(db, user_id, account_id, policy_revision)
    account_id = identifier(account_id)
    analysis = await db[management.ANALYSES].find_one({"user_id": user_id, "account_id": account_id,
        "analysis_id": analysis_id}, {"_id": 0})
    if not analysis:
        raise problem("google_automation_analysis_unproved", "التحليل غير متاح لهذا الحساب.", 404)
    client, _ = await management.account_client(db, user_id, account_id, client_factory=client_factory)
    try:
        metadata = await client.account_metadata()
        if metadata.get("identity_verified") is not True or metadata.get("account_id") != account_id:
            raise problem("google_automation_native_evidence_invalid", "تعذر إثبات حساب جوجل.", 409)
        inventory = await _inventory(client, metadata)
    finally:
        await client.close()
    history = await _history(db, user_id, account_id)
    plan = plan_google_autonomy(user_id=user_id, account_id=account_id, policy=policy,
        analysis=analysis, workspace=inventory, history=history, current=management.now())
    candidates = [c for c in plan["candidates"] if c.get("recommendation_id") == recommendation_id]
    if plan["blockers"] or len(candidates) != 1 or candidates[0].get("decision") != "eligible_for_preview":
        raise problem("google_automation_plan_not_eligible", "التوصية غير مؤهلة ضمن حدود التفويض الحالي.", 409)
    candidate = candidates[0]
    # Recheck after native reads. A preview is validation-only, never authority.
    fresh_policy = await _policy(db, user_id, account_id, policy_revision)
    if authority.policy_authorization_digest(policy) != authority.policy_authorization_digest(fresh_policy):
        raise problem("google_automation_policy_changed", "تغير تفويض جوجل أثناء المعاينة.", 409)
    from .google_ads_campaign_contract import GoogleCampaignActionInput
    body = GoogleCampaignActionInput(account_id=account_id, campaign_id=candidate["campaign_id"],
        action=candidate["native_action"], changes=candidate["changes"],
        reason="توصية جوجل ضمن تفويض الإدارة الآلية", idempotency_key=candidate["idempotency_key"])
    source = authority.make_automation_source(fresh_policy, analysis_id=analysis_id, recommendation_id=recommendation_id)
    return await management._preview_google_action(db, _actor(user_id), body,
        client_factory=client_factory, automation_source=source)


async def _execution_preflight(db, row):
    source = authority._bound_proposal(row)
    await _policy(db, row["user_id"], row["account_id"], source["policy_revision"])
    if not row.get("before_digest") or not row.get("campaign_id"):
        raise problem("google_automation_native_evidence_invalid", "لا توجد إعدادات حملة مثبتة للتنفيذ.", 409)


async def _authorize_dispatch(db, row, client, metadata, epoch, snapshot, *, read_started_at):
    inventory = await _inventory(client, metadata)
    reply = await authority.authorize_google_automation_claim(db, user_id=row["user_id"],
        account_id=row["account_id"], proposal_id=row["proposal_id"], execution_claim_id=row["execution_claim_id"],
        native_evidence={"metadata": metadata, "auth_epoch": epoch, "campaign_snapshot": snapshot,
            "inventory": inventory, "observed_at": read_started_at})
    if (not isinstance(reply, dict) or reply.get("authorized") is not True
        or reply.get("claim_id") != row["execution_claim_id"] or reply.get("proposal_id") != row["proposal_id"]
        or reply.get("submission_floor_required") is not True):
        raise problem("google_automation_authorization_unconfirmed", "لم يثبت إقرار تفويض الإرسال؛ تحقق دون إعادة إرسال.", 503)
    return reply


async def _settle_if_present(db, row):
    policy = await db[AUTOMATION_POLICIES].find_one({"user_id": row["user_id"], "account_id": row["account_id"]}, {"_id": 0})
    if not policy:
        raise problem("google_automation_settlement_unconfirmed", "تعذر إثبات سجل التفويض؛ تبقى العملية محجوزة للتحقق.", 503)
    claim = policy.get("_dispatch_claim")
    if claim is None:
        return
    if (not isinstance(claim, dict) or claim.get("proposal_id") != row["proposal_id"]
        or claim.get("execution_claim_id") != row.get("execution_claim_id")):
        raise problem("google_automation_claim_pending", "تفويض حساب جوجل يحتاج تحققًا مستقلًا.", 409)
    await authority.settle_google_automation_claim(db, user_id=row["user_id"], account_id=row["account_id"],
        claim_id=claim["claim_id"])


async def execute_google_automation_proposal(db, *, user_id, account_id, proposal_id, client_factory=GoogleAdsClient):
    row = await management.get_proposal(db, user_id, proposal_id)
    if row.get("account_id") != identifier(account_id):
        raise problem("google_automation_proposal_binding_invalid", "العملية ليست ضمن الحساب المحدد.", 403)
    authority._bound_proposal(row)
    if row.get("status") == "completed" and row.get("verified"):
        await _settle_if_present(db, row)
        await management._release_fence(db, row)
        return management.public_proposal(row)
    _enabled()
    result = await management._execute_google_proposal(db, _actor(user_id), proposal_id,
        row["confirmation_digest"], client_factory=client_factory, automation=True)
    fresh = await management.get_proposal(db, user_id, proposal_id)
    if fresh.get("status") in {"completed", "failed"}:
        await _settle_if_present(db, fresh)
        await management._release_fence(db, fresh)
    return result


async def reconcile_google_automation_proposal(db, *, user_id, account_id, proposal_id, client_factory=GoogleAdsClient):
    # Recovery remains read-only after feature disable or policy revocation.
    row = await management.get_proposal(db, user_id, proposal_id)
    if row.get("account_id") != identifier(account_id):
        raise problem("google_automation_proposal_binding_invalid", "العملية ليست ضمن الحساب المحدد.", 403)
    authority._bound_proposal(row)
    if row.get("status") in {"completed", "failed"}:
        rejection = row.get("rejection") or {}
        proven = (authority.stopped_before_http_proven(row) or row.get("status") == "completed" and row.get("verified") is True and row.get("submission_started") is True
            or row.get("status") == "failed" and row.get("submission_started") is False and row.get("safe_error") == "execution_cancelled_before_dispatch"
            or row.get("status") == "failed" and row.get("submission_started") is True and row.get("provider_write_reached") is False
                and isinstance(rejection, dict) and type(rejection.get("provider_status")) is int and 400 <= rejection["provider_status"] < 500)
        if not proven:
            raise problem("google_automation_recovery_unproved", "لم تثبت نهاية العملية؛ لا تعِد إرسالها.", 409)
        await _settle_if_present(db, row)
        await management._release_fence(db, row)
        return management.public_proposal(row)
    if not row.get("submission_started"):
        fence = await db[management.FENCES].find_one({"user_id": user_id, "account_id": account_id,
            "proposal_id": proposal_id, "execution_claim_id": row.get("execution_claim_id"), "status": "claimed"}, {"_id": 0})
        if not fence or not row.get("execution_claim_id"):
            raise problem("google_automation_recovery_unproved", "لم تثبت ملكية العملية؛ لا تعِد إرسالها.", 409)
        if fence.get("claimed_at", management.now().isoformat()) > (management.now()-timedelta(minutes=5)).isoformat():
            raise problem("google_ads_reconcile_wait", "انتظر مهلة التحقق قبل تسوية العملية.", 409)
        changed = await db[management.PROPOSALS].update_one({"user_id": user_id, "account_id": account_id,
            "proposal_id": proposal_id, "execution_claim_id": row["execution_claim_id"], "submission_started": False,
            "status": {"$in": ["executing", "approved", "uncertain"]}},
            {"$set": {"status": "failed", "safe_error": "execution_cancelled_before_dispatch", "provider_write_reached": False}})
        if not changed.matched_count:
            raise problem("google_automation_recovery_unproved", "تغيرت حالة الإرسال؛ تحقق مجددًا دون إعادته.", 409)
        row = await management.get_proposal(db, user_id, proposal_id)
        await _settle_if_present(db, row)
        await management._release_fence(db, row)
        return management.public_proposal(row)
    result = await management._reconcile_google_proposal(db, _actor(user_id), proposal_id,
        client_factory=client_factory, automation=True)
    fresh = await management.get_proposal(db, user_id, proposal_id)
    if fresh.get("status") in {"completed", "failed"}:
        await _settle_if_present(db, fresh)
        await management._release_fence(db, fresh)
    return result
