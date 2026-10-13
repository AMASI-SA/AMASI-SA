"""Google Ads V2 control plane: human and AI use one approved action gateway."""
from __future__ import annotations

import base64
import io
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .google_ads_campaign_contract import GoogleCampaignDraft, GoogleCampaignActionInput, campaign_capabilities
from .google_ads_client import GoogleAdsClient, identifier, problem
from .google_ads_management import (DRAFTS, PROPOSALS, POLICIES, ANALYSES, account_client, available_accounts, digest,
    ensure_google_management_indexes, execute_google_proposal, get_proposal, now, preview_google_action, principal, public_proposal, reconcile_google_proposal)
from .google_ads_workspace import google_lookup, read_google_workspace

KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ACTION_NAMES = set(GoogleCampaignActionInput.model_fields["action"].annotation.__args__) | {"upload_image", "attach_video"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DraftCreate(Strict):
    draft: GoogleCampaignDraft
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class DraftUpdate(Strict):
    draft: GoogleCampaignDraft
    expected_revision: int = Field(ge=1)


class Approval(Strict):
    confirmation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class VideoAsset(Strict):
    account_id: str = Field(pattern=r"^[0-9]{1,30}$")
    youtube_video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    name: str = Field(default="فيديو إعلان", min_length=1, max_length=100)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class PolicyInput(Strict):
    enabled: bool
    allowed_actions: list[str] = Field(max_length=40)
    max_daily_budget_native: str = Field(pattern=r"^[0-9]{1,12}(\.[0-9]{1,6})?$")
    max_total_budget_native: str = Field(pattern=r"^[0-9]{1,12}(\.[0-9]{1,6})?$")
    expires_at: str | None = None


def public_draft(row):
    return {key: row.get(key) for key in ("draft_id", "revision", "draft", "account_id", "created_at", "updated_at", "actor_id", "source")}


async def save_draft(db, actor, body):
    await ensure_google_management_indexes(db)
    account_id = body.draft.account_id
    if account_id not in {row["account_id"] for row in await available_accounts(db, actor["user_id"])}:
        raise problem("google_ads_account_not_owned", "حساب المسودة غير مرتبط بمتجرك.", 404)
    content = body.draft.model_dump(mode="json")
    content_hash = digest(content)
    existing = await db[DRAFTS].find_one({"user_id": actor["user_id"], "idempotency_key": body.idempotency_key}, {"_id": 0})
    if existing:
        if existing.get("initial_digest") != content_hash:
            raise problem("google_ads_idempotency_conflict", "طلب الحفظ مرتبط بمسودة أخرى.")
        return public_draft(existing)
    row = {"user_id": actor["user_id"], "actor_id": actor["actor_id"], "draft_id": uuid.uuid4().hex, "revision": 1, "account_id": account_id, "draft": content,
        "source": "mezan_typed_draft", "initial_digest": content_hash, "idempotency_key": body.idempotency_key, "created_at": now().isoformat(), "updated_at": now().isoformat()}
    try:
        await db[DRAFTS].insert_one(row)
    except DuplicateKeyError:
        return await save_draft(db, actor, body)
    return public_draft(row)


async def owner(db, user):
    actor = await principal(db, user, write=True)
    if not actor["owner"]:
        raise problem("google_ads_owner_required", "إدارة التفويض متاحة للمالك فقط.", 403)
    return actor


def attach_google_ads_management_routes(router, db, current_user, *, client_factory=GoogleAdsClient, ai_client_factory=None):
    root = "/google_ads"

    async def actor_read(user=Depends(current_user)):
        return await principal(db, user)

    async def actor_write(user=Depends(current_user)):
        return await principal(db, user, write=True)

    async def client_for(actor, account_id):
        return (await account_client(db, actor["user_id"], account_id, client_factory=client_factory))[0]

    @router.get(root + "/workspace")
    async def workspace(from_date: str, to_date: str, account_id: str | None = None, entity_type: str = "campaigns", campaign_id: str | None = None, group_id: str | None = None,
        page: int = Query(1, ge=1, le=100), limit: int = Query(25, ge=1, le=100), campaign_query: str = Query("", max_length=100), actor=Depends(actor_read)):
        return await read_google_workspace(db, actor, from_date=from_date, to_date=to_date, account_id=account_id, entity_type=entity_type, campaign_id=campaign_id, group_id=group_id, page=page, limit=limit, campaign_query=campaign_query, client_factory=client_factory)

    @router.get(root + "/management/schema")
    async def schema(actor=Depends(actor_read)):
        return {"schema": GoogleCampaignDraft.model_json_schema(), "campaign_types": campaign_capabilities(), "actions": sorted(ACTION_NAMES), "defaults": {"budget_mode": "DAILY", "initial_status": "PAUSED"}, "approval_required": True}

    @router.get(root + "/management/readiness")
    async def readiness(account_id: str | None = None, actor=Depends(actor_read)):
        accounts = await available_accounts(db, actor["user_id"])
        goals = []
        for row in accounts:
            if account_id and row["account_id"] != identifier(account_id):
                continue
            client = None
            try:
                client = await client_for(actor, row["account_id"])
                metadata = await client.account_metadata()
                row.update(metadata, currency=metadata["currency_code"], timezone=metadata["time_zone"], readable=True)
                current_goals = await google_lookup(client, "conversion_goals")
                row["conversion_goals"] = current_goals["items"] if current_goals["complete"] else []
                goals += row["conversion_goals"]
                if not actor["owner"]:
                    row["execution_policy"] = await db[POLICIES].find_one({"user_id": actor["user_id"], "account_id": row["account_id"], "subject_id": actor["actor_id"]}, {"_id": 0, "user_id": 0})
            except HTTPException as exc:
                row.update(readable=False, safe_error=exc.detail if isinstance(exc.detail, dict) else {"code": "google_ads_readiness_failed"})
            finally:
                if client:
                    await client.close()
        return {"accounts": accounts, "campaign_types": campaign_capabilities(), "customer_conversion_goals": goals, "permissions": {"owner": actor["owner"], "can_draft": actor["can_draft"], "can_execute": actor["owner"] or any((row.get("execution_policy") or {}).get("enabled") for row in accounts)},
            "management_enabled": any(row.get("readable") for row in accounts), "approval_required": True, "ai_write_tools": False, "source": "google_ads_api"}

    @router.get(root + "/lookup/{kind}")
    async def lookup(kind: str, account_id: str, query: str = Query("", max_length=100), merchant_id: str | None = None, actor=Depends(actor_read)):
        client = await client_for(actor, account_id)
        try:
            return await google_lookup(client, kind, query=query, merchant_id=merchant_id)
        finally:
            await client.close()

    @router.get(root + "/assets")
    async def assets(account_id: str, actor=Depends(actor_read)):
        client = await client_for(actor, account_id)
        try:
            items, complete = await client.assets()
            return {"items": items, "complete": complete, "source": "google_ads_api"}
        finally:
            await client.close()

    @router.post(root + "/assets")
    async def image_asset(account_id: str = Form(...), idempotency_key: str = Form(...), name: str = Form("صورة إعلان"), file: UploadFile = File(...), actor=Depends(actor_write)):
        from PIL import Image, UnidentifiedImageError
        account_id = identifier(account_id)
        if not KEY.fullmatch(idempotency_key) or not 1 <= len(name.strip()) <= 100:
            raise problem("google_ads_asset_input_invalid", "اسم المادة أو هوية الطلب غير صالح.", 422)
        raw = await file.read(5 * 1024 * 1024 + 1)
        if not raw or len(raw) > 5 * 1024 * 1024:
            raise problem("google_ads_image_size", "حجم الصورة يجب ألا يتجاوز 5 ميغابايت.", 422)
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"JPEG", "PNG", "GIF"} or image.width * image.height > 25_000_000:
                    raise ValueError()
                width, height = image.size
                image.verify()
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise problem("google_ads_image_invalid", "ارفع صورة JPEG أو PNG أو GIF صالحة.", 422) from None
        prepared = {"operations": [{"assetOperation": {"create": {"name": name.strip(), "imageAsset": {"data": base64.b64encode(raw).decode(), "fileSize": str(len(raw)), "fullSize": {"widthPixels": width, "heightPixels": height}}}}}],
            "summary": {"name": name.strip(), "type": "IMAGE", "dimensions": {"width": width, "height": height}, "bytes": len(raw), "content_sha256": digest(base64.b64encode(raw).decode())}}
        return await preview_google_action(db, actor, {"action": "upload_image", "account_id": account_id, "idempotency_key": idempotency_key}, client_factory=client_factory, prepared_asset=prepared)

    @router.post(root + "/assets/video")
    async def video_asset(body: VideoAsset, actor=Depends(actor_write)):
        prepared = {"operations": [{"assetOperation": {"create": {"name": body.name, "youtubeVideoAsset": {"youtubeVideoId": body.youtube_video_id}}}}], "summary": {"name": body.name, "type": "YOUTUBE_VIDEO", "youtube_video_id": body.youtube_video_id}}
        return await preview_google_action(db, actor, {"action": "attach_video", "account_id": body.account_id, "idempotency_key": body.idempotency_key}, client_factory=client_factory, prepared_asset=prepared)

    @router.get(root + "/management/drafts")
    async def drafts(account_id: str | None = None, limit: int = Query(25, ge=1, le=100), actor=Depends(actor_read)):
        scope = {"user_id": actor["user_id"]}
        if account_id:
            scope["account_id"] = identifier(account_id)
        rows = await db[DRAFTS].find(scope, {"_id": 0}).sort("updated_at", -1).to_list(length=limit)
        return {"items": [public_draft(row) for row in rows]}

    @router.post(root + "/management/drafts")
    async def draft_create(body: DraftCreate, actor=Depends(actor_write)):
        return await save_draft(db, actor, body)

    @router.get(root + "/management/drafts/{draft_id}")
    async def draft_get(draft_id: str, actor=Depends(actor_read)):
        row = await db[DRAFTS].find_one({"user_id": actor["user_id"], "draft_id": draft_id}, {"_id": 0})
        if not row:
            raise problem("google_ads_draft_not_found", "المسودة غير موجودة.", 404)
        return public_draft(row)

    @router.put(root + "/management/drafts/{draft_id}")
    async def draft_update(draft_id: str, body: DraftUpdate, actor=Depends(actor_write)):
        row = await db[DRAFTS].find_one_and_update({"user_id": actor["user_id"], "draft_id": draft_id, "account_id": body.draft.account_id, "revision": body.expected_revision},
            {"$set": {"draft": body.draft.model_dump(mode="json"), "updated_at": now().isoformat(), "actor_id": actor["actor_id"]}, "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER)
        if not row:
            raise problem("google_ads_draft_stale", "المسودة تغيرت؛ افتح النسخة الأحدث قبل التعديل.")
        return public_draft(row)

    @router.post(root + "/management/proposals")
    async def proposal(body: GoogleCampaignActionInput, actor=Depends(actor_write)):
        if not body.idempotency_key:
            raise problem("google_ads_idempotency_required", "هوية الطلب مطلوبة لمنع تكرار العملية.", 422)
        return await preview_google_action(db, actor, body, client_factory=client_factory)

    @router.get(root + "/management/proposals")
    async def proposals(account_id: str | None = None, campaign_id: str | None = None, limit: int = Query(25, ge=1, le=100), actor=Depends(actor_read)):
        scope = {"user_id": actor["user_id"]}
        if account_id:
            scope["account_id"] = identifier(account_id)
        if campaign_id:
            scope["campaign_id"] = identifier(campaign_id)
        rows = await db[PROPOSALS].find(scope, {"_id": 0, "operations": 0, "before_snapshot": 0, "auth_epoch": 0}).sort("created_at", -1).to_list(length=limit)
        return {"items": [public_proposal(row) for row in rows]}

    @router.get(root + "/management/proposals/{proposal_id}")
    async def proposal_get(proposal_id: str, actor=Depends(actor_read)):
        return public_proposal(await get_proposal(db, actor["user_id"], proposal_id))

    @router.post(root + "/management/proposals/{proposal_id}/approve-and-execute")
    async def execute(proposal_id: str, body: Approval, actor=Depends(actor_write)):
        return await execute_google_proposal(db, actor, proposal_id, body.confirmation_digest, client_factory=client_factory)

    @router.post(root + "/management/proposals/{proposal_id}/reconcile")
    async def reconcile(proposal_id: str, actor=Depends(actor_read)):
        return await reconcile_google_proposal(db, actor, proposal_id, client_factory=client_factory)

    @router.get(root + "/management/subjects")
    async def subjects(user=Depends(current_user)):
        actor = await owner(db, user)
        rows = await db.users.find({"created_by": actor["user_id"], "disabled": {"$ne": True}, "is_active": {"$ne": False}}, {"_id": 0, "id": 1, "name": 1, "email": 1}).to_list(length=100)
        return {"items": [{"subject_id": str(row["id"]), "name": row.get("name"), "email": row.get("email")} for row in rows]}

    async def policy_scope(actor, account_id, subject_id):
        account_id = identifier(account_id)
        if account_id not in {row["account_id"] for row in await available_accounts(db, actor["user_id"])} or not await db.users.find_one({"id": subject_id, "created_by": actor["user_id"]}, {"_id": 0, "id": 1}):
            raise problem("google_ads_policy_scope_invalid", "التفويض يجب أن يخص حساب المتجر وأحد أعضاء فريقه.", 404)
        return {"user_id": actor["user_id"], "account_id": account_id, "subject_id": subject_id}

    @router.get(root + "/management/policies/{account_id}/{subject_id}")
    async def policy_get(account_id: str, subject_id: str, user=Depends(current_user)):
        actor = await owner(db, user)
        scope = await policy_scope(actor, account_id, subject_id)
        return await db[POLICIES].find_one(scope, {"_id": 0, "user_id": 0}) or {"account_id": scope["account_id"], "subject_id": subject_id, "enabled": False, "allowed_actions": [], "max_daily_budget_native": "0", "max_total_budget_native": "0"}

    @router.put(root + "/management/policies/{account_id}/{subject_id}")
    async def policy_set(account_id: str, subject_id: str, body: PolicyInput, user=Depends(current_user)):
        actor = await owner(db, user)
        scope = await policy_scope(actor, account_id, subject_id)
        if set(body.allowed_actions) - ACTION_NAMES or len(set(body.allowed_actions)) != len(body.allowed_actions):
            raise problem("google_ads_policy_actions_invalid", "إجراءات التفويض غير صالحة.", 422)
        payload = body.model_dump()
        if body.expires_at:
            try:
                expiry = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
                if expiry.tzinfo is None or expiry <= now():
                    raise ValueError()
                payload["expires_at"] = expiry.astimezone(timezone.utc).isoformat()
            except ValueError:
                raise problem("google_ads_policy_expiry_invalid", "اختر موعد انتهاء مستقبليًا مع المنطقة الزمنية.", 422) from None
        await ensure_google_management_indexes(db)
        await db[POLICIES].update_one(scope, {"$set": {**scope, **payload, "updated_by": actor["actor_id"], "updated_at": now().isoformat()}}, upsert=True)
        return {**payload, "account_id": scope["account_id"], "subject_id": subject_id}

    @router.post(root + "/ai/draft")
    async def ai_draft(body: dict, actor=Depends(actor_write)):
        from .google_ads_ai import GoogleAIDraftInput, generate_google_campaign_draft
        from pydantic import ValidationError
        try:
            request = GoogleAIDraftInput.model_validate(body)
        except ValidationError:
            raise problem("google_ai_input_invalid", "راجع طلب الحملة وسقف الميزانية.", 422) from None
        client = await client_for(actor, request.account_id)
        try:
            context = await client.account_metadata()
            context["supported_campaign_types"] = [row["type"] for row in campaign_capabilities() if row.get("create")]
            assets, complete = await client.assets()
            context["asset_inventory"] = assets if complete else []
            for kind, key in (("languages", "language_targets"), ("audiences", "audiences"), ("user_lists", "user_lists"), ("conversion_actions", "conversion_actions"), ("conversion_goals", "conversion_goals"), ("merchants", "merchant_ids"), ("apps", "apps"), ("hotels", "hotel_center_ids")):
                lookup_result = await google_lookup(client, kind)
                values = lookup_result["items"] if lookup_result["complete"] else []
                context[key] = [row["id"] for row in values] if key.endswith("_ids") else values
            result = await generate_google_campaign_draft(db, actor["user_id"], request, client_factory=ai_client_factory, account_context=context)
            return result
        finally:
            await client.close()

    @router.post(root + "/ai/analyze")
    async def ai_analysis(body: dict, actor=Depends(actor_read)):
        from .google_ads_ai import GoogleAIAnalysisInput, analyze_google_campaigns
        from pydantic import ValidationError
        payload = dict(body)
        for old, new in (("from_date", "date_from"), ("to_date", "date_to")):
            if old in payload:
                payload[new] = payload.pop(old)
        try:
            request = GoogleAIAnalysisInput.model_validate(payload)
        except ValidationError:
            raise problem("google_ai_input_invalid", "راجع الحساب والفترة المطلوب تحليلها.", 422) from None
        workspace_result = await read_google_workspace(db, actor, from_date=request.date_from, to_date=request.date_to, account_id=request.account_id, campaign_id=request.campaign_id, client_factory=client_factory)
        policy = await db[POLICIES].find_one({"user_id": actor["user_id"], "account_id": request.account_id, "subject_id": actor["actor_id"], "enabled": True}, {"_id": 0}) or {}
        facts = {**workspace_result, "date_from": request.date_from, "date_to": request.date_to, "campaigns": workspace_result["campaigns"] if workspace_result["complete"] else [],
            "permitted_actions": ["PAUSE_CAMPAIGN", "RESUME_CAMPAIGN"] if actor["owner"] else [], "limits": policy}
        result = await analyze_google_campaigns(db, actor["user_id"], request, facts=facts, client_factory=ai_client_factory)
        await db[ANALYSES].insert_one({"analysis_id": uuid.uuid4().hex, "user_id": actor["user_id"], "actor_id": actor["actor_id"], "account_id": request.account_id, "date_from": request.date_from, "date_to": request.date_to, "created_at": now().isoformat(), "result": result})
        return result

    @router.get(root + "/ai/analyses")
    async def analyses(account_id: str | None = None, actor=Depends(actor_read)):
        scope = {"user_id": actor["user_id"]}
        if account_id:
            scope["account_id"] = identifier(account_id)
        rows = await db[ANALYSES].find(scope, {"_id": 0}).sort("created_at", -1).to_list(length=20)
        return {"items": rows}
