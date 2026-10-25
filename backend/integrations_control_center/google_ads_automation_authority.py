"""Internal, default-off standing-policy authorization; no provider dispatch.

Only an explicit future owner activation may persist activation_version=1;
legacy enabled policies never gain execution rights when the worker is installed.
A successful claim is ONE acknowledgement to the current execution owner. It is
not replayable permission and cannot replace the native proposal's durable
submission floor. Revocation and claim linearize on the same policy document:
revocation first denies the claim; claim first may finish while revocation blocks
subsequent claims. Unknown claims never expire or refund themselves.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import re
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .google_ads_automation_policy import AUTOMATION_POLICIES
from .google_ads_autonomy_planner import plan_google_autonomy
from .google_ads_campaign_builder import GoogleCampaignBuildError, build_update_operations
from .google_ads_client import identifier, problem
from .google_ads_management import ANALYSES, FENCES, PROPOSALS, available_accounts, digest, human_snapshot, immutable

SERVICE_ACTOR_ID = "mezan_google_automation_v1"
ACTIVATION_VERSION = 1
POLICY_BINDING_FIELDS = ("schema_version", "user_id", "account_id", "revision", "enabled", "activation_version", "activation_revision", "activated_by", "activated_at", "currency_code", "time_zone",
    "campaign_ids", "allowed_actions", "max_campaign_daily_budget_native", "max_account_daily_budget_native",
    "max_budget_change_percent", "cooldown_seconds", "max_actions_per_day", "expires_at", "updated_by", "updated_at")
SOURCE_FIELDS = {"kind", "policy_revision", "policy_digest", "analysis_id", "recommendation_id"}
HISTORY_FIELDS = ("user_id", "account_id", "proposal_id", "execution_claim_id", "campaign_id", "created_campaign_id",
    "action", "status", "verified", "submission_started", "created_at", "submitted_at", "executed_at")
MAX_HISTORY = 1000
MAX_DAY_BUCKETS = 32
NATIVE_PROOF_AGE = timedelta(seconds=90)
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def execution_enabled():
    return os.getenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _now(current=None):
    value = current if current is not None else datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise problem("google_automation_clock_unproved", "تعذر إثبات وقت تفويض جوجل.", 409)
    return value.astimezone(timezone.utc)


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def activation_proof_valid(policy, current=None):
    """A legacy enabled setting is never an explicit current owner activation."""
    if (not isinstance(policy, dict) or not isinstance(policy.get("user_id"), str) or not policy["user_id"]
        or type(policy.get("activation_version")) is not int or policy["activation_version"] != ACTIVATION_VERSION
        or type(policy.get("revision")) is not int or policy["revision"] < 1
        or type(policy.get("activation_revision")) is not int or policy["activation_revision"] != policy["revision"]
        or policy.get("activated_by") != policy["user_id"]):
        return False
    stamp = _instant(policy.get("activated_at"))
    comparison = _now() if current is None else current
    if not isinstance(comparison, datetime) or comparison.tzinfo is None:
        return False
    return stamp is not None and stamp <= comparison.astimezone(timezone.utc)


def _fail(code):
    raise problem(code, "تعذر إثبات تفويض تنفيذ جوجل؛ لم يُمنح إذن إرسال جديد.", 409)


def policy_authorization_digest(policy):
    """Exclude operational claims/counters and audit events from frozen policy."""
    return digest({key: policy.get(key) for key in POLICY_BINDING_FIELDS})


def make_automation_source(policy, *, analysis_id, recommendation_id):
    """Bind a server-owned preview to one saved policy revision and advice item."""
    if (not isinstance(policy, dict) or type(policy.get("revision")) is not int or policy["revision"] < 1
        or not isinstance(analysis_id, str) or not _ID.fullmatch(analysis_id)
        or not isinstance(recommendation_id, str) or not recommendation_id or len(recommendation_id) > 240):
        _fail("google_automation_proposal_binding_invalid")
    return {"kind": "standing_policy", "policy_revision": policy["revision"],
        "policy_digest": policy_authorization_digest(policy), "analysis_id": analysis_id, "recommendation_id": recommendation_id}


def automation_confirmation_digest(row):
    # Compatible when management.immutable later includes this optional field.
    # A manual digest does not become automated approval by attaching a source.
    return digest({**immutable(row), "automation_source": row.get("automation_source")})


def _bound_proposal(row):
    source = row.get("automation_source")
    if (row.get("actor_id") != SERVICE_ACTOR_ID or row.get("provider") != "google_ads"
        or row.get("action") not in ("pause", "enable", "set_budget")
        or not isinstance(source, dict) or set(source) != SOURCE_FIELDS or source.get("kind") != "standing_policy"
        or type(source.get("policy_revision")) is not int or source["policy_revision"] < 1
        or not isinstance(source.get("policy_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", source["policy_digest"])
        or not isinstance(source.get("analysis_id"), str) or not _ID.fullmatch(source["analysis_id"])
        or not isinstance(source.get("recommendation_id"), str) or not source["recommendation_id"]
        or automation_confirmation_digest(row) != row.get("confirmation_digest")):
        _fail("google_automation_proposal_binding_invalid")
    return source


async def _linearize_claim(collection, query, update):
    """Only this acknowledged same-document CAS grants a new local claim."""
    return await collection.update_one(query, update)


def _day_buckets(policy, today, zone):
    buckets = policy.get("_dispatch_days", [])
    if not isinstance(buckets, list) or len(buckets) > MAX_DAY_BUCKETS:
        _fail("google_automation_quota_unproved")
    dates = set()
    copied, current_ids = [], set()
    for bucket in buckets:
        if not isinstance(bucket, dict) or set(bucket) != {"date", "time_zone", "proposal_ids"}:
            _fail("google_automation_quota_unproved")
        try:
            day = date.fromisoformat(bucket["date"])
        except (ValueError, TypeError):
            _fail("google_automation_quota_unproved")
        ids = bucket["proposal_ids"]
        if (day.isoformat() != bucket["date"] or day > today or day in dates
            or not isinstance(bucket["time_zone"], str) or not isinstance(ids, list) or len(ids) > 20
            or any(not isinstance(pid, str) or not _ID.fullmatch(pid) for pid in ids) or len(set(ids)) != len(ids)):
            _fail("google_automation_quota_unproved")
        dates.add(day)
        if day == today:
            if bucket["time_zone"] != zone:
                _fail("google_automation_quota_unproved")
            current_ids.update(ids)
        else:
            copied.append({"date": bucket["date"], "time_zone": bucket["time_zone"], "proposal_ids": list(ids)})
    return copied, current_ids


def _daily_history_ids(rows, today, zone):
    ids = set()
    for row in rows:
        if row.get("status") != "completed" and row.get("submission_started") is not True:
            continue
        stamp = _instant(row.get("submitted_at") or row.get("executed_at") or row.get("created_at"))
        pid = row.get("proposal_id")
        if stamp is None or not isinstance(pid, str) or not _ID.fullmatch(pid):
            _fail("google_automation_history_unproved")
        if stamp.astimezone(zone).date() == today:
            ids.add(pid)
    return ids


def _native_proof(row, evidence, policy, current):
    if not isinstance(evidence, dict):
        _fail("google_automation_native_evidence_invalid")
    metadata, snapshot, inventory = (evidence.get(k) for k in ("metadata", "campaign_snapshot", "inventory"))
    stamp = _instant(evidence.get("observed_at"))
    if (not isinstance(metadata, dict) or not isinstance(snapshot, dict) or not isinstance(inventory, dict)
        or stamp is None or not timedelta(0) <= current - stamp < NATIVE_PROOF_AGE
        or metadata.get("identity_verified") is not True or metadata.get("account_id") != row["account_id"]
        or evidence.get("auth_epoch") != row.get("auth_epoch") or not row.get("auth_epoch")
        or metadata.get("currency_code") != row.get("currency") or metadata.get("time_zone") != row.get("time_zone")
        or metadata.get("currency_code") != policy.get("currency_code") or metadata.get("time_zone") != policy.get("time_zone")
        or digest(snapshot) != row.get("before_digest")):
        _fail("google_automation_native_evidence_invalid")
    # Snapshot and complete inventory must describe the same target settings.
    target = next((campaign for campaign in inventory.get("campaigns", [])
        if isinstance(campaign, dict) and campaign.get("campaign_id") == row.get("campaign_id")), None)
    human = human_snapshot(snapshot)
    try:
        same = (target and human["campaign_id"] == row.get("campaign_id")
            and human["status"] == target.get("status") and human["budget_mode"] == target.get("budget_mode")
            and Decimal(human["budget_native"]) == Decimal(target.get("budget_native")))
    except (TypeError, ValueError, ArithmeticError):
        same = False
    if not same:
        _fail("google_automation_native_evidence_invalid")
    return inventory, target


def _budget_reservation(inventory, target, candidate):
    previous = Decimal(target["budget_native"])
    proposed = Decimal(candidate["changes"]["budget_native"]) if candidate["native_action"] == "set_budget" else previous
    increase = proposed if candidate["native_action"] == "enable" else max(proposed - previous, Decimal(0)) if candidate["native_action"] == "set_budget" else Decimal(0)
    total = Decimal(0)
    for campaign in inventory["campaigns"]:
        if campaign.get("status") not in ("ENABLED", "PAUSED", "REMOVED"):
            total = None
            break
        if campaign.get("status") == "ENABLED":
            try:
                if campaign.get("budget_mode") != "DAILY": raise ValueError()
                value = Decimal(campaign["budget_native"])
                if not value.is_finite() or value <= 0: raise ValueError()
                total += value
            except (KeyError, TypeError, ValueError, ArithmeticError):
                total = None
                break
    # Values are evidence/reservations, never actual hard daily spending caps.
    return {"campaign_budget_before_native": str(previous), "campaign_budget_target_native": str(proposed),
        "account_budget_before_native": str(total) if total is not None else None,
        "budget_increase_reserved_native": str(increase), "budget_semantics": "average_daily_budget"}


async def _require_active_owner(db, user_id):
    from auth import account_is_disabled
    owner = await db.users.find_one({"id": user_id}, {"_id": 0, "id": 1, "role": 1,
        "disabled": 1, "is_active": 1, "deleted_at": 1})
    if not owner or str(owner.get("role") or "").lower() != "owner" or account_is_disabled(owner):
        _fail("google_automation_owner_unavailable")


async def authorize_google_automation_claim(db, *, user_id, account_id, proposal_id, execution_claim_id,
        native_evidence, current=None):
    """Reserve one standing-policy claim for the exact current native executor.

    Internal API only: native_evidence is produced by the gateway's verified
    client reads, never HTTP/model input. The gateway must freeze automation_source
    in its confirmation digest and claim the normal account/proposal fences first.
    Success does NOT submit or transition the proposal. An acknowledged native
    submission floor is still mandatory. EVERY failure requires retaining the
    execution fence: a prior invocation may already own a committed claim.
    Recovery must terminally CAS-cancel the exact unsubmitted proposal, settle
    any matching policy claim from that read proof, and only then release the
    account fence. Read uncertainty or a submitted floor keeps it held. No
    exception authorizes an automatic resend; no resume/retry API exists here.
    """
    if not execution_enabled():
        raise problem("google_automation_execution_disabled", "التنفيذ الآلي لجوجل غير مفعّل.", 503,
            authorization_cas_attempted=False, preserve_execution_fence=True, automatic_retry_allowed=False)
    phase = {"cas_attempted": False}
    try:
        return await asyncio.wait_for(_authorize(db, user_id=user_id, account_id=account_id,
            proposal_id=proposal_id, execution_claim_id=execution_claim_id, native_evidence=native_evidence,
            current=current, phase=phase), timeout=15)
    except asyncio.CancelledError as exc:
        # Cancellation can arrive after Mongo committed but before its reply.
        # The gateway must retain its fence once this call has begun, including
        # when no HTTPException/detail could be returned to classify the phase.
        exc.authorization_cas_attempted = phase["cas_attempted"]
        exc.preserve_execution_fence = True
        exc.automatic_retry_allowed = False
        raise
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            # Even a pre-CAS failure can be a retry of an earlier invocation
            # whose claim committed. No error proves that the claim is absent.
            exc.detail.update(authorization_cas_attempted=phase["cas_attempted"],
                preserve_execution_fence=True, automatic_retry_allowed=False)
        raise
    except Exception:
        # Includes timeout and an acknowledgement lost after Mongo committed.
        # Never let the manual pre-submit cleanup restore/release this owner.
        raise problem("google_automation_authorization_unconfirmed", "تعذر تأكيد تفويض الإرسال؛ تحقق من العملية دون إعادة إرسالها.", 503,
            authorization_cas_attempted=phase["cas_attempted"], preserve_execution_fence=True,
            automatic_retry_allowed=False) from None


async def _authorize(db, *, user_id, account_id, proposal_id, execution_claim_id, native_evidence, current, phase):
    current_time = _now(current)
    account_id = identifier(account_id)
    if (not isinstance(user_id, str) or not user_id or not isinstance(proposal_id, str) or not _ID.fullmatch(proposal_id)
        or not isinstance(execution_claim_id, str) or not _ID.fullmatch(execution_claim_id)):
        _fail("google_automation_execution_claim_unproved")
    await _require_active_owner(db, user_id)
    connection = await db.mezan_integrations_v2.find_one({"user_id": user_id, "provider": "google_ads",
        "connection_status": "connected", "connection_provenance": "api_connection"}, {"_id": 0, "provider": 1})
    if not connection or account_id not in {a["account_id"] for a in await available_accounts(db, user_id)}:
        _fail("google_automation_account_unavailable")
    scope = {"user_id": user_id, "account_id": account_id}
    row_scope = {**scope, "proposal_id": proposal_id, "execution_claim_id": execution_claim_id,
        "status": "approved", "submission_started": False}
    row = await db[PROPOSALS].find_one(row_scope, {"_id": 0})
    fence_scope = {**scope, "proposal_id": proposal_id, "execution_claim_id": execution_claim_id, "status": "claimed"}
    if not row or not await db[FENCES].find_one(fence_scope, {"_id": 0, "proposal_id": 1}):
        _fail("google_automation_execution_claim_unproved")
    source = _bound_proposal(row)
    policy = await db[AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
    if (not policy or policy.get("enabled") is not True or policy.get("updated_by") != user_id
        or policy.get("revision") != source["policy_revision"]
        or policy_authorization_digest(policy) != source["policy_digest"]):
        _fail("google_automation_policy_changed")
    # Policies saved while no executor was installed are configuration only.
    # Installing/enabling a worker must never activate those legacy rows.
    if not activation_proof_valid(policy, current_time):
        _fail("google_automation_activation_required")
    if policy.get("_dispatch_claim") is not None:
        _fail("google_automation_claim_pending")
    expiry, proposal_expiry = _instant(policy.get("expires_at")), _instant(row.get("expires_at"))
    if expiry is None or proposal_expiry is None or min(expiry, proposal_expiry) <= current_time:
        _fail("google_automation_policy_changed")
    inventory, target = _native_proof(row, native_evidence, policy, current_time)
    analysis = await db[ANALYSES].find_one({**scope, "analysis_id": source["analysis_id"]}, {"_id": 0})
    if not analysis:
        _fail("google_automation_analysis_unproved")
    projection = {"_id": 0, **{key: 1 for key in HISTORY_FIELDS}}
    rows = await db[PROPOSALS].find(scope, projection).sort("created_at", -1).to_list(length=MAX_HISTORY + 1)
    if len(rows) > MAX_HISTORY:
        _fail("google_automation_history_unproved")
    rows = [item for item in rows if not (item.get("proposal_id") == proposal_id
        and item.get("execution_claim_id") == execution_claim_id)]
    # Finish all local reads/index work before the final owner, time, evidence
    # and quota checks. No further read/audit await separates them from the CAS.
    await db[AUTOMATION_POLICIES].create_index([("user_id", 1), ("account_id", 1)], unique=True, name="google_automation_tenant_account")
    if not await db[PROPOSALS].find_one(row_scope, {"_id": 0, "proposal_id": 1}) or not await db[FENCES].find_one(fence_scope, {"_id": 0, "proposal_id": 1}):
        _fail("google_automation_execution_claim_unproved")
    await _require_active_owner(db, user_id)
    cas_time = current_time = _now(current)
    if min(expiry, proposal_expiry) <= cas_time:
        _fail("google_automation_policy_changed")
    if not activation_proof_valid(policy, cas_time):
        _fail("google_automation_activation_required")
    inventory, target = _native_proof(row, native_evidence, policy, cas_time)
    if not execution_enabled():
        raise problem("google_automation_execution_disabled", "التنفيذ الآلي لجوجل غير مفعّل.", 503)
    # Ignore only the exact currently proved self-claim, never other pending work.
    plan = plan_google_autonomy(user_id=user_id, account_id=account_id, policy=policy, analysis=analysis,
        workspace=inventory, history={**scope, "complete": True, "items": rows}, current=current_time)
    candidates = [c for c in plan["candidates"] if c.get("recommendation_id") == source["recommendation_id"]]
    if plan["blockers"] or len(candidates) != 1 or candidates[0].get("decision") != "eligible_for_preview":
        _fail("google_automation_plan_not_eligible")
    candidate = candidates[0]
    if (candidate["campaign_id"] != row.get("campaign_id") or candidate["native_action"] != row.get("action")
        or row.get("planned") != {"action": candidate["native_action"], **candidate["changes"]}
        or row.get("idempotency_key") != candidate["idempotency_key"]):
        _fail("google_automation_proposal_binding_invalid")
    try:
        operations = build_update_operations(candidate["native_action"], account_id,
            native_evidence["campaign_snapshot"], candidate["changes"])
    except GoogleCampaignBuildError:
        _fail("google_automation_native_evidence_invalid")
    if digest(operations) != digest(row.get("operations")):
        _fail("google_automation_proposal_binding_invalid")
    zone = ZoneInfo(policy["time_zone"])
    today = current_time.astimezone(zone).date()
    other_days, consumed = _day_buckets(policy, today, policy["time_zone"])
    consumed |= _daily_history_ids(rows, today, zone)
    consumed.add(proposal_id)
    if len(consumed) > policy["max_actions_per_day"]:
        _fail("google_automation_daily_action_limit")
    days = sorted([*other_days, {"date": today.isoformat(), "time_zone": policy["time_zone"], "proposal_ids": sorted(consumed)}], key=lambda item: item["date"])[-MAX_DAY_BUCKETS:]
    generation = policy.get("_dispatch_generation", 0)
    if type(generation) is not int or not 0 <= generation < 2**63 - 2:
        _fail("google_automation_quota_unproved")
    # A cancellation after these reads still has to defeat the separate native
    # submission-floor CAS. Authorization alone never permits provider dispatch.
    claim = {"claim_id": execution_claim_id, "proposal_id": proposal_id, "execution_claim_id": execution_claim_id,
        "policy_revision": source["policy_revision"], "policy_digest": source["policy_digest"],
        "confirmation_digest": row["confirmation_digest"], "operations_digest": digest(operations),
        "analysis_id": source["analysis_id"], "recommendation_id": source["recommendation_id"],
        "campaign_id": row["campaign_id"], "action": row["action"], "authorized_at": cas_time.isoformat(),
        "account_local_date": today.isoformat(), "time_zone": policy["time_zone"],
        "native_inventory_digest": digest(inventory), **_budget_reservation(inventory, target, candidate)}
    query = {**scope, **{key: policy.get(key) for key in POLICY_BINDING_FIELDS if key not in scope},
        "enabled": True, "expires_at": {"$eq": policy["expires_at"], "$gt": cas_time.isoformat()},
        "_dispatch_claim": {"$in": [None]},
        "_dispatch_generation": generation if "_dispatch_generation" in policy else {"$exists": False},
        "_dispatch_days": policy["_dispatch_days"] if "_dispatch_days" in policy else {"$exists": False}}
    phase["cas_attempted"] = True
    changed = await _linearize_claim(db[AUTOMATION_POLICIES], query,
        {"$set": {"_dispatch_claim": claim, "_dispatch_days": days}, "$inc": {"_dispatch_generation": 1}})
    if not changed.matched_count:
        _fail("google_automation_policy_claim_changed")
    if not execution_enabled():
        # Preserve the reservation/claim for explicit recovery; do not hand the
        # caller fresh permission after a global stop raced the acknowledgement.
        raise problem("google_automation_execution_disabled", "التنفيذ الآلي لجوجل غير مفعّل.", 503)
    return {"authorized": True, "authorization_kind": "standing_policy", "claim_id": execution_claim_id,
        "proposal_id": proposal_id, "policy_revision": source["policy_revision"], "submission_floor_required": True,
        "provider_writes": 0}


async def settle_google_automation_claim(db, *, user_id, account_id, claim_id, current=None):
    """Clear a claim only after reading a provably terminal native proposal.

    This remains available with automation disabled or policy revoked. It never
    resumes an execution and never refunds its daily slot. There is no TTL or
    caller-supplied outcome. The native gateway/reconciler owns provider reads.
    """
    try:
        return await asyncio.wait_for(_settle(db, user_id=user_id, account_id=account_id,
            claim_id=claim_id, current=current), timeout=10)
    except HTTPException:
        raise
    except Exception:
        raise problem("google_automation_settlement_unconfirmed", "تعذر تأكيد تسوية تفويض جوجل؛ لا تعِد إرسال العملية.", 503) from None


async def _settle(db, *, user_id, account_id, claim_id, current):
    current_time = _now(current)
    account_id = identifier(account_id)
    scope = {"user_id": user_id, "account_id": account_id}
    policy = await db[AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
    claim = (policy or {}).get("_dispatch_claim")
    if not isinstance(claim, dict) or claim.get("claim_id") != claim_id:
        _fail("google_automation_settlement_unproved")
    row = await db[PROPOSALS].find_one({**scope, "proposal_id": claim.get("proposal_id"),
        "execution_claim_id": claim.get("execution_claim_id")}, {"_id": 0})
    if not row:
        _fail("google_automation_settlement_unproved")
    source = _bound_proposal(row)
    if (row.get("confirmation_digest") != claim.get("confirmation_digest") or digest(row.get("operations")) != claim.get("operations_digest")
        or source["policy_revision"] != claim.get("policy_revision") or source["policy_digest"] != claim.get("policy_digest")):
        _fail("google_automation_settlement_unproved")
    completed = row.get("status") == "completed" and row.get("verified") is True and row.get("submission_started") is True
    cancelled = row.get("status") == "failed" and row.get("submission_started") is False and row.get("safe_error") == "execution_cancelled_before_dispatch"
    rejection = row.get("rejection") or {}
    rejected = (row.get("status") == "failed" and row.get("submission_started") is True and row.get("provider_write_reached") is False
        and isinstance(rejection, dict) and type(rejection.get("provider_status")) is int and 400 <= rejection["provider_status"] < 500)
    if not (completed or cancelled or rejected):
        _fail("google_automation_settlement_unproved")
    generation = policy.get("_dispatch_generation")
    if type(generation) is not int or not 0 <= generation < 2**63 - 1:
        _fail("google_automation_settlement_unproved")
    changed = await db[AUTOMATION_POLICIES].update_one({**scope, "_dispatch_claim": claim, "_dispatch_generation": generation},
        {"$set": {"_dispatch_claim": None, "_dispatch_last_settlement": {"claim_id": claim_id,
            "status": "completed" if completed else "cancelled_before_dispatch" if cancelled else "provider_rejected",
            "observed_at": current_time.isoformat()}}, "$inc": {"_dispatch_generation": 1}})
    if not changed.matched_count:
        _fail("google_automation_settlement_unproved")
    return {"released": True, "claim_id": claim_id, "provider_writes": 0, "quota_refunded": False}
