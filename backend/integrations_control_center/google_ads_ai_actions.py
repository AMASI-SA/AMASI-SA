"""Bind saved Google advice to the existing human approval gateway.

ADR-001 1/3/4/8/10/11/14: additive, disableable, tenant-scoped and idempotent.
ADR-002: no model write tool; review does not approve or execute an action.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .google_ads_campaign_contract import GoogleCampaignActionInput
from .google_ads_client import identifier, problem
from .google_ads_management import digest

REVIEWABLE_ACTIONS = {"PAUSE_CAMPAIGN": "pause", "RESUME_CAMPAIGN": "enable", "CHANGE_BUDGET": "set_budget"}
EVIDENCE_MAX_AGE = timedelta(hours=5)
_AMOUNT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$")


def ai_action_review_enabled():
    return os.getenv("GOOGLE_ADS_AI_ACTIONS_ENABLED", "true").strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def recommendation_action(row, actor, recommendation_id, *, current):
    """Recover an action from server-owned advice, never client-supplied changes."""
    if not ai_action_review_enabled():
        raise problem("google_ai_action_review_disabled", "مراجعة تنفيذ توصيات جوجل موقوفة مؤقتًا.", 503)
    if row.get("user_id") != actor["user_id"] or not actor.get("can_draft"):
        raise problem("google_ai_analysis_not_owned", "التحليل غير متاح لإدارة إعلانات متجرك.", 403)
    created = _instant(row.get("created_at"))
    if created is None or not timedelta(0) <= current - created < EVIDENCE_MAX_AGE:
        raise problem("google_ai_analysis_expired", "أعد تحليل الحملة؛ أدلة هذه التوصية قديمة أو غير مثبتة.")
    result = row.get("result") or {}
    account_id = identifier(row.get("account_id"))
    period = result.get("period") or {}
    if result.get("provider") != "google_ads" or result.get("account_id") != account_id or period.get("account_id") != account_id:
        raise problem("google_ai_recommendation_scope_invalid", "حساب التوصية لا يطابق حساب التحليل.")
    matches = [item for item in result.get("recommendations", []) if item.get("recommendation_id") == recommendation_id]
    if len(matches) != 1:
        raise problem("google_ai_recommendation_not_found", "التوصية غير متاحة؛ حدّث التحليل.", 404)
    item = matches[0]
    if item.get("provider") != "google_ads" or item.get("account_id") != account_id:
        raise problem("google_ai_recommendation_scope_invalid", "التوصية لا تتبع الحساب المحدد.")
    action = REVIEWABLE_ACTIONS.get(item.get("action"))
    if not action:
        raise problem("google_ai_recommendation_review_only", "هذه توصية تشخيصية أو تحتاج إعدادات إضافية في محرر الحملة.", 422)
    campaign_id = identifier(item.get("campaign_id"))
    currency, time_zone = period.get("currency_code"), period.get("time_zone")
    try:
        valid_zone = isinstance(time_zone, str) and bool(ZoneInfo(time_zone))
    except (ZoneInfoNotFoundError, ValueError):
        valid_zone = False
    if (not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency) or not valid_zone
        or any(not row.get(key) or row[key] != period.get(key) for key in ("date_from", "date_to"))):
        raise problem("google_ai_recommendation_evidence_invalid", "عملة التوصية أو توقيتها أو فترتها غير مثبتة.")
    evidence = item.get("evidence") or []
    native_times = []
    if not isinstance(evidence, list) or not evidence:
        raise problem("google_ai_recommendation_evidence_invalid", "التوصية تحتاج أدلة أداء مثبتة.")
    for fact in evidence:
        if (not isinstance(fact, dict) or fact.get("campaign_id") != campaign_id
            or fact.get("currency_code") != currency or fact.get("time_zone") != time_zone
            or any(fact.get(key) != period[key] for key in ("date_from", "date_to"))):
            raise problem("google_ai_recommendation_evidence_invalid", "أدلة التوصية لا تطابق الحملة والفترة.")
        if fact.get("source") == "google_ads_api":
            observed = _instant(fact.get("observed_at"))
            if (not str(fact.get("evidence_id") or "").startswith(f"google:campaign:{account_id}:{campaign_id}:")
                or observed is None or not timedelta(0) <= current - observed < EVIDENCE_MAX_AGE):
                raise problem("google_ai_recommendation_evidence_invalid", "أدلة جوجل قديمة أو غير مثبتة.")
            native_times.append(observed)
    if not native_times:
        raise problem("google_ai_recommendation_evidence_invalid", "يلزم دليل أداء حديث من جوجل.")
    changes = {}
    if action == "set_budget":
        amount = item.get("proposed_budget_native")
        if not isinstance(amount, str) or not _AMOUNT.fullmatch(amount) or Decimal(amount) <= 0:
            raise problem("google_ai_recommendation_budget_invalid", "مبلغ الميزانية المقترح غير صالح.", 422)
        changes = {"budget_native": amount}
    source = {"analysis_id": row["analysis_id"], "recommendation_id": recommendation_id,
              "account_id": account_id, "campaign_id": campaign_id, "currency": currency, "time_zone": time_zone,
              "date_from": period["date_from"], "date_to": period["date_to"],
              "evidence_digest": digest(item), "expires_at": (min(created, *native_times) + EVIDENCE_MAX_AGE).isoformat()}
    key = "google_ai_" + digest({"analysis_id": row["analysis_id"], "recommendation_id": recommendation_id,
                                "user_id": actor["user_id"], "actor_id": actor["actor_id"]})
    body = GoogleCampaignActionInput(action=action, account_id=account_id, campaign_id=campaign_id,
        changes=changes, reason=str(item.get("rationale") or item.get("title") or "توصية ذكاء ميزان")[:500], idempotency_key=key)
    return body, source
