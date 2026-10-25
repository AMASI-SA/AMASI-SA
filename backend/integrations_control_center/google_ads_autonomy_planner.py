"""Pure Google standing-policy readiness plans; never an execution authorization.

The caller supplies tenant-owned saved analysis, current native workspace and
complete account action history. Even an eligible result requires a fresh native
preview and a separate, dispatch-time standing-authorization check. This module
has no database/client/model access and never creates a worker user or principal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ACTIONS = {"PAUSE_CAMPAIGN": "pause", "RESUME_CAMPAIGN": "enable", "CHANGE_BUDGET": "set_budget"}
_PENDING = {"executing", "approved", "submitted", "verifying", "uncertain"}
_AMOUNT = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$")


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _fresh(value, current, maximum):
    parsed = _instant(value)
    return parsed is not None and timedelta(0) <= current - parsed < maximum


def _amount(value):
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value):
        return None
    try:
        result = Decimal(value)
        return result if result > 0 else None
    except InvalidOperation:
        return None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def plan_google_autonomy(*, user_id, account_id, policy, analysis, workspace, history, current):
    """Return deterministic dry-run candidates, with fail-closed reason codes.

    ``history`` must be complete for pending account operations, campaign cooldowns
    and the account-local current day. It uses native proposal records, including
    uncertain/submitted outcomes; incomplete history cannot prove readiness.
    DAILY amounts mean average daily budget settings, never an actual spend cap.
    This plan reserves budget/action slots only within this returned batch. A real
    executor must reserve them durably and revalidate authorization before dispatch.
    """
    result = {"provider": "google_ads", "account_id": account_id, "mode": "dry_run",
              "executor_available": False, "executable": False, "provider_writes": 0,
              "budget_semantics": "average_daily_budget", "blockers": [], "candidates": []}

    def blocked(code):
        result["blockers"] = [code]
        return result

    if not isinstance(current, datetime) or current.tzinfo is None:
        return blocked("clock_unproved")
    current = current.astimezone(timezone.utc)
    if not isinstance(policy, dict) or policy.get("user_id") != user_id or policy.get("account_id") != account_id:
        return blocked("policy_scope_unproved")
    if policy.get("enabled") is not True:
        return blocked("policy_disabled")
    expiry = _instant(policy.get("expires_at"))
    if expiry is None or expiry <= current:
        return blocked("policy_expired")
    try:
        zone = ZoneInfo(policy["time_zone"])
    except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
        return blocked("policy_invalid")
    campaign_cap = _amount(policy.get("max_campaign_daily_budget_native"))
    account_cap = _amount(policy.get("max_account_daily_budget_native"))
    change_cap = _amount(policy.get("max_budget_change_percent"))
    campaign_ids, allowed = policy.get("campaign_ids"), policy.get("allowed_actions")
    cooldown, daily_limit = policy.get("cooldown_seconds"), policy.get("max_actions_per_day")
    revision = policy.get("revision")
    if (policy.get("schema_version") != 1 or type(revision) is not int or revision < 1
        or not isinstance(policy.get("currency_code"), str) or not re.fullmatch(r"[A-Z]{3}", policy["currency_code"])
        or not isinstance(campaign_ids, list) or not campaign_ids or len(campaign_ids) > 50
        or any(not isinstance(cid, str) or not cid.isdigit() for cid in campaign_ids)
        or len(set(campaign_ids)) != len(campaign_ids)
        or not isinstance(allowed, list) or not allowed or any(a not in _ACTIONS.values() for a in allowed)
        or len(set(allowed)) != len(allowed)
        or campaign_cap is None or account_cap is None or account_cap < campaign_cap
        or change_cap is None or change_cap > 100
        or type(cooldown) is not int or not 3600 <= cooldown <= 604800
        or type(daily_limit) is not int or not 1 <= daily_limit <= 20):
        return blocked("policy_invalid")
    if not isinstance(analysis, dict) or analysis.get("user_id") != user_id or analysis.get("account_id") != account_id or not analysis.get("analysis_id"):
        return blocked("analysis_scope_unproved")
    if not _fresh(analysis.get("created_at"), current, timedelta(hours=5)):
        return blocked("analysis_stale")
    native = analysis.get("result") or {}
    if not isinstance(native, dict):
        return blocked("analysis_scope_unproved")
    period = native.get("period") or {}
    if not isinstance(period, dict):
        return blocked("analysis_scope_unproved")
    if (native.get("provider") != "google_ads" or native.get("account_id") != account_id
        or period.get("account_id") != account_id
        or any(period.get(k) != policy[k] for k in ("currency_code", "time_zone"))
        or any(not analysis.get(k) or analysis[k] != period.get(k) for k in ("date_from", "date_to"))):
        return blocked("analysis_scope_unproved")
    if not isinstance(workspace, dict) or workspace.get("complete") is not True:
        return blocked("workspace_incomplete")
    if (workspace.get("account_id") != account_id
        or any(workspace.get(k) != policy[k] for k in ("currency_code", "time_zone"))):
        return blocked("workspace_scope_unproved")
    if not _fresh(workspace.get("observed_at"), current, timedelta(minutes=10)):
        return blocked("workspace_stale")
    campaigns = workspace.get("campaigns")
    if not isinstance(campaigns, list) or len(campaigns) > 500 or any(not isinstance(c, dict) for c in campaigns):
        return blocked("workspace_incomplete")
    by_id = {c.get("campaign_id"): c for c in campaigns if isinstance(c.get("campaign_id"), str)}
    if len(by_id) != len(campaigns):
        return blocked("workspace_scope_unproved")
    if not isinstance(history, dict) or history.get("complete") is not True:
        return blocked("history_incomplete")
    if (history.get("user_id") != user_id or history.get("account_id") != account_id
        or not isinstance(history.get("items"), list) or len(history["items"]) > 1000):
        return blocked("history_unproved")
    today, action_count, last_action = current.astimezone(zone).date(), 0, {}
    for row in history["items"]:
        if not isinstance(row, dict) or row.get("user_id") != user_id or row.get("account_id") != account_id:
            return blocked("history_unproved")
        if row.get("status") in _PENDING:
            return blocked("account_execution_pending")
        if row.get("status") == "completed" or row.get("submission_started") is True:
            stamp = _instant(row.get("executed_at") or row.get("submitted_at") or row.get("created_at"))
            if stamp is None or stamp > current or not row.get("campaign_id"):
                return blocked("history_unproved")
            if stamp.astimezone(zone).date() == today:
                action_count += 1
            cid = row["campaign_id"]
            last_action[cid] = max(last_action.get(cid, stamp), stamp)
    if action_count >= daily_limit:
        return blocked("daily_action_limit")
    recommendations = native.get("recommendations")
    if not isinstance(recommendations, list) or len(recommendations) > 6 or any(not isinstance(r, dict) for r in recommendations):
        return blocked("recommendations_ambiguous")
    ids = [r.get("recommendation_id") for r in recommendations]
    if any(not isinstance(rid, str) or not rid for rid in ids) or len(set(ids)) != len(ids):
        return blocked("recommendations_ambiguous")
    total, total_proven = Decimal(0), True
    for campaign in campaigns:
        if campaign.get("status") == "ENABLED":
            amount = _amount(campaign.get("budget_native"))
            if (amount is None or campaign.get("budget_mode") != "DAILY" or campaign.get("source") != "google_ads_api"
                or campaign.get("account_id") != account_id or not _fresh(campaign.get("observed_at"), current, timedelta(minutes=10))
                or any(campaign.get(k) != policy[k] for k in ("currency_code", "time_zone"))):
                total_proven = False
            else:
                total += amount
    planned_campaigns = set()
    for item in recommendations:
        cid = item.get("campaign_id")
        candidate = {"recommendation_id": item["recommendation_id"], "campaign_id": cid,
                     "decision": "blocked", "blockers": [], "approval_required": True, "executed": False}
        result["candidates"].append(candidate)
        code = None
        native_action = _ACTIONS.get(item.get("action")) if isinstance(item.get("action"), str) else None
        campaign = by_id.get(cid) if isinstance(cid, str) else None
        evidence = item.get("evidence")
        if (item.get("provider") != "google_ads" or item.get("account_id") != account_id
            or not isinstance(cid, str) or not cid.isdigit()):
            code = "recommendation_scope_unproved"
        elif cid not in campaign_ids:
            code = "campaign_not_allowed"
        elif native_action is None:
            code = "action_unsupported"
        elif native_action not in allowed:
            code = "action_not_allowed"
        elif item.get("confidence") != "high":
            code = "confidence_below_high"
        elif cid in planned_campaigns:
            code = "campaign_already_planned"
        elif action_count >= daily_limit:
            code = "daily_action_limit"
        elif cid in last_action and current - last_action[cid] < timedelta(seconds=cooldown):
            code = "campaign_cooldown"
        elif not isinstance(evidence, list) or not evidence or any(not isinstance(e, dict) for e in evidence):
            code = "evidence_unproved"
        elif any(e.get("campaign_id") != cid or any(e.get(k) != period.get(k) for k in ("currency_code", "time_zone", "date_from", "date_to")) for e in evidence):
            code = "evidence_unproved"
        elif (not any(e.get("source") == "google_ads_api" for e in evidence)
            or any(e.get("source") == "google_ads_api" and (not str(e.get("evidence_id", "")).startswith(f"google:campaign:{account_id}:{cid}:") or not _fresh(e.get("observed_at"), current, timedelta(hours=5))) for e in evidence)):
            code = "evidence_unproved"
        elif not campaign or campaign.get("account_id") != account_id or campaign.get("source") != "google_ads_api" or any(campaign.get(k) != policy[k] for k in ("currency_code", "time_zone")):
            code = "campaign_scope_unproved"
        elif not _fresh(campaign.get("observed_at"), current, timedelta(minutes=10)):
            code = "campaign_stale"
        elif campaign.get("budget_mode") != "DAILY" or _amount(campaign.get("budget_native")) is None:
            code = "campaign_budget_unproved"
        elif campaign.get("status") != ("PAUSED" if native_action == "enable" else "ENABLED"):
            code = "campaign_status_changed"
        changes, next_total = {}, total
        if code is None and native_action in {"enable", "set_budget"}:
            previous = _amount(campaign["budget_native"])
            target = _amount(item.get("proposed_budget_native")) if native_action == "set_budget" else previous
            if target is None:
                code = "budget_amount_invalid"
            elif target > campaign_cap:
                code = "campaign_budget_limit"
            elif native_action == "set_budget" and abs(target - previous) / previous * 100 > change_cap:
                code = "budget_change_limit"
            elif not total_proven:
                code = "account_budget_unproved"
            else:
                next_total = total + (target if native_action == "enable" else target - previous)
                if next_total > account_cap:
                    code = "account_budget_limit"
                elif native_action == "set_budget":
                    changes = {"budget_native": item["proposed_budget_native"]}
        if code is not None:
            candidate["blockers"] = [code]
            continue
        # A planned pause/decrease has not freed real budget. Only reserve
        # increases here; later candidates cannot spend an unexecuted saving.
        total = max(total, next_total)
        action_count += 1
        planned_campaigns.add(cid)
        binding = {"user_id": user_id, "account_id": account_id, "policy_revision": revision,
                   "analysis_id": analysis["analysis_id"], "recommendation_id": item["recommendation_id"],
                   "campaign_id": cid, "action": native_action, "changes": changes}
        candidate.update(decision="eligible_for_preview", native_action=native_action, changes=changes,
                         policy_revision=revision, analysis_id=analysis["analysis_id"],
                         idempotency_key="google_auto_" + _digest(binding))
    return result
