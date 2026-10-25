"""Tenant-scoped, bounded diagnostics for native Google read-only analysis.

Attempts contain phases and constant failure codes, never prompts, model output,
provider response bodies, credentials or campaign mutations.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import re
from time import monotonic
import uuid

from fastapi import HTTPException

from .google_ads_client import problem
from .google_ads_management import ANALYSES, POLICIES

ATTEMPTS = "mezan_google_ads_ai_attempts_v1"
PUBLIC_FIELDS = ("analysis_id", "account_id", "campaign_id", "date_from", "date_to",
                 "created_at", "finished_at", "status", "phase", "duration_ms",
                 "error_code", "http_status", "exception_type", "provider_writes")
logger = logging.getLogger(__name__)


async def run_analysis(db, actor, request, *, reader, analyzer, client_factory, ai_client_factory):
    analysis_id = uuid.uuid4().hex
    created = datetime.now(timezone.utc)
    started = monotonic()
    collection = db[ATTEMPTS]
    scope = {"user_id": actor["user_id"], "analysis_id": analysis_id}
    phase = "native_workspace"
    diagnostic_available = False
    async def initialize_attempt():
        await collection.create_index([("user_id", 1), ("account_id", 1), ("created_at", -1)])
        await collection.create_index("expires_at", expireAfterSeconds=0)
        await collection.insert_one({**scope, "actor_id": actor["actor_id"],
            "account_id": request.account_id, "campaign_id": request.campaign_id,
            "date_from": request.date_from, "date_to": request.date_to,
            "created_at": created.isoformat(), "expires_at": created + timedelta(days=7),
            "status": "running", "phase": phase, "provider_writes": 0})
    try:
        await asyncio.wait_for(initialize_attempt(), timeout=2.0)
        diagnostic_available = True
    except Exception:
        logger.warning("google_ai_attempt_record_unavailable phase=initialize")

    async def update_attempt(values):
        nonlocal diagnostic_available
        if not diagnostic_available:
            return
        try:
            await asyncio.wait_for(collection.update_one(scope, {"$set": values}), timeout=1.0)
        except Exception:
            diagnostic_available = False
            logger.warning("google_ai_attempt_record_unavailable phase=%s", phase)

    async def finish(status, **extra):
        await update_attempt({"status": status, "phase": phase,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": max(0, int((monotonic() - started) * 1000)), **extra})

    async def failed(status, **extra):
        # Diagnostic storage must never replace the original provider error.
        try:
            await finish(status, **extra)
        except Exception:
            logger.warning("google_ai_attempt_record_unavailable phase=%s", phase)

    try:
        workspace = await reader(db, actor, from_date=request.date_from,
            to_date=request.date_to, account_id=request.account_id,
            campaign_id=request.campaign_id, client_factory=client_factory)
        phase = "policy_read"
        await update_attempt({"phase": phase})
        policy = await db[POLICIES].find_one({"user_id": actor["user_id"],
            "account_id": request.account_id, "subject_id": actor["actor_id"],
            "enabled": True}, {"_id": 0}) or {}
        if policy.get("expires_at") and policy["expires_at"] <= datetime.now(timezone.utc).isoformat():
            policy = {}
        from .google_ads_ai_actions import REVIEWABLE_ACTIONS
        permitted = [key for key, action in REVIEWABLE_ACTIONS.items() if
            (actor["owner"] and action in {"pause", "enable"}) or action in policy.get("allowed_actions", [])]
        facts = {**workspace, "date_from": request.date_from, "date_to": request.date_to,
            "campaigns": workspace["campaigns"] if workspace["complete"] else [],
            "permitted_actions": permitted, "limits": policy}
        phase = "structured_analysis"
        await update_attempt({"phase": phase})
        result = await analyzer(db, actor["user_id"], request, facts=facts,
            client_factory=ai_client_factory)
        phase = "save_analysis"
        await update_attempt({"phase": phase})
        saved_at = datetime.now(timezone.utc).isoformat()
        await db[ANALYSES].insert_one({**scope, "actor_id": actor["actor_id"],
            "account_id": request.account_id, "date_from": request.date_from,
            "date_to": request.date_to, "created_at": saved_at, "result": result})
        await finish("complete")
        return {**result, "analysis_id": analysis_id, "created_at": saved_at}
    except HTTPException as exc:
        code = (exc.detail or {}).get("code") if isinstance(exc.detail, dict) else None
        code = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,100}", code) else "google_ai_request_failed"
        logger.warning("google_ai_attempt_failed phase=%s code=%s status=%s", phase, code, exc.status_code)
        await failed("failed", error_code=code, http_status=exc.status_code)
        raise
    except asyncio.CancelledError:
        # A disconnected caller is distinct from a provider/model rejection.
        await failed("cancelled", error_code="google_ai_request_cancelled")
        raise
    except Exception as exc:
        safe_type = type(exc).__name__
        safe_type = safe_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", safe_type) else "other"
        logger.warning("google_ai_attempt_failed phase=%s type=%s", phase, safe_type)
        await failed("failed", error_code="google_ai_analysis_failed", http_status=502,
                     exception_type=safe_type)
        raise problem("google_ai_analysis_failed", "تعذر إكمال تحليل جوجل. لم تتغير الحملات؛ يمكن مراجعة حالة محاولة التحليل.", 502) from None


async def list_attempts(db, user_id, *, account_id, date_from=None, date_to=None):
    scope = {"user_id": user_id, "account_id": account_id}
    if date_from is not None or date_to is not None:
        from .google_ads_workspace import period
        if date_from is None or date_to is None:
            raise problem("google_ai_analysis_period_required", "حدد بداية ونهاية فترة التحليل معًا.", 422)
        scope["date_from"], scope["date_to"] = period(date_from, date_to)
    rows = await db[ATTEMPTS].find(scope, {"_id": 0}).sort("created_at", -1).to_list(length=20)
    return {"items": [{key: row[key] for key in PUBLIC_FIELDS if key in row} for row in rows]}
