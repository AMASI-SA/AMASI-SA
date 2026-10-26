"""Bounded default-off PAUSED creation cycles in the existing isolated child.

The account mutex is shared with management; creation has its own due timestamp.
Only the creation gateway can reserve native authority or submit an operation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

CYCLES = "mezan_google_ads_automation_worker_cycles_v1"
POLICIES = "mezan_google_ads_automation_creation_policies_v1"
PROPOSALS = "mezan_google_ads_proposals_v1"
FENCES = "mezan_google_ads_execution_fences_v1"
PENDING = ("executing", "approved", "submitted", "verifying", "uncertain")
MAX_ACCOUNTS = 4
ACCOUNT_TIMEOUT_SECONDS = 240
LEASE_DURATION = timedelta(minutes=10)
INTERVAL = timedelta(hours=5)
RETRY_DELAY = timedelta(minutes=15)
_ID = r"[A-Za-z0-9_-]{1,128}"
FIELDS = {"_id": 0, **{key: 1 for key in ("user_id", "account_id", "proposal_id", "status",
    "action", "submission_started", "execution_claim_id", "actor_id", "automation_source")}}


def utcnow():
    return datetime.now(timezone.utc)


def worker_enabled():
    return os.getenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def creation_execution_enabled():
    # Keep exactly the gateway's semantic contract, including base WORKER=enabled.
    if not worker_enabled(): return False
    if os.getenv("GOOGLE_ADS_AUTOMATION_CREATION_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}: return False
    from .google_ads_automation_authority import execution_enabled
    return execution_enabled()


def _scope_ok(user_id, account_id):
    return (isinstance(user_id, str) and 0 < len(user_id) <= 128 and "\x00" not in user_id
        and isinstance(account_id, str) and re.fullmatch(r"[0-9]{1,20}", account_id) is not None)


def _instant(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError, TypeError): return None


def _policy_gate(policy, current, scope):
    from .google_ads_automation_authority import activation_proof_valid
    from .google_ads_automation_creation_contract import GoogleAutomationCreationPolicy
    if (not isinstance(policy, dict) or any(policy.get(k) != v for k, v in scope.items())
        or policy.get("enabled") is not True or policy.get("auto_enable_new") is not False or policy.get("configuration_blockers")
        or not activation_proof_valid(policy, current)): return False
    try:
        GoogleAutomationCreationPolicy.model_validate(_snapshot(policy))
    except (ValueError, TypeError): return False
    expiry = _instant(policy.get("expires_at"))
    return expiry is not None and current < expiry <= current + timedelta(days=30)


def _snapshot(row):
    from .google_ads_automation_creation_contract import GoogleAutomationCreationPolicy
    return {key: row[key] for key in GoogleAutomationCreationPolicy.model_fields if key in row}


async def _claim(db, scope, current, recovery):
    key = hashlib.sha256(f"{scope['user_id']}:{scope['account_id']}".encode()).hexdigest()
    try:
        await db[CYCLES].insert_one({"_id": key, **scope, "state": "idle", "created_at": current,
            "creation_next_run_at": current})
    except DuplicateKeyError: pass
    due = {} if recovery else {"$or": [{"creation_next_run_at": {"$lte": current}}, {"creation_next_run_at": {"$exists": False}}]}
    return await db[CYCLES].find_one_and_update({"_id": key, **scope, "$and": [due,
        {"$or": [{"state": {"$ne": "running"}}, {"lease_until": {"$lte": current}}]}]},
        {"$set": {"state": "running", "claim_token": uuid.uuid4().hex, "started_at": current,
            "lease_until": current + LEASE_DURATION}}, return_document=ReturnDocument.AFTER)


async def _owns_claim(db, claim, current):
    return await db[CYCLES].find_one({"_id": claim["_id"], "state": "running",
        "claim_token": claim["claim_token"], "lease_until": {"$gt": current}}, {"_id": 1}) is not None


async def _release(db, claim, result, finished, recovery):
    due = finished + (RETRY_DELAY if result["status"] == "failed" else INTERVAL)
    prior = _instant(claim.get("creation_next_run_at"))
    if recovery and prior and prior > finished: due = prior
    await db[CYCLES].update_one({"_id": claim["_id"], "state": "running", "claim_token": claim["claim_token"]},
        {"$set": {"state": "idle", "finished_at": finished, "creation_next_run_at": due,
            "last_creation_status": result["status"]}})


def _owned(row, scope):
    from .google_ads_automation_authority import SERVICE_ACTOR_ID
    return (isinstance(row, dict) and all(row.get(k) == v for k, v in scope.items())
        and row.get("actor_id") == SERVICE_ACTOR_ID and row.get("action") == "create_campaign"
        and isinstance(row.get("proposal_id"), str) and re.fullmatch(_ID, row["proposal_id"]) is not None
        and isinstance(row.get("automation_source"), dict) and row["automation_source"].get("kind") == "standing_creation")


async def _stale(db, scope, row, current):
    execution = row.get("execution_claim_id")
    if (row.get("status") not in ("executing", "approved", "uncertain") or row.get("submission_started") is not False
        or not isinstance(execution, str) or re.fullmatch(_ID, execution) is None): return False
    fence = await db[FENCES].find_one({**scope, "proposal_id": row["proposal_id"], "execution_claim_id": execution,
        "status": "claimed"}, {"claimed_at": 1})
    started = _instant((fence or {}).get("claimed_at"))
    return started is not None and current - started >= timedelta(minutes=5)


def _choice(plan, policy, scope):
    from .google_ads_automation_creation_contract import GoogleCreationChoice
    if (not isinstance(plan, dict) or plan.get("provider") != "google_ads" or plan.get("account_id") != scope["account_id"]
        or type(plan.get("provider_writes")) is not int or plan["provider_writes"] != 0
        or plan.get("executable") is not False or plan.get("initial_status") != "PAUSED"
        or plan.get("ready_for_paused_preview") is not True or plan.get("blockers")): return None
    try: choice = GoogleCreationChoice.model_validate(plan.get("choice")).model_dump()
    except (ValueError, TypeError): return None
    intent = plan.get("intent")
    if not isinstance(intent, dict): return None
    binding = intent.get("binding") or {}
    if (choice["product_id"] not in {p["product_id"] for p in policy["product_scopes"]}
        or choice["template_id"] != policy["template_id"] or choice["template_version"] != policy["template_version"]
        or binding.get("identity") != {**scope, **choice, "owner_generation": policy["owner_generation"]}
        or type(binding.get("policy_revision")) is not int or binding["policy_revision"] != policy["revision"]
        or intent.get("initial_status") != "PAUSED" or intent.get("auto_enable_new") is not False
        or not isinstance(intent.get("creation_identity"), str)
        or re.fullmatch(r"google_create_[a-f0-9]{64}", intent["creation_identity"]) is None): return None
    return choice


async def _account_cycle(db, scope, claim, *, plan_builder, previewer, executor, reconciler, clock, allow_creation):
    from auth import account_is_disabled
    owner = await db.users.find_one({"id": scope["user_id"]}, {"_id": 0})
    if not owner or owner.get("id") != scope["user_id"] or str(owner.get("role", "")).lower() != "owner" or account_is_disabled(owner):
        return {"status": "owner_unproved"}
    policy = await db[POLICIES].find_one(scope, {"_id": 0})
    pending = await db[PROPOSALS].find({**scope, "automation_source.kind": "standing_creation",
        "status": {"$in": list(PENDING)}}, FIELDS).to_list(length=2)
    if len(pending) > 1: return {"status": "pending_ambiguous"}
    row = pending[0] if pending else None
    reservation = (policy or {}).get("_creation_claim")
    if reservation is not None:
        if (not isinstance(reservation, dict) or any(not isinstance(reservation.get(k), str) or re.fullmatch(_ID, reservation[k]) is None
            for k in ("proposal_id", "execution_claim_id", "claim_id")) or reservation["claim_id"] != reservation["execution_claim_id"]):
            return {"status": "creation_claim_unproved"}
        bound = await db[PROPOSALS].find_one({**scope, "proposal_id": reservation["proposal_id"],
            "execution_claim_id": reservation["execution_claim_id"]}, FIELDS)
        if (not _owned(bound, scope)
            or any(reservation.get(k) is not None and reservation[k] != v for k, v in scope.items())
            or reservation.get("creation_identity") is not None and reservation["creation_identity"] != bound["automation_source"].get("creation_identity")):
            return {"status": "creation_claim_unproved"}
        if row and row["proposal_id"] != bound["proposal_id"]: return {"status": "pending_ambiguous"}
        row = bound
    if row:
        if not _owned(row, scope): return {"status": "pending_unproved"}
        recoverable = (row.get("status") in ("submitted", "verifying", "uncertain") and row.get("submission_started") is True
            or reservation is not None and row.get("status") in ("completed", "failed"))
        if not recoverable: recoverable = await _stale(db, scope, row, clock())
        if not recoverable: return {"status": "pending_dispatch"}
        if not worker_enabled() or not await _owns_claim(db, claim, clock()): return {"status": "claim_lost"}
        result = await reconciler(db, **scope, proposal_id=row["proposal_id"])
        return {"status": _status(result), "proposal_id": row["proposal_id"], "reconciled": True}
    if not allow_creation or not creation_execution_enabled(): return {"status": "execution_disabled"}
    if not _policy_gate(policy, clock(), scope): return {"status": "policy_disabled"}
    if (await db[PROPOSALS].find_one({**scope, "status": {"$in": list(PENDING)}}, {"_id": 1})
        or await db[FENCES].find_one({**scope, "status": "claimed"}, {"_id": 1})):
        return {"status": "account_execution_pending"}
    plan = await plan_builder(db, **scope, current=clock())
    choice = _choice(plan, policy, scope)
    if choice is None: return {"status": "no_eligible_creation"}
    async def ready():
        latest = await db[POLICIES].find_one(scope, {"_id": 0})
        return (worker_enabled() and creation_execution_enabled() and _policy_gate(latest, clock(), scope)
            and _snapshot(latest) == _snapshot(policy) and await _owns_claim(db, claim, clock()))
    if not await ready(): return {"status": "authority_changed"}
    proposal = await previewer(db, **scope, policy_revision=policy["revision"], choice=choice)
    if (not _owned({"user_id": scope["user_id"], **(proposal if isinstance(proposal, dict) else {})}, scope)
        or proposal.get("provider") != "google_ads" or (proposal.get("planned") or {}).get("initial_status") != "PAUSED"
        or proposal["automation_source"].get("creation_identity") != plan["intent"]["creation_identity"]):
        return {"status": "preview_unproved"}
    if proposal.get("status") == "completed" and proposal.get("verified") is True:
        return {"status": "completed", "already_completed": True}
    if proposal.get("status") != "previewed": return {"status": "pending"}
    if not await ready(): return {"status": "authority_changed"}
    result = await executor(db, **scope, proposal_id=proposal["proposal_id"])
    return {"status": _status(result), "proposal_id": proposal["proposal_id"], "initial_status": "PAUSED", "activation_performed": False}


def _status(result):
    value = result.get("status") if isinstance(result, dict) else None
    return value if value in ("completed", "failed", "submitted", "verifying", "uncertain") else "pending"


async def run_google_creation_cycle(db, *, max_accounts=4, plan_builder=None, previewer=None,
        executor=None, reconciler=None, clock=utcnow):
    """Process at most four acquired account cycles, one PAUSED creation each."""
    if type(max_accounts) is not int or not 1 <= max_accounts <= MAX_ACCOUNTS: raise ValueError("invalid max_accounts")
    if not worker_enabled(): return {"status": "disabled", "accounts": 0, "results": []}
    allow_creation = creation_execution_enabled()
    policies = await db[POLICIES].find({"enabled": True}, {"_id": 0}).to_list(length=33) if allow_creation else []
    policies = [p for p in policies[:32] if _scope_ok(p.get("user_id"), p.get("account_id"))
        and _policy_gate(p, clock(), {"user_id": p["user_id"], "account_id": p["account_id"]})]
    pending = await db[PROPOSALS].find({"automation_source.kind": "standing_creation", "status": {"$in": list(PENDING)}}, FIELDS).to_list(length=33)
    unsettled = await db[POLICIES].find({"_creation_claim": {"$exists": True, "$ne": None}}, {"_id": 0, "user_id": 1, "account_id": 1}).to_list(length=33)
    accounts = {}
    recoveries = set()
    for row in [*unsettled[:32], *pending[:32], *policies]:
        if _scope_ok(row.get("user_id"), row.get("account_id")):
            accounts.setdefault((row["user_id"], row["account_id"]), None)
    for row in [*unsettled[:32], *pending[:32]]:
        if _scope_ok(row.get("user_id"), row.get("account_id")): recoveries.add((row["user_id"], row["account_id"]))
    if accounts and reconciler is None:
        from .google_ads_automation_creation_gateway import reconcile_google_automation_creation
        reconciler = reconcile_google_automation_creation
    if policies:
        if plan_builder is None:
            from .google_ads_automation_creation_plan import build_google_creation_plan
            plan_builder = build_google_creation_plan
        if previewer is None or executor is None:
            from .google_ads_automation_creation_gateway import preview_google_automation_creation, execute_google_automation_creation
            previewer = previewer or preview_google_automation_creation
            executor = executor or execute_google_automation_creation
    processed, results = 0, []
    for user_id, account_id in accounts:
        if processed >= max_accounts: break
        scope = {"user_id": user_id, "account_id": account_id}
        recovery = (user_id, account_id) in recoveries
        claim = await _claim(db, scope, clock(), recovery)
        result = {"status": "not_due"}
        if claim:
            processed += 1
            try:
                result = await asyncio.wait_for(_account_cycle(db, scope, claim, plan_builder=plan_builder,
                    previewer=previewer, executor=executor, reconciler=reconciler, clock=clock,
                    allow_creation=allow_creation), timeout=ACCOUNT_TIMEOUT_SECONDS)
            except asyncio.CancelledError: raise
            except Exception:
                result = {"status": "failed", "code": "google_creation_cycle_failed"}
            finally: await _release(db, claim, result, clock(), recovery)
        results.append({"account_id": account_id, **result})
    return {"status": "complete" if allow_creation else "recovery_only", "accounts": processed,
        "considered": len(results), "deferred": max(0, len(accounts)-len(results)), "results": results}
