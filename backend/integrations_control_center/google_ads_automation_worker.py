"""Default-off, bounded Google standing-policy cycles for an isolated child.

This module attaches no scheduler and imports no gateway while disabled. Native
reads use a service context without human write permissions. Only the explicit
automation gateway may authorize, submit or reconcile an operation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import uuid
from zoneinfo import ZoneInfo

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .google_ads_automation_policy import AUTOMATION_POLICIES
from .google_ads_autonomy_planner import plan_google_autonomy
from .google_ads_management import ANALYSES, PROPOSALS, FENCES

CYCLES = "mezan_google_ads_automation_worker_cycles_v1"
MAX_ACCOUNTS = 4
INTERVAL = timedelta(hours=5)
RETRY_DELAY = timedelta(minutes=15)
ACCOUNT_TIMEOUT_SECONDS = 240
LEASE_DURATION = timedelta(minutes=10)
PENDING = ("executing", "approved", "submitted", "verifying", "uncertain")
READBACK_PENDING = ("submitted", "verifying", "uncertain")
RECOVERY_FIELDS = {"_id":0, **{key:1 for key in ("user_id","account_id","proposal_id","status",
    "submission_started","execution_claim_id","actor_id","automation_source")}}
_ACTIONS = {"pause":"PAUSE_CAMPAIGN", "enable":"RESUME_CAMPAIGN", "set_budget":"CHANGE_BUDGET"}


def utcnow():
    return datetime.now(timezone.utc)


def worker_enabled():
    values = {"1", "true", "yes", "on", "enabled"}
    return str(os.getenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "false")).strip().lower() in values


def execution_enabled():
    from .google_ads_automation_authority import execution_enabled as allowed
    return allowed()


def _service_actor(user_id):
    from .google_ads_automation_authority import SERVICE_ACTOR_ID
    return {"user_id": user_id, "actor_id": SERVICE_ACTOR_ID, "owner": False, "can_draft": False}


def _scope_ok(user_id, account_id):
    return (isinstance(user_id,str) and 0 < len(user_id) <= 128 and "\x00" not in user_id
        and isinstance(account_id,str) and re.fullmatch(r"[0-9]{1,30}",account_id) is not None)


def _policy_gate(policy, current, *, user_id, account_id):
    from .google_ads_automation_authority import activation_proof_valid
    if not isinstance(policy,dict): return False
    # Older configuration could store enabled=True while no executor existed.
    # Only a new explicit owner activation may authorize the installed worker.
    if not activation_proof_valid(policy,current): return False
    result = plan_google_autonomy(user_id=user_id, account_id=account_id,
        policy=policy, analysis={}, workspace={}, history={}, current=current)
    return result["blockers"] == ["analysis_scope_unproved"]


async def build_google_automation_analysis(db, policy, *, current):
    """Generate policy-limited advice; no model write tools or owner context."""
    from .google_ads_ai import GoogleAIAnalysisInput, analyze_google_campaigns
    from .google_ads_client import problem
    from .google_ads_workspace import read_google_workspace
    if not _policy_gate(policy,current,user_id=policy.get("user_id"),account_id=policy.get("account_id")):
        raise problem("google_automation_activation_required", "يلزم تفعيل صريح جديد للإدارة الآلية من المالك.", 403)
    end = current.astimezone(ZoneInfo(policy["time_zone"])).date()
    request = GoogleAIAnalysisInput(account_id=policy["account_id"],
        date_from=(end-timedelta(days=2)).isoformat(), date_to=end.isoformat())
    facts = await read_google_workspace(db, _service_actor(policy["user_id"]),
        from_date=request.date_from, to_date=request.date_to, account_id=policy["account_id"])
    if (facts.get("complete") is not True or facts.get("account_id") != policy["account_id"]
        or any(facts.get(key) != policy[key] for key in ("currency_code", "time_zone"))):
        raise problem("google_automation_analysis_evidence_unproved", "بيانات حساب جوجل غير مكتملة أو تغيرت.", 409)
    allowed = set(policy["campaign_ids"])
    scoped = [row for row in facts.get("campaigns", []) if row.get("campaign_id") in allowed]
    return await analyze_google_campaigns(db, policy["user_id"], request,
        facts={**facts, "campaigns": scoped, "date_from": request.date_from, "date_to": request.date_to,
            "permitted_actions": sorted(_ACTIONS[value] for value in policy["allowed_actions"]),
            "limits": {"max_daily_budget_native": policy["max_campaign_daily_budget_native"],
                       "max_budget_change_percent": policy["max_budget_change_percent"]}})


async def _claim(db, user_id, account_id, current, *, recovery=False):
    key = hashlib.sha256(f"{user_id}:{account_id}".encode()).hexdigest()
    collection = db[CYCLES]
    try:
        await collection.insert_one({"_id": key, "user_id": user_id, "account_id": account_id,
            "state": "idle", "next_run_at": current, "created_at": current})
    except DuplicateKeyError:
        pass
    due = {} if recovery else {"$or":[{"next_run_at":{"$lte":current}}, {"next_run_at":{"$exists":False}}]}
    token = uuid.uuid4().hex
    return await collection.find_one_and_update({"_id":key, "user_id":user_id, "account_id":account_id,
        "$and":[due, {"$or":[{"state":{"$ne":"running"}}, {"lease_until":{"$lte":current}}]}]},
        {"$set":{"state":"running", "claim_token":token, "started_at":current,
                 "lease_until":current+LEASE_DURATION}}, return_document=ReturnDocument.AFTER)


async def _owns_claim(db, claim, current):
    return await db[CYCLES].find_one({"_id":claim["_id"], "state":"running", "claim_token":claim["claim_token"],
        "lease_until":{"$gt":current}}, {"_id":1}) is not None


async def _history(db, user_id, account_id):
    from .google_ads_automation_plan import HISTORY_PROJECTION
    scope = {"user_id":user_id, "account_id":account_id}
    rows = await db[PROPOSALS].find(scope, HISTORY_PROJECTION).sort("created_at",-1).to_list(length=1001)
    return {**scope, "complete":len(rows)<=1000, "items":rows[:1000]}


async def _stale_unsubmitted(db, user_id, account_id, row, current):
    if row.get("status") not in ("executing","approved","uncertain") or row.get("submission_started") is not False:
        return False
    execution_id=row.get("execution_claim_id")
    if not isinstance(execution_id,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",execution_id): return False
    fence=await db[FENCES].find_one({"user_id":user_id,"account_id":account_id,"proposal_id":row["proposal_id"],
        "execution_claim_id":execution_id,"status":"claimed"}, {"_id":0,"claimed_at":1})
    if not fence: return False
    try:
        stamp=datetime.fromisoformat(str(fence.get("claimed_at")).replace("Z","+00:00"))
        return stamp.tzinfo is not None and current-stamp.astimezone(timezone.utc)>=timedelta(minutes=5)
    except (ValueError,TypeError):
        return False


async def _account_cycle(db, account, claim, *, analysis_builder, inventory_reader, previewer, executor, reconciler, clock, allow_analysis):
    from auth import account_is_disabled
    user_id,account_id = account["user_id"],account["account_id"]
    owner = await db.users.find_one({"id":user_id}, {"_id":0})
    if (not owner or str(owner.get("role") or "").lower() != "owner" or account_is_disabled(owner)
        or owner.get("id") != user_id):
        return {"status":"owner_unproved"}
    scope={"user_id":user_id,"account_id":account_id}
    policy=await db[AUTOMATION_POLICIES].find_one(scope, {"_id":0})
    pending = await db[PROPOSALS].find({"user_id":user_id,"account_id":account_id,
        "automation_source.kind":"standing_policy", "status":{"$in":list(PENDING)}},
        RECOVERY_FIELDS).to_list(length=2)
    if len(pending)>1: return {"status":"pending_ambiguous"}
    row=pending[0] if pending else None
    reservation=(policy or {}).get("_dispatch_claim")
    if reservation is not None:
        # A completed/rejected native result may have been persisted just before
        # a crash prevented private policy-claim settlement. Never start a new
        # analysis while that reservation still owns the account.
        if (not isinstance(reservation,dict) or any(not isinstance(reservation.get(key),str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",reservation[key]) for key in ("claim_id","execution_claim_id","proposal_id"))
            or reservation["claim_id"]!=reservation["execution_claim_id"]
            or any(reservation.get(key) is not None and reservation[key]!=value for key,value in scope.items())):
            return {"status":"dispatch_claim_unproved"}
        bound=await db[PROPOSALS].find_one({**scope,"proposal_id":reservation["proposal_id"],
            "execution_claim_id":reservation["execution_claim_id"]}, RECOVERY_FIELDS)
        if (not bound or bound.get("user_id")!=user_id or bound.get("account_id")!=account_id
            or bound.get("actor_id")!=_service_actor(user_id)["actor_id"]
            or (bound.get("automation_source") or {}).get("kind")!="standing_policy"):
            return {"status":"dispatch_claim_unproved"}
        if row and row.get("proposal_id")!=bound["proposal_id"]: return {"status":"pending_ambiguous"}
        row=bound
    if row:
        readback=row.get("status") in READBACK_PENDING and row.get("submission_started") is True
        terminal=reservation is not None and row.get("status") in ("completed","failed")
        stale=await _stale_unsubmitted(db,user_id,account_id,row,clock()) if not readback and not terminal else False
        if not (readback or terminal or stale):
            return {"status":"pending_dispatch", "proposal_id":row["proposal_id"]}
        if not worker_enabled() or not await _owns_claim(db,claim,clock()): return {"status":"claim_lost"}
        # This gateway reads back the original dispatch; it must never resend it.
        result=await reconciler(db,user_id=user_id,account_id=account_id,proposal_id=row["proposal_id"])
        return {"status":result.get("status") if result.get("status") in ("completed","failed","uncertain","verifying","submitted") else "pending",
            "proposal_id":row["proposal_id"], "reconciled":True}
    if not allow_analysis or not execution_enabled(): return {"status":"execution_disabled"}
    if not policy or not _policy_gate(policy,clock(),user_id=user_id,account_id=account_id):
        return {"status":"policy_disabled"}
    history=await _history(db,user_id,account_id)
    if not history["complete"]:
        return {"status":"history_incomplete"}
    if any(row.get("status") in PENDING for row in history["items"]):
        return {"status":"account_execution_pending"}
    if not execution_enabled(): return {"status":"execution_disabled"}
    if not worker_enabled() or not await _owns_claim(db,claim,clock()): return {"status":"claim_lost"}
    analysis=await analysis_builder(db,policy,current=clock())
    latest=await db[AUTOMATION_POLICIES].find_one({"user_id":user_id,"account_id":account_id}, {"_id":0})
    if not latest or latest.get("revision") != policy["revision"] or not _policy_gate(latest,clock(),user_id=user_id,account_id=account_id):
        return {"status":"policy_changed"}
    if not execution_enabled(): return {"status":"execution_disabled"}
    if not worker_enabled() or not await _owns_claim(db,claim,clock()): return {"status":"claim_lost"}
    if not isinstance(analysis,dict) or analysis.get("provider") != "google_ads" or analysis.get("account_id") != account_id:
        return {"status":"analysis_unproved"}
    period=analysis.get("period") or {}
    if (period.get("account_id") != account_id or any(period.get(k)!=policy[k] for k in ("currency_code","time_zone"))
        or not period.get("date_from") or not period.get("date_to")):
        return {"status":"analysis_unproved"}
    analysis_id=hashlib.sha256(f"{claim['claim_token']}:{user_id}:{account_id}:{policy['revision']}".encode()).hexdigest()
    saved={"analysis_id":analysis_id,"user_id":user_id,"account_id":account_id,
        "actor_id":_service_actor(user_id)["actor_id"],"created_at":clock().isoformat(),
        "date_from":period["date_from"],"date_to":period["date_to"],"result":analysis,
        "source":"google_standing_policy_cycle","policy_revision":policy["revision"]}
    await db[ANALYSES].insert_one(saved)
    inventory=await inventory_reader(db,_service_actor(user_id),account_id=account_id)
    history=await _history(db,user_id,account_id)
    plan=plan_google_autonomy(user_id=user_id,account_id=account_id,policy=latest,
        analysis=saved,workspace=inventory,history=history,current=clock())
    candidates=[row for row in plan["candidates"] if row.get("decision")=="eligible_for_preview"]
    if not candidates:
        return {"status":"no_eligible_action","analysis_id":analysis_id,
            "blockers":plan["blockers"] or sorted({code for row in plan["candidates"] for code in row.get("blockers",[])})}
    if not execution_enabled(): return {"status":"execution_disabled"}
    if not worker_enabled() or not await _owns_claim(db,claim,clock()): return {"status":"claim_lost"}
    candidate=candidates[0]
    proposal=await previewer(db,user_id=user_id,account_id=account_id,policy_revision=policy["revision"],
        analysis_id=analysis_id,recommendation_id=candidate["recommendation_id"])
    if not isinstance(proposal,dict) or not isinstance(proposal.get("proposal_id"),str):
        return {"status":"preview_unproved"}
    if proposal.get("status")=="completed" and proposal.get("verified") is True:
        return {"status":"completed","proposal_id":proposal["proposal_id"],"already_completed":True}
    if proposal.get("status")!="previewed": return {"status":"pending", "proposal_id":proposal["proposal_id"]}
    latest=await db[AUTOMATION_POLICIES].find_one({"user_id":user_id,"account_id":account_id}, {"_id":0})
    if not latest or latest.get("revision") != policy["revision"] or not _policy_gate(latest,clock(),user_id=user_id,account_id=account_id):
        return {"status":"policy_changed","proposal_id":proposal["proposal_id"]}
    if not execution_enabled(): return {"status":"execution_disabled","proposal_id":proposal["proposal_id"]}
    if not worker_enabled() or not await _owns_claim(db,claim,clock()): return {"status":"claim_lost"}
    # The gateway re-reads policy and owner at dispatch, binds native evidence,
    # reserves the durable claim and preserves the original submission floor.
    result=await executor(db,user_id=user_id,account_id=account_id,proposal_id=proposal["proposal_id"])
    return {"status":result.get("status") if result.get("status") in ("completed","failed","submitted","verifying","uncertain") else "pending",
        "proposal_id":proposal["proposal_id"]}


async def run_google_automation_cycle(db, *, analysis_builder=None, inventory_reader=None,
        previewer=None, executor=None, reconciler=None, clock=utcnow):
    """Run at most four accounts and one prospective dispatch per account.

    A child-process scheduler is deliberately outside this module. Production
    defaults import only the dedicated automation gateway, never human execute.
    """
    if not worker_enabled(): return {"status":"disabled","accounts":0,"results":[]}
    allow_analysis=execution_enabled()
    from .google_ads_automation_plan import read_google_automation_inventory
    if reconciler is None:
        from .google_ads_automation_gateway import reconcile_google_automation_proposal
        reconciler=reconcile_google_automation_proposal
    if allow_analysis and (previewer is None or executor is None):
        from .google_ads_automation_gateway import preview_google_automation_recommendation,execute_google_automation_proposal
        previewer=previewer or preview_google_automation_recommendation
        executor=executor or execute_google_automation_proposal
    analysis_builder=analysis_builder or build_google_automation_analysis
    inventory_reader=inventory_reader or read_google_automation_inventory
    current=clock()
    from .google_ads_automation_authority import ACTIVATION_VERSION,activation_proof_valid
    policies=(await db[AUTOMATION_POLICIES].find({"enabled":True,"activation_version":ACTIVATION_VERSION,
        "$expr":{"$eq":["$activation_revision","$revision"]},
        "activated_by":{"$exists":True},"activated_at":{"$exists":True},
        "expires_at":{"$gt":current.isoformat()}}, {"_id":0}).to_list(length=MAX_ACCOUNTS+1)) if allow_analysis else []
    policies=[policy for policy in policies if activation_proof_valid(policy,current)]
    pending=await db[PROPOSALS].find({"automation_source.kind":"standing_policy","status":{"$in":list(PENDING)}},
        {"_id":0,"user_id":1,"account_id":1}).to_list(length=MAX_ACCOUNTS+1)
    unsettled=await db[AUTOMATION_POLICIES].find({"_dispatch_claim":{"$exists":True,"$ne":None}},
        {"_id":0,"user_id":1,"account_id":1}).to_list(length=MAX_ACCOUNTS+1)
    accounts={}
    for row in [*unsettled,*pending,*policies]:
        if _scope_ok(row.get("user_id"),row.get("account_id")):
            accounts.setdefault((row["user_id"],row["account_id"]),row)
    recovery_scopes={(row.get("user_id"),row.get("account_id")) for row in [*unsettled,*pending] if _scope_ok(row.get("user_id"),row.get("account_id"))}
    results=[]
    for (user_id,account_id),account in list(accounts.items())[:MAX_ACCOUNTS]:
        recovery=(user_id,account_id) in recovery_scopes
        claim=await _claim(db,user_id,account_id,clock(),recovery=recovery)
        result={"status":"not_due"}
        if claim:
            try:
                result=await asyncio.wait_for(_account_cycle(db,account,claim,analysis_builder=analysis_builder,
                    inventory_reader=inventory_reader,previewer=previewer,executor=executor,reconciler=reconciler,clock=clock,allow_analysis=allow_analysis),timeout=ACCOUNT_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code=(exc.detail or {}).get("code") if isinstance(getattr(exc,"detail",None),dict) else None
                result={"status":"failed","code":code if isinstance(code,str) and re.fullmatch(r"[a-z0-9_]{1,100}",code) else "google_automation_cycle_failed"}
            finally:
                finished=clock()
                next_run=finished+(RETRY_DELAY if result["status"]=="failed" else INTERVAL)
                previous=claim.get("next_run_at")
                # Motor's default codec decodes BSON UTC datetimes as naive.
                # Treat that decoded value as UTC, never as local wall time.
                if isinstance(previous,datetime):
                    previous=previous.replace(tzinfo=previous.tzinfo or timezone.utc).astimezone(timezone.utc)
                if recovery and isinstance(previous,datetime) and previous>finished:
                    next_run=previous
                await db[CYCLES].update_one({"_id":claim["_id"],"state":"running","claim_token":claim["claim_token"]},
                    {"$set":{"state":"idle","finished_at":finished,"next_run_at":next_run,"last_status":result["status"]}})
        results.append({"account_id":account_id,**result})
    return {"status":"complete" if allow_analysis else "recovery_only","accounts":len(results),"deferred":max(0,len(accounts)-MAX_ACCOUNTS),"results":results}
