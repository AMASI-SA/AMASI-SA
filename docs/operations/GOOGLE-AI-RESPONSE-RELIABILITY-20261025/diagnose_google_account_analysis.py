"""Bounded Google-account analysis diagnosis; no campaign execution or raw data output."""
import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from dotenv import load_dotenv
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path("/app/backend/.env"))
from integrations_control_center.google_ads_ai import GoogleAIAnalysisInput, analyze_google_campaigns
from integrations_control_center.google_ads_workspace import read_google_workspace

ACCOUNT = "5052346701"

async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    phase, started = "scope", monotonic()
    try:
        rows = await db.mezan_integration_accounts_v2.find({
            "provider": "google_ads", "connection_status": "connected",
            "connection_provenance": "api_connection",
            "$or": [{"ad_account_id": ACCOUNT}, {"external_account_id": ACCOUNT}],
        }, {"_id": 0, "user_id": 1}).to_list(length=4)
        owners = {str(row["user_id"]) for row in rows if row.get("user_id")}
        if len(owners) != 1:
            print("diagnostic scope_unproven", flush=True)
            return
        user_id = owners.pop()
        owner = await db.users.find_one({"id": user_id, "role": "owner",
            "disabled": {"$ne": True}, "is_active": {"$ne": False},
            "deleted_at": {"$in": [None, ""]}}, {"_id": 0, "id": 1})
        if not owner:
            print("diagnostic owner_unproven", flush=True)
            return
        actor = {"user_id": user_id, "actor_id": user_id, "owner": True,
                 "can_read": True, "can_draft": False}
        request = GoogleAIAnalysisInput(account_id=ACCOUNT, date_from="2026-10-25", date_to="2026-10-25")
        phase = "native_workspace"
        facts = await asyncio.wait_for(read_google_workspace(db, actor,
            from_date=request.date_from, to_date=request.date_to, account_id=ACCOUNT), timeout=65)
        print("diagnostic native_workspace", "complete", facts.get("complete"),
              "campaigns", len(facts.get("campaigns") or []), "seconds", round(monotonic()-started, 2), flush=True)
        if facts.get("complete") is not True:
            return
        facts = {**facts, "date_from": request.date_from, "date_to": request.date_to,
                 "permitted_actions": ["PAUSE_CAMPAIGN", "RESUME_CAMPAIGN"], "limits": {}}
        phase = "structured_analysis"
        result = await analyze_google_campaigns(db, user_id, request, facts=facts)
        print("diagnostic structured_analysis", "success", "recommendations",
              len(result.get("recommendations") or []), "seconds", round(monotonic()-started, 2), flush=True)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code")
        code = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,100}", code) else "unavailable"
        print("diagnostic failure", "phase", phase, "status", exc.status_code,
              "code", code, "seconds", round(monotonic()-started, 2), flush=True)
    except Exception as exc:
        print("diagnostic exception", "phase", phase, "type", type(exc).__name__,
              "seconds", round(monotonic()-started, 2), flush=True)
    finally:
        client.close()

asyncio.run(asyncio.wait_for(main(), timeout=115))
