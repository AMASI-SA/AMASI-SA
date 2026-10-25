"""Explicit owner delegation for the existing governed Snapchat AI executor.

Policies default to disabled. Enabling requires current owner acknowledgement,
native limits and expiry, plus an installed final provider-write fence. Server
execution/proposal bindings, never client expected_outcome JSON, carry authority.
The existing five-hour monitor invokes this module; it owns no extra timer.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from integrations_control_center.snapchat_account_selection import _load_selected_accounts
from integrations_control_center.snapchat_native_data_common import SnapchatNativeSyncError

POLICY_COLLECTION = "mezan_snapchat_ai_policies_v1"
LEASE_COLLECTION = "mezan_snapchat_ai_account_leases_v1"
BINDING_COLLECTION = "mezan_snapchat_ai_proposal_bindings_v1"
ALLOWANCE_COLLECTION = "mezan_snapchat_ai_daily_allowances_v1"
RUN_COLLECTION = "mezan_snapchat_ai_autonomy_runs_v1"
EXECUTION_COLLECTION = "mezan_campaign_ai_executions_v1"
RECOMMENDATION_COLLECTION = "mezan_campaign_ai_recommendations_v1"
FENCE_VERSION = "snapchat-autonomy-final-write-v1"
LEASE_DURATION = timedelta(minutes=5)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
POLICY_FIELDS = (
    "mode", "allowed_actions", "max_entity_daily_budget_micro",
    "max_daily_budget_increase_micro", "max_change_percent",
    "min_interval_minutes", "max_actions_per_cycle", "expires_at",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":"), default=str).encode()).hexdigest()


def _problem(code: str, message: str = "تعذر تنفيذ التفويض ضمن حدوده الحالية.", status: int = 409):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _account_id(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise _problem("snapchat_autonomy_account_invalid", "رقم حساب Snapchat غير صالح.", 422)
    return value


def executor_ready() -> bool:
    # A policy saved before integration must not become a dormant grant.
    from integrations_control_center import snapchat_campaign_management as management
    return getattr(management, "AUTONOMY_WRITE_FENCE_VERSION", None) == FENCE_VERSION


class SnapchatAutonomyPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    mode: Literal["disabled", "shadow", "enabled"] = "disabled"
    allowed_actions: list[Literal["pause", "reduce", "scale"]] = Field(default_factory=list, max_length=3)
    max_entity_daily_budget_micro: int | None = Field(default=None, ge=5_000_000, le=50_000_000_000_000)
    max_daily_budget_increase_micro: int | None = Field(default=None, ge=0, le=50_000_000_000_000)
    max_change_percent: int | None = Field(default=None, ge=5, le=30)
    min_interval_minutes: int | None = Field(default=None, ge=300, le=10_080)
    max_actions_per_cycle: int | None = Field(default=None, ge=1, le=18)
    expires_at: str | None = Field(default=None, max_length=80)
    acknowledgement_version: str | None = Field(default=None, max_length=80)
    accept_autonomous_execution: bool = False

    @model_validator(mode="after")
    def validate_policy(self):
        if len(self.allowed_actions) != len(set(self.allowed_actions)):
            raise ValueError("allowed_actions must be unique")
        if self.expires_at is not None and _instant(self.expires_at) is None:
            raise ValueError("expires_at must include an ISO timezone")
        if self.mode != "disabled":
            if not self.allowed_actions or any(getattr(self, field) is None for field in (
                "max_entity_daily_budget_micro", "max_daily_budget_increase_micro",
                "max_change_percent", "min_interval_minutes", "max_actions_per_cycle", "expires_at",
            )):
                raise ValueError("actions, native limits, cadence and expiry must be explicit")
            if "scale" in self.allowed_actions and self.max_daily_budget_increase_micro == 0:
                raise ValueError("scale requires an explicit positive daily budget-increase allowance")
        if self.mode == "enabled" and (
            self.accept_autonomous_execution is not True
            or self.acknowledgement_version != FENCE_VERSION
        ):
            raise ValueError("current explicit owner acknowledgement is required")
        return self


class SnapchatAutonomyRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)


async def _binding(db: Any, user_id: str, account_id: str) -> dict[str, str]:
    accounts = await _load_selected_accounts(db, user_id)
    matches = [row for row in accounts if row.get("ad_account_id") == account_id]
    if len(matches) != 1:
        raise _problem("snapchat_autonomy_account_not_selected", "اختر حساب Snapchat داخل ميزان أولًا.")
    row = matches[0]
    if (row.get("user_id") != user_id or row.get("provider") != "snapchat_ads"
            or row.get("mezan_selected") is not True
            or row.get("connection_status") != "connected"
            or row.get("connection_provenance") != "api_connection"
            or row.get("external_account_id") not in {None, account_id}):
        raise _problem("snapchat_autonomy_account_identity_invalid")
    currency, zone = row.get("currency"), row.get("timezone")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise _problem("snapchat_autonomy_currency_unverified")
    try:
        if not isinstance(zone, str) or not zone:
            raise ValueError("missing timezone")
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise _problem("snapchat_autonomy_timezone_unverified")
    return {"account_id": account_id, "currency": currency, "timezone": zone,
            "display_name": str(row.get("display_name") or account_id)}


def _policy_identity(user_id: str, account_id: str) -> dict[str, str]:
    return {"_id": _key("policy", user_id, account_id), "user_id": user_id, "account_id": account_id}


def _policy_blockers(row: dict[str, Any], binding: dict[str, str] | None, current: datetime) -> list[str]:
    blockers = []
    if binding is None:
        blockers.append("selected_account_unavailable")
    elif any(row.get(key) != binding.get(key) for key in ("currency", "timezone")):
        blockers.append("account_binding_changed")
    expires = _instant(row.get("expires_at"))
    if expires is None or expires <= current:
        blockers.append("policy_expired")
    if row.get("mode") == "enabled":
        if not executor_ready():
            blockers.append("final_write_fence_not_installed")
        if (row.get("authorization_version") != FENCE_VERSION
                or not row.get("grant_id") or not row.get("granted_by")):
            blockers.append("current_owner_grant_required")
    return blockers


def _public(row: dict[str, Any] | None, binding: dict[str, str] | None, current: datetime) -> dict[str, Any]:
    value = row or {}
    mode = value.get("mode", "disabled")
    blockers = _policy_blockers(value, binding, current) if row and mode != "disabled" else []
    return {
        "policy_id": value.get("_id"), "account_id": value.get("account_id") or (binding or {}).get("account_id"),
        "display_name": (binding or {}).get("display_name") or value.get("display_name"),
        "revision": int(value.get("revision") or 0), "configured_mode": mode,
        "effective_mode": mode if not blockers else "disabled", "blockers": blockers,
        "executor_ready": executor_ready(), "acknowledgement_version": FENCE_VERSION,
        "execution_enabled": mode == "enabled" and not blockers,
        "currency": value.get("currency") or (binding or {}).get("currency"),
        "timezone": value.get("timezone") or (binding or {}).get("timezone"),
        **{key: value.get(key) for key in POLICY_FIELDS if key != "mode"},
        "updated_at": value.get("updated_at"), "revoked_at": value.get("revoked_at"),
        "provider_write_reached": False,
    }


async def save_snapchat_autonomy_policy(db, user_id, actor_id, account_id, payload, *, now=_utcnow):
    if actor_id != user_id:
        raise _problem("snapchat_autonomy_owner_required", status=403)
    account_id = _account_id(account_id)
    binding = await _binding(db, user_id, account_id)
    current = now()
    if payload.mode != "disabled" and (_instant(payload.expires_at) or current) <= current:
        raise _problem("snapchat_autonomy_expired", "اختر موعد انتهاء في المستقبل.", 422)
    if payload.mode == "enabled" and not executor_ready():
        raise _problem("snapchat_autonomy_executor_unavailable", "التنفيذ التلقائي غير جاهز بعد؛ استخدم المحاكاة.")
    identity = _policy_identity(user_id, account_id)
    collection = db[POLICY_COLLECTION]
    existing = await collection.find_one(identity)
    timestamp = current.isoformat()
    values = {key: getattr(payload, key) for key in POLICY_FIELDS}
    values.update(binding)
    values.update({
        "updated_at": timestamp, "updated_by": actor_id,
        "revoked_at": timestamp if payload.mode == "disabled" else None,
        "authorization_version": FENCE_VERSION if payload.mode == "enabled" else None,
        "grant_id": secrets.token_hex(16) if payload.mode == "enabled" else None,
        "granted_by": actor_id if payload.mode == "enabled" else None,
        "granted_at": timestamp if payload.mode == "enabled" else None,
        "expires_at_utc": _instant(payload.expires_at),
    })
    audit = {"event": "policy_saved", "actor_id": actor_id, "occurred_at": timestamp,
             "mode": payload.mode, "revision": payload.expected_revision + 1,
             "limits": {key: getattr(payload, key) for key in POLICY_FIELDS},
             "currency": binding["currency"], "timezone": binding["timezone"],
             "grant_id": values["grant_id"]}
    if existing is None:
        if payload.expected_revision != 0:
            raise _problem("snapchat_autonomy_revision_conflict", "تغيرت الإعدادات؛ حدّث الصفحة قبل الحفظ.")
        result = {**identity, **values, "revision": 1, "created_at": timestamp, "audit": [audit]}
        try:
            await collection.insert_one(result)
        except DuplicateKeyError:
            raise _problem("snapchat_autonomy_revision_conflict", "تغيرت الإعدادات؛ حدّث الصفحة قبل الحفظ.")
    else:
        result = await collection.find_one_and_update(
            {**identity, "revision": payload.expected_revision},
            {"$set": values, "$inc": {"revision": 1},
             "$push": {"audit": {"$each": [audit], "$slice": -50}}},
            return_document=ReturnDocument.AFTER,
        )
        if result is None:
            raise _problem("snapchat_autonomy_revision_conflict", "تغيرت الإعدادات؛ حدّث الصفحة قبل الحفظ.")
    return _public(result, binding, current)


async def revoke_snapchat_autonomy_policy(db, user_id, actor_id, account_id, expected_revision, *, now=_utcnow):
    if actor_id != user_id:
        raise _problem("snapchat_autonomy_owner_required", status=403)
    account_id = _account_id(account_id)
    identity = _policy_identity(user_id, account_id)
    current = now()
    collection = db[POLICY_COLLECTION]
    existing = await collection.find_one(identity)
    if existing is None and expected_revision == 0:
        return _public({"account_id": account_id, "mode": "disabled"}, None, current)
    row = await collection.find_one_and_update(
        {**identity, "revision": expected_revision},
        {"$set": {"mode": "disabled", "grant_id": None, "authorization_version": None,
                  "revoked_at": current.isoformat(), "updated_at": current.isoformat(), "updated_by": actor_id},
         "$inc": {"revision": 1}, "$push": {"audit": {"$each": [{
             "event": "policy_revoked", "actor_id": actor_id,
             "occurred_at": current.isoformat(), "revision": expected_revision + 1,
         }], "$slice": -50}}}, return_document=ReturnDocument.AFTER,
    )
    if row is None:
        raise _problem("snapchat_autonomy_revision_conflict", "تغيرت الإعدادات؛ حدّث الصفحة.")
    # Revocation does not depend on a still-connected account.
    return _public(row, None, current)


async def get_snapchat_autonomy_policy(db, user_id, account_id, *, now=_utcnow):
    account_id = _account_id(account_id)
    row = await db[POLICY_COLLECTION].find_one(_policy_identity(user_id, account_id))
    try:
        binding = await _binding(db, user_id, account_id)
    except (HTTPException, SnapchatNativeSyncError):
        binding = None
    return _public(row or {"account_id": account_id, "mode": "disabled"}, binding, now())


async def _claim_account_cycle(db, user_id, policy, *, now=_utcnow):
    current = now()
    token = secrets.token_hex(16)
    identity = {"_id": _key("account", user_id, policy["account_id"]), "user_id": user_id,
                "account_id": policy["account_id"]}
    values = {"owner": token, "lease_until": current + LEASE_DURATION,
              "policy_id": policy["_id"], "policy_revision": policy["revision"]}
    try:
        await db[LEASE_COLLECTION].insert_one({**identity, **values})
        return token
    except DuplicateKeyError:
        pass
    row = await db[LEASE_COLLECTION].find_one_and_update(
        {**identity, "$and": [
            {"$or": [{"owner": None}, {"lease_until": {"$lte": current}}]},
            {"$or": [{"last_cycle_at": {"$exists": False}},
                     {"last_cycle_at": {"$lte": current - timedelta(minutes=policy["min_interval_minutes"])}}]},
        ]}, {"$set": values}, return_document=ReturnDocument.AFTER,
    )
    return token if row else None


async def _require_policy(db, user_id, authorization, *, now=_utcnow):
    if (authorization.get("kind") != "account_policy"
            or authorization.get("version") != FENCE_VERSION
            or not isinstance(authorization.get("lease_owner"), str)
            or len(authorization["lease_owner"]) < 16):
        raise _problem("snapchat_autonomy_authorization_invalid")
    account_id = str(authorization.get("account_id") or "")
    policy = await db[POLICY_COLLECTION].find_one(_policy_identity(user_id, account_id))
    if not policy or policy.get("mode") != "enabled":
        raise _problem("snapchat_autonomy_policy_revoked")
    binding = await _binding(db, user_id, account_id)
    if _policy_blockers(policy, binding, now()):
        raise _problem("snapchat_autonomy_policy_unavailable")
    if any(policy.get(field) != authorization.get(field) for field in ("grant_id", "revision")):
        raise _problem("snapchat_autonomy_policy_changed")
    if policy["_id"] != authorization.get("policy_id"):
        raise _problem("snapchat_autonomy_policy_identity_mismatch")
    if (authorization.get("granted_by") != policy["granted_by"]
            or authorization.get("actor_id") != f"snapchat-policy:{policy['_id']}:{policy['revision']}"):
        raise _problem("snapchat_autonomy_actor_invalid")
    lease = await db[LEASE_COLLECTION].find_one({
        "_id": _key("account", user_id, account_id), "user_id": user_id,
        "account_id": account_id, "owner": authorization.get("lease_owner"),
        "lease_until": {"$gt": now()},
    })
    if not lease:
        raise _problem("snapchat_autonomy_account_lease_lost")
    return policy


def _validate_change(policy, recommendation, target):
    from campaign_ai_monitor_legacy import _snapchat_recommendation_payload
    if (recommendation.get("provider") != "snapchat"
            or recommendation.get("account_id") != policy["account_id"]
            or target.get("account_id") != policy["account_id"]
            or recommendation.get("action") not in policy["allowed_actions"]):
        raise _problem("snapchat_autonomy_action_outside_policy")
    if target.get("currency_native") != policy["currency"]:
        raise _problem("snapchat_autonomy_currency_changed")
    payload, percent = _snapchat_recommendation_payload(recommendation, target)
    if percent is not None and percent > policy["max_change_percent"]:
        raise _problem("snapchat_autonomy_change_limit_exceeded")
    increase = 0
    if "daily_budget_micro" in payload:
        new = payload["daily_budget_micro"]
        old = int(round(float(target["current_daily_budget_native"]) * 1_000_000))
        if new > policy["max_entity_daily_budget_micro"]:
            raise _problem("snapchat_autonomy_budget_limit_exceeded")
        increase = max(0, new - old)
        # Validate the actual provider-minimum-rounded change, not just the
        # model percentage. A minimum floor can magnify a tiny scale request.
        if increase * 100 > old * policy["max_change_percent"]:
            raise _problem("snapchat_autonomy_actual_change_limit_exceeded")
    return payload, increase


async def validate_autonomous_claim(db, user_id, checked, authorization, *, now=_utcnow):
    policy = await _require_policy(db, user_id, authorization, now=now)
    _validate_change(policy, checked["recommendation"], checked["target"])
    if checked["recommendation"].get("approval_available") is not True:
        raise _problem("snapchat_autonomy_recommendation_not_executable")
    return policy


async def prepare_autonomous_execution(db, user_id, execution, checked, *, now=_utcnow):
    authorization = execution["authorization"]
    policy = await validate_autonomous_claim(db, user_id, checked, authorization, now=now)
    payload, increase = _validate_change(policy, checked["recommendation"], checked["target"])
    day = now().astimezone(ZoneInfo(policy["timezone"])).date().isoformat()
    identity = {"_id": _key("allowance", user_id, policy["account_id"], day),
                "user_id": user_id, "account_id": policy["account_id"], "date": day}
    collection = db[ALLOWANCE_COLLECTION]
    try:
        await collection.insert_one({**identity, "reserved_increase_micro": 0, "execution_ids": []})
    except DuplicateKeyError:
        pass
    reserved = await collection.find_one_and_update(
        {**identity, "execution_ids": {"$ne": execution["execution_id"]},
         "reserved_increase_micro": {"$lte": policy["max_daily_budget_increase_micro"] - increase}},
        {"$inc": {"reserved_increase_micro": increase},
         "$addToSet": {"execution_ids": execution["execution_id"]}},
        return_document=ReturnDocument.AFTER,
    )
    if reserved is None:
        previous = await collection.find_one({**identity, "execution_ids": execution["execution_id"]})
        if previous is None:
            raise _problem("snapchat_autonomy_daily_increase_allowance_exceeded")
    # Reservation is deliberately not refunded after an ambiguous provider
    # outcome. It cannot be consumed a second time by a retry.
    stored = await db[EXECUTION_COLLECTION].update_one(
        {"execution_id": execution["execution_id"], "user_id": user_id, "status": "executing"},
        {"$set": {"autonomy_reserved": {"date": day, "increase_micro": increase,
                                             "expected_changes": payload}}},
    )
    if not stored.matched_count:
        raise _problem("snapchat_autonomy_execution_not_active")
    return payload


async def bind_autonomous_proposal(db, user_id, execution_id, proposal_id, recommendation, target, payload):
    execution = await db[EXECUTION_COLLECTION].find_one({"user_id": user_id, "execution_id": execution_id})
    if not execution or execution.get("authorization_kind") != "account_policy":
        raise _problem("snapchat_autonomy_execution_not_owned")
    await _require_policy(db, user_id, execution["authorization"])
    if (execution.get("account_id") != target.get("account_id")
            or execution.get("entity_id") != target.get("entity_id")
            or execution.get("action") != recommendation.get("action")
            or (execution.get("autonomy_reserved") or {}).get("expected_changes") != payload):
        raise _problem("snapchat_autonomy_execution_binding_mismatch")
    row = {"_id": _key("binding", user_id, proposal_id), "user_id": user_id,
           "proposal_id": proposal_id, "execution_id": execution_id,
           "account_id": target["account_id"], "target_id": target["entity_id"],
           "entity_level": recommendation["entity_level"], "expected_changes": payload}
    try:
        await db[BINDING_COLLECTION].insert_one(row)
    except DuplicateKeyError:
        previous = await db[BINDING_COLLECTION].find_one({"_id": row["_id"], "user_id": user_id})
        if previous != row:
            raise _problem("snapchat_autonomy_proposal_binding_conflict")
    await db[EXECUTION_COLLECTION].update_one(
        {"execution_id": execution_id, "user_id": user_id},
        {"$set": {"provider_proposal_id": proposal_id}},
    )


async def require_snapchat_autonomy_write_authorization(
    db, *, user_id, proposal_id, account_id, target_id, action, changes, now=_utcnow,
):
    """Final management fence. No binding means ordinary manual governance.

    Call immediately before the actual client.execute operation. Client JSON
    cannot opt into, opt out of, or overwrite this server-owned binding.
    """
    binding = await db[BINDING_COLLECTION].find_one({
        "_id": _key("binding", user_id, proposal_id), "user_id": user_id,
    })
    if binding is None:
        return
    execution = await db[EXECUTION_COLLECTION].find_one({
        "execution_id": binding["execution_id"], "user_id": user_id,
        "authorization_kind": "account_policy", "status": "executing",
    })
    if not execution:
        raise _problem("snapchat_autonomy_execution_not_active")
    policy = await _require_policy(db, user_id, execution["authorization"], now=now)
    expected_action = {"campaign": "campaign.update", "ad_group": "ad_squad.update", "ad": "ad.update"}.get(binding["entity_level"])
    if (account_id != binding["account_id"] or account_id != policy["account_id"]
            or target_id != binding["target_id"] or action != expected_action
            or changes != binding["expected_changes"]
            or execution.get("provider_proposal_id") != proposal_id):
        raise _problem("snapchat_autonomy_final_operation_changed")
    if execution.get("action") not in policy["allowed_actions"]:
        raise _problem("snapchat_autonomy_action_outside_policy")
    if changes.get("daily_budget_micro", 0) > policy["max_entity_daily_budget_micro"]:
        raise _problem("snapchat_autonomy_budget_limit_exceeded")
    reserved = execution.get("autonomy_reserved") or {}
    day = now().astimezone(ZoneInfo(policy["timezone"])).date().isoformat()
    if reserved.get("date") != day or reserved.get("expected_changes") != changes:
        raise _problem("snapchat_autonomy_allowance_day_or_operation_changed")
    allowance = await db[ALLOWANCE_COLLECTION].find_one({
        "_id": _key("allowance", user_id, account_id, day), "user_id": user_id,
        "execution_ids": execution["execution_id"],
    })
    if (not allowance
            or int(allowance.get("reserved_increase_micro") or 0) > policy["max_daily_budget_increase_micro"]):
        raise _problem("snapchat_autonomy_daily_increase_allowance_unproven")
    lease = await db[LEASE_COLLECTION].find_one_and_update(
        {"_id": _key("account", user_id, account_id), "user_id": user_id,
         "owner": execution["authorization"]["lease_owner"], "lease_until": {"$gt": now()}},
        {"$set": {"lease_until": now() + LEASE_DURATION}}, return_document=ReturnDocument.AFTER,
    )
    if lease is None:
        raise _problem("snapchat_autonomy_account_lease_lost")
    # This is the final database operation before returning to provider write.
    # Revocation/revision races before this fence invalidate the exact grant.
    permit = await db[POLICY_COLLECTION].find_one_and_update(
        {"_id": policy["_id"], "user_id": user_id, "account_id": account_id,
         "revision": policy["revision"], "grant_id": policy["grant_id"],
         "mode": "enabled", "authorization_version": FENCE_VERSION,
         "expires_at_utc": {"$gt": now()}},
        {"$set": {"last_write_authorized_at": now().isoformat()}},
        return_document=ReturnDocument.AFTER,
    )
    if permit is None:
        raise _problem("snapchat_autonomy_policy_revoked_before_write")


async def trusted_execution_proposal(db, user_id, execution, *, allow_incomplete_link=False):
    """Return only a server-bound proposal in the same exact execution scope."""
    if (execution.get("user_id") != user_id or execution.get("provider") != "snapchat"
            or execution.get("authorization_kind") != "account_policy"):
        return None
    proposal_id = execution.get("provider_proposal_id")
    if proposal_id:
        binding = await db[BINDING_COLLECTION].find_one({
            "_id": _key("binding", user_id, proposal_id), "user_id": user_id,
        })
    elif allow_incomplete_link:
        bindings = await db[BINDING_COLLECTION].find({
            "user_id": user_id, "execution_id": execution["execution_id"],
        }).limit(2).to_list(length=2)
        binding = bindings[0] if len(bindings) == 1 else None
        proposal_id = (binding or {}).get("proposal_id")
    else:
        return None
    if not binding or not proposal_id or binding.get("proposal_id") != proposal_id:
        return None
    if (binding.get("execution_id") != execution.get("execution_id")
            or binding.get("account_id") != execution.get("account_id")
            or binding.get("target_id") != execution.get("entity_id")
            or binding.get("entity_level") != execution.get("entity_level")
            or binding.get("expected_changes") != (execution.get("autonomy_reserved") or {}).get("expected_changes")):
        return None
    proposal = await db["mezan_snapchat_campaign_proposals_v1"].find_one({
        "user_id": user_id, "proposal_id": proposal_id, "account_id": execution["account_id"],
    })
    if not proposal:
        return None
    from integrations_control_center import snapchat_campaign_management as management
    expected_action = {"campaign": "campaign.update", "ad_group": "ad_squad.update", "ad": "ad.update"}.get(execution.get("entity_level"))
    operation = proposal.get("operation") or {}
    if (proposal.get("action") != expected_action
            or management._provider_target_id(proposal) != binding["target_id"]
            or management._operation_expected_values(operation) != binding["expected_changes"]):
        return None
    return binding, proposal


def _known_linked_resolution(execution, binding, proposal):
    """Interpret proved outcomes; terminal status strings alone prove nothing."""
    from integrations_control_center import snapchat_campaign_management as management
    if proposal.get("provider_write_uncertain") is not False or proposal.get("rollback_write_uncertain") is True:
        return None
    target_id = binding["target_id"]
    operation = proposal["operation"]
    expected = binding["expected_changes"]
    verification = proposal.get("verification") or {}
    snapshot = verification.get("provider_snapshot") or {}
    if (proposal.get("status") == "completed"
            and proposal.get("provider_write_state") == "confirmed"
            and proposal.get("provider_write_reached") is True
            and proposal.get("provider_entity_id") == target_id
            and verification.get("verified") is True
            and verification.get("field_changes_verified") is True
            and verification.get("entity_id") == target_id
            and verification.get("verification_source") in {"readback", "post_error_readback"}
            and _instant(verification.get("verified_at")) is not None
            and snapshot.get("id") == target_id
            and snapshot.get("ad_account_id") in {None, execution["account_id"]}
            and management._changed_values_match(expected, snapshot)[0]):
        return {"status": "completed", "writes_performed": True,
                "provider_write_outcome_known": True, "outcome": "applied",
                "proof_at": verification["verified_at"], "snapshot": snapshot}
    current = proposal.get("reconciliation_snapshot") or {}
    original = proposal.get("original_snapshot") or {}
    if (proposal.get("status") == "failed"
            and proposal.get("provider_write_state") == "confirmed_not_applied"
            and proposal.get("provider_write_reached") is False
            and current.get("id") == target_id and original.get("id") == target_id
            and current.get("ad_account_id") in {None, execution["account_id"]}
            and not management._changed_values_match(expected, current)[0]
            and not management._provider_state_conflict_fields(operation, original, current)):
        return {"status": "failed", "writes_performed": False,
                "provider_write_outcome_known": True, "outcome": "not_applied",
                "proof_at": None, "snapshot": current}
    return None


async def cancelled_execution_outcome(db, user_id, execution, *, now=_utcnow):
    """Cancellation is not proof that a provider request was never sent."""
    base = {"finished_at": now().isoformat(), "cancelled_at": now().isoformat()}
    if execution.get("authorization_kind") != "account_policy":
        return {**base, "status": "provider_state_uncertain", "writes_performed": False,
                "provider_write_outcome_known": False,
                "failure": {"code": "campaign_execution_cancelled_outcome_unknown"}}
    linked = await trusted_execution_proposal(db, user_id, execution, allow_incomplete_link=True)
    if linked is not None:
        binding, proposal = linked
        known = _known_linked_resolution(execution, binding, proposal)
        if known:
            return {**base, "status": known["status"], "writes_performed": known["writes_performed"],
                    "provider_write_outcome_known": True,
                    "failure": {"code": "campaign_execution_cancelled_after_known_provider_outcome"}}
        no_send = bool(
            proposal.get("provider_write_state") in {"not_attempted", "preflighting"}
            and proposal.get("provider_write_reached") is False
            and proposal.get("provider_write_uncertain") is not True
        )
        if no_send:
            return {**base, "status": "cancelled", "writes_performed": False,
                    "provider_write_outcome_known": True,
                    "failure": {"code": "campaign_execution_cancelled_before_provider_write"}}
        return {**base, "status": "provider_state_uncertain",
                "writes_performed": proposal.get("provider_write_reached") is True,
                "provider_write_outcome_known": False,
                "failure": {"code": "campaign_execution_cancelled_outcome_unknown"}}
    # Only the absence of BOTH a proposal link and a trusted binding proves
    # that this automatic runner has not reached its write-capable phase.
    binding = await db[BINDING_COLLECTION].find_one({
        "user_id": user_id, "execution_id": execution["execution_id"],
    })
    no_send = not execution.get("provider_proposal_id") and binding is None
    return {**base, "status": "cancelled" if no_send else "provider_state_uncertain",
            "writes_performed": False, "provider_write_outcome_known": no_send,
            "failure": {"code": "campaign_execution_cancelled_before_provider_write"
                        if no_send else "campaign_execution_cancelled_outcome_unknown"}}


async def refresh_linked_execution_outcomes(db, user_id, account_id, *, now=_utcnow):
    """Bounded projection of trusted read-only management reconciliation.

    This does not call the provider or replay an execution. The original claim
    and increase allowance stay intact even when the provider proves no write.
    """
    pending = await db[EXECUTION_COLLECTION].find({
        "user_id": user_id, "account_id": account_id, "authorization_kind": "account_policy",
        "status": {"$in": ["executing", "provider_state_uncertain", "verification_required"]},
    }).limit(19).to_list(length=19)
    if len(pending) > 18:
        return {"refreshed": 0, "blocked": "pending_execution_limit"}
    refreshed = 0
    for execution in pending:
        linked = await trusted_execution_proposal(db, user_id, execution)
        if linked is None:
            continue
        binding, proposal = linked
        resolution = _known_linked_resolution(execution, binding, proposal)
        if resolution is None:
            continue
        observed_at = _instant(resolution["proof_at"])
        if observed_at is None:
            # The not-applied branch retains its exact provider snapshot and
            # releases the exact entity lease in the same reconciliation.
            lease = await db["mezan_snapchat_campaign_entity_leases_v1"].find_one({
                "user_id": user_id, "proposal_id": proposal["proposal_id"], "active": False,
            }, sort=[("released_at", -1)])
            observed_at = _instant((lease or {}).get("released_at")) or now()
        if observed_at > now() + timedelta(minutes=5):
            continue
        # Persist the fresh-plan fence before clearing the blocking execution.
        # A crash between the two writes must retain blocking, never expose an
        # old plan as executable without its reconciliation cutoff.
        fenced = await db[LEASE_COLLECTION].update_one(
            {"_id": _key("account", user_id, account_id), "user_id": user_id},
            {"$max": {"require_snapshot_after": observed_at}},
        )
        if not fenced.matched_count:
            continue
        changed = await db[EXECUTION_COLLECTION].update_one(
            {"execution_id": execution["execution_id"], "user_id": user_id,
             "account_id": account_id, "provider_proposal_id": proposal["proposal_id"],
             "status": execution["status"]},
            {"$set": {
                "status": resolution["status"], "writes_performed": resolution["writes_performed"],
                "provider_write_outcome_known": True, "reconciled_at": now().isoformat(),
                "reconciliation": {"source": "linked_management_read_only_reconciliation",
                    "outcome": resolution["outcome"], "proposal_id": proposal["proposal_id"],
                    "proof_at": observed_at.isoformat(), "snapshot_digest": _key(resolution["snapshot"])},
             }, "$push": {"audit": {"$each": [{
                 "event": "provider_outcome_reconciled", "actor_id": "snapchat-reconciliation-projection",
                 "occurred_at": now().isoformat(), "outcome": resolution["outcome"],
             }], "$slice": -50}}},
        )
        if not changed.matched_count:
            continue
        refreshed += 1
        await db[RECOMMENDATION_COLLECTION].update_one(
            {"user_id": user_id, "snapshot_id": execution["snapshot_id"]},
            {"$set": {"recommendations.$[item].execution_status": resolution["status"]}},
            array_filters=[{"item.recommendation_id": execution["recommendation_id"]}],
        )
    return {"refreshed": refreshed}


async def dispatch_snapchat_autonomy(db, user_id, snapshot_id, *, now=_utcnow):
    """Process only this exact newly saved snapshot, once per account/cycle."""
    from campaign_ai_monitor_legacy import (
        claim_campaign_recommendation_execution, run_claimed_campaign_execution,
    )
    snapshot = await db[RECOMMENDATION_COLLECTION].find_one(
        {"user_id": user_id}, sort=[("generated_at", -1)],
    )
    if not snapshot or snapshot.get("snapshot_id") != snapshot_id:
        return {"status": "blocked", "reason": "snapshot_not_latest", "accounts": []}
    policies = await db[POLICY_COLLECTION].find(
        {"user_id": user_id, "mode": {"$in": ["shadow", "enabled"]}}
    ).limit(50).to_list(length=50)
    outcomes = []
    for policy in policies:
        account_id = policy["account_id"]
        outcome = {"account_id": account_id, "mode": policy["mode"], "actions": [], "blockers": []}
        token = None
        try:
            binding = await _binding(db, user_id, account_id)
            blockers = _policy_blockers(policy, binding, now())
            if blockers:
                outcome["blockers"] = blockers
                continue
            await refresh_linked_execution_outcomes(db, user_id, account_id, now=now)
            if await db[EXECUTION_COLLECTION].find_one({
                "user_id": user_id, "account_id": account_id, "authorization_kind": "account_policy",
                "status": {"$in": ["executing", "provider_state_uncertain", "verification_required"]},
            }):
                outcome["blockers"] = ["previous_execution_requires_reconciliation"]
                continue
            account_state = await db[LEASE_COLLECTION].find_one({
                "_id": _key("account", user_id, account_id), "user_id": user_id,
            }) or {}
            fresh_after = _instant(account_state.get("require_snapshot_after"))
            generated_at = _instant(snapshot.get("generated_at"))
            if fresh_after is not None and (generated_at is None or generated_at <= fresh_after):
                outcome["blockers"] = ["fresh_snapshot_required_after_reconciliation"]
                continue
            token = await _claim_account_cycle(db, user_id, policy, now=now)
            if not token:
                outcome["blockers"] = ["account_cycle_busy_or_cooldown"]
                continue
            authorization = {"kind": "account_policy", "policy_id": policy["_id"],
                             "account_id": account_id, "revision": policy["revision"],
                             "grant_id": policy.get("grant_id"), "lease_owner": token,
                             "actor_id": f"snapchat-policy:{policy['_id']}:{policy['revision']}",
                             "granted_by": policy.get("granted_by"), "version": FENCE_VERSION}
            attempted = 0
            shadow_reserved = 0
            if policy["mode"] == "shadow":
                day = now().astimezone(ZoneInfo(policy["timezone"])).date().isoformat()
                allowance = await db[ALLOWANCE_COLLECTION].find_one({
                    "_id": _key("allowance", user_id, account_id, day), "user_id": user_id,
                }) or {}
                shadow_reserved = int(allowance.get("reserved_increase_micro") or 0)
            for recommendation in snapshot.get("recommendations") or []:
                if attempted >= policy["max_actions_per_cycle"]:
                    break
                if (recommendation.get("provider") != "snapchat"
                        or recommendation.get("account_id") != account_id
                        or recommendation.get("action") not in policy["allowed_actions"]):
                    continue
                recommendation_id = recommendation["recommendation_id"]
                target = (snapshot.get("execution_targets") or {}).get(recommendation_id) or {}
                if recommendation.get("approval_available") is not True:
                    continue
                try:
                    _, increase = _validate_change(policy, recommendation, target)
                    if policy["mode"] == "shadow":
                        if shadow_reserved + increase > policy["max_daily_budget_increase_micro"]:
                            raise _problem("snapchat_autonomy_daily_increase_allowance_exceeded")
                        from campaign_ai_execution_quality_gate import preflight_approved_execution
                        await preflight_approved_execution(
                            db, recommendation_collection=RECOMMENDATION_COLLECTION,
                            user_id=user_id, snapshot_id=snapshot_id,
                            recommendation_id=recommendation_id, now=now,
                        )
                        outcome["actions"].append({"recommendation_id": recommendation_id, "status": "shadow_eligible"})
                        shadow_reserved += increase
                        attempted += 1
                        continue
                    claim = await claim_campaign_recommendation_execution(
                        db, user_id, snapshot_id=snapshot_id,
                        recommendation_id=recommendation_id, authorization=authorization,
                    )
                    if claim["claimed"]:
                        attempted += 1
                        result = await run_claimed_campaign_execution(db, user_id, claim["execution_id"])
                    else:
                        result = claim["execution"]
                    outcome["actions"].append({"recommendation_id": recommendation_id,
                                               "execution_id": claim["execution_id"], "status": result.get("status")})
                    if result.get("status") in {"provider_state_uncertain", "verification_required", "executing"}:
                        break
                except HTTPException as exc:
                    outcome["actions"].append({"recommendation_id": recommendation_id,
                                               "status": "blocked", "code": (exc.detail or {}).get("code", "policy_blocked")})
        except Exception as exc:
            code = (exc.detail or {}).get("code") if isinstance(exc, HTTPException) else type(exc).__name__
            outcome["blockers"] = [str(code or "autonomy_account_failed")[:100]]
        finally:
            if token:
                try:
                    await db[LEASE_COLLECTION].update_one(
                        {"_id": _key("account", user_id, account_id), "owner": token},
                        {"$set": {"owner": None, "lease_until": now(), "last_cycle_at": now()}},
                    )
                except Exception:
                    outcome["blockers"].append("account_lease_release_failed")
            outcomes.append(outcome)
    summary = {"status": "complete", "snapshot_id": snapshot_id, "accounts": outcomes, "finished_at": now().isoformat()}
    await db[RUN_COLLECTION].update_one(
        {"_id": _key("run", user_id, snapshot_id)},
        {"$setOnInsert": {"user_id": user_id, **summary}}, upsert=True,
    )
    return summary


def attach_snapchat_autonomy_routes(router: APIRouter, db, current_user: Callable, require_owner: Callable):
    prefix = "/ai-monitor/snapchat/autonomy"

    @router.get(prefix)
    async def list_policies(user: dict = Depends(current_user)):
        owner_id = str(require_owner(user)["id"])
        try:
            accounts = await _load_selected_accounts(db, owner_id)
        except SnapchatNativeSyncError:
            accounts = []
        ids = list(dict.fromkeys(row["ad_account_id"] for row in accounts))
        rows = await db[POLICY_COLLECTION].find({"user_id": owner_id}).limit(50).to_list(length=50)
        ids.extend(row["account_id"] for row in rows if row["account_id"] not in ids)
        items = [await get_snapchat_autonomy_policy(db, owner_id, account_id) for account_id in ids]
        return {"items": items, "executor_ready": executor_ready(), "acknowledgement_version": FENCE_VERSION}

    @router.get(prefix + "/{account_id}/executions")
    async def execution_history(account_id: str, user: dict = Depends(current_user)):
        owner_id = str(require_owner(user)["id"])
        account_id = _account_id(account_id)
        rows = await db[EXECUTION_COLLECTION].find(
            {"user_id": owner_id, "account_id": account_id, "authorization_kind": "account_policy"},
            {"_id": 0, "execution_id": 1, "recommendation_id": 1, "entity_id": 1,
             "entity_level": 1, "action": 1, "status": 1, "authorized_at": 1,
             "finished_at": 1, "executed_by": 1, "policy_authorized_by": 1,
             "provider_proposal_id": 1, "failure.code": 1, "writes_performed": 1},
        ).sort("authorized_at", -1).limit(20).to_list(length=20)
        return {"items": rows}

    @router.put(prefix + "/{account_id}")
    async def save_policy(account_id: str, payload: SnapchatAutonomyPolicyInput, user: dict = Depends(current_user)):
        owner_id = str(require_owner(user)["id"])
        return await save_snapchat_autonomy_policy(db, owner_id, owner_id, account_id, payload)

    @router.post(prefix + "/{account_id}/revoke")
    async def revoke_policy(account_id: str, payload: SnapchatAutonomyRevisionInput, user: dict = Depends(current_user)):
        owner_id = str(require_owner(user)["id"])
        return await revoke_snapchat_autonomy_policy(db, owner_id, owner_id, account_id, payload.expected_revision)
