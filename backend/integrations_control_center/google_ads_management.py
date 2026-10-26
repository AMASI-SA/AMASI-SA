"""One tenant-scoped draft/approval/execute gateway for Google in Mezan.

Manual marketers and AI submit the same typed drafts. Models never receive a
provider write tool. Durable fences survive an unknown provider outcome; only
read-based reconciliation releases them. ADR-001 1/4/8/10/11/14; ADR-002.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
import uuid

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from .google_ads_client import GoogleAdsClient, identifier, mutation_resource_names, problem
from .google_ads_reporting import _accounts, _credential, GoogleAdsReportingError
from .google_oauth_security import GOOGLE_CREDENTIALS_COLLECTION

DRAFTS = "mezan_google_ads_drafts_v1"
PROPOSALS = "mezan_google_ads_proposals_v1"
FENCES = "mezan_google_ads_execution_fences_v1"
POLICIES = "mezan_google_ads_execution_policies_v1"
AUDIT = "mezan_google_ads_action_audit_v1"
ANALYSES = "mezan_google_ads_ai_analyses_v1"
MAX_PLAN_BYTES = 9 * 1024 * 1024
PROPOSAL_TTL = timedelta(minutes=30)
PUBLIC_FIELDS = ("proposal_id", "provider", "action", "account_id", "account_name", "currency", "time_zone", "campaign_id", "draft_id", "draft_revision", "reason", "before", "planned", "financial_bound", "confirmation_digest", "status", "created_at", "expires_at", "submitted_at", "executed_at", "verified", "provider_write_reached", "submission_started", "resource_names", "created_campaign_id", "safe_error", "after", "verification", "reconcile_after", "events", "actor_id", "local_finalization_pending", "ai_source", "automation_source")


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str).encode()).hexdigest()


def public_proposal(row):
    return {key: row.get(key) for key in PUBLIC_FIELDS if key in row}


async def ensure_google_management_indexes(db):
    for collection, keys, name in (
        (DRAFTS, [("user_id", 1), ("draft_id", 1)], "google_draft_identity"),
        (DRAFTS, [("user_id", 1), ("idempotency_key", 1)], "google_draft_idempotency"),
        (PROPOSALS, [("user_id", 1), ("proposal_id", 1)], "google_proposal_identity"),
        (PROPOSALS, [("user_id", 1), ("idempotency_key", 1)], "google_proposal_idempotency"),
        (FENCES, [("user_id", 1), ("account_id", 1)], "google_account_fence"),
        (POLICIES, [("user_id", 1), ("account_id", 1), ("subject_id", 1)], "google_subject_account_policy"),
    ):
        await db[collection].create_index(keys, unique=True, name=name)
    # An uncertain submission is deliberately retained without a TTL index.
    await db[PROPOSALS].create_index([("user_id", 1), ("created_at", -1)])
    await db[AUDIT].create_index([("user_id", 1), ("proposal_id", 1), ("occurred_at", 1)])


async def principal(db, user, *, write=False):
    """Reuse existing web ads permissions; derive a staff tenant server-side."""
    if not isinstance(user, dict) or not user.get("id"):
        raise problem("google_ads_authentication_required", "سجّل الدخول إلى ميزان.", 401)
    from auth import account_is_disabled
    if account_is_disabled(user):
        raise problem("google_ads_actor_disabled", "الحساب موقوف.", 403)
    actor_id = str(user["id"])
    if str(user.get("role") or "").lower() == "owner":
        return {"user_id": actor_id, "actor_id": actor_id, "owner": True, "can_read": True, "can_draft": True}
    member = await db.users.find_one({"id": actor_id}, {"_id": 0, "id": 1, "role": 1, "created_by": 1, "disabled": 1, "is_active": 1, "deleted_at": 1, "extra_permissions": 1, "denied_permissions": 1})
    if not member or account_is_disabled(member) or not member.get("created_by"):
        raise problem("google_ads_tenant_unproved", "تعذر إثبات عضوية المسوّق في المتجر.", 403)
    owner_id = str(member["created_by"])
    owner = await db.users.find_one({"id": owner_id, "role": "owner", "disabled": {"$ne": True}, "is_active": {"$ne": False}, "deleted_at": {"$in": [None, ""]}}, {"_id": 0, "id": 1})
    if not owner:
        raise problem("google_ads_tenant_unproved", "مالك متجر المسوّق غير متاح.", 403)
    permissions = set(member.get("extra_permissions") or [])
    role = str(member.get("role") or "").lower()
    if role == "admin":
        permissions |= {"ads.view", "ads.manage"}
    elif role in {"accountant", "viewer"}:
        permissions.add("ads.view")
    permissions -= set(member.get("denied_permissions") or [])
    required = "ads.manage" if write else "ads.view"
    if required not in permissions:
        raise problem("google_ads_permission_denied", "لا تملك صلاحية إدارة الإعلانات." if write else "لا تملك صلاحية عرض الإعلانات.", 403)
    return {"user_id": owner_id, "actor_id": actor_id, "owner": False, "can_read": True, "can_draft": "ads.manage" in permissions}


async def execution_permission(db, actor, account_id, action, *, budget=None, budget_mode="DAILY"):
    if actor["owner"]:
        return {"can_execute": True, "owner": True}
    policy = await db[POLICIES].find_one({"user_id": actor["user_id"], "account_id": account_id, "subject_id": actor["actor_id"], "enabled": True}, {"_id": 0})
    if not policy or action not in (policy.get("allowed_actions") or []) or policy.get("expires_at") and policy["expires_at"] <= now().isoformat():
        raise problem("google_ads_owner_delegation_required", "تحتاج تفويض المالك لهذا الحساب والإجراء قبل التنفيذ.", 403)
    if budget is not None:
        ceiling = policy.get("max_total_budget_native" if budget_mode == "TOTAL" else "max_daily_budget_native")
        if not ceiling or Decimal(str(budget)) > Decimal(str(ceiling)):
            raise problem("google_ads_delegated_budget_exceeded", "ميزانية العملية تتجاوز تفويض المالك بعملة الحساب.", 403)
    return policy


async def available_accounts(db, user_id):
    try:
        rows = await _accounts(db, user_id)
    except GoogleAdsReportingError as exc:
        if exc.code == "google_ads_accounts_missing":
            return []
        raise problem(exc.code, "تعذر قراءة الحسابات المرتبطة بجوجل.", exc.status_code) from None
    return [{"account_id": row["ad_account_id"], "name": row.get("display_name") or row["ad_account_id"], "currency": row.get("currency"), "timezone": row.get("timezone"), "selected": row.get("mezan_selected") is True} for row in rows]


async def account_client(db, user_id, account_id, *, client_factory=GoogleAdsClient):
    account_id = identifier(account_id)
    row = await db.mezan_integration_accounts_v2.find_one({"user_id": user_id, "provider": "google_ads", "connection_status": "connected", "connection_provenance": "api_connection", "$or": [{"ad_account_id": account_id}, {"external_account_id": account_id}]}, {"_id": 0, "ad_account_id": 1, "external_account_id": 1, "display_name": 1, "currency": 1, "timezone": 1, "login_customer_id": 1})
    connection = await db.mezan_integrations_v2.find_one({"user_id": user_id, "provider": "google_ads", "connection_status": "connected", "connection_provenance": "api_connection"}, {"_id": 0, "provider": 1})
    if not row or not connection:
        raise problem("google_ads_account_not_owned", "الحساب غير متاح ضمن ربط متجرك.", 404)
    try:
        token = await _credential(db, user_id, now())
    except GoogleAdsReportingError as exc:
        raise problem(exc.code, "تحتاج إعادة التوثيق أو مراجعة اتصال جوجل.", exc.status_code) from None
    credential = await db[GOOGLE_CREDENTIALS_COLLECTION].find_one({"user_id": user_id}, {"_id": 0, "refresh_token_ciphertext": 1, "scope": 1, "google_subject": 1})
    # Access-token refresh does not invalidate an approval. Reauthorization,
    # a changed refresh credential, subject or granted scopes does.
    epoch = digest(credential or {})
    return client_factory(token, {**row, "ad_account_id": account_id}), epoch


def human_snapshot(snapshot):
    campaign, budget = snapshot.get("campaign") or {}, snapshot.get("campaign_budget") or {}
    amount = budget.get("totalAmountMicros") if budget.get("period") == "CUSTOM_PERIOD" else budget.get("amountMicros")
    return {"campaign_id": str(campaign.get("id") or ""), "name": campaign.get("name"), "campaign_type": "APP" if campaign.get("advertisingChannelType") == "MULTI_CHANNEL" else campaign.get("advertisingChannelType"),
        "status": campaign.get("status"), "budget_native": str(Decimal(str(amount or "0")) / 1000000),
        "budget_mode": "TOTAL" if budget.get("period") == "CUSTOM_PERIOD" else "DAILY",
        "bidding": {"strategy": campaign.get("biddingStrategyType"), "maximize_conversions": campaign.get("maximizeConversions"), "maximize_conversion_value": campaign.get("maximizeConversionValue"), "target_cpa": campaign.get("targetCpa"), "target_roas": campaign.get("targetRoas")},
        "groups_count": len(snapshot.get("groups") or []), "ads_count": len(snapshot.get("ads") or [])}


def immutable(row):
    keys = ("proposal_id", "provider", "user_id", "actor_id", "action", "account_id", "currency", "time_zone", "auth_epoch", "before_digest", "draft_id", "draft_revision", "operations", "reason", "planned", "financial_bound", "expires_at", "correlation_label")
    # Preserve approval digests for existing manual proposals.
    return {**{key: row.get(key) for key in keys}, **({"ai_source": row["ai_source"]} if "ai_source" in row else {}), **({"automation_source": row["automation_source"]} if "automation_source" in row else {})}


async def record_event(db, row, status, *, expected_status=None, expected_submission_started=False, audit=True, **updates):
    event = {"id": uuid.uuid4().hex, "user_id": row["user_id"], "proposal_id": row["proposal_id"], "actor_id": row["actor_id"], "action": row["action"], "status": status, "occurred_at": now().isoformat(), "account_id": row["account_id"]}
    scope = {"user_id": row["user_id"], "proposal_id": row["proposal_id"]}
    if row.get("automation_source"):
        scope["account_id"] = row["account_id"]
    if expected_status is not None:
        scope.update(status=expected_status, submission_started=expected_submission_started)
        if row.get("execution_claim_id"):
            scope["execution_claim_id"] = row["execution_claim_id"]
    elif status != "completed":
        scope["status"] = {"$ne": "completed"}
    changed = await db[PROPOSALS].update_one(scope, {"$set": {"status": status, "updated_at": event["occurred_at"], **updates}, "$push": {"events": {"$each": [{"status": status, "at": event["occurred_at"]}], "$slice": -100}}})
    if not changed.matched_count:
        raise problem("google_ads_execution_phase_changed", "حالة العملية تغيرت؛ لن يُعاد الإرسال.")
    row.update(status=status, **updates)
    if audit:
        await db[AUDIT].insert_one(event)


async def get_proposal(db, user_id, proposal_id):
    row = await db[PROPOSALS].find_one({"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0})
    if not row:
        raise problem("google_ads_proposal_not_found", "الاقتراح غير موجود في متجرك.", 404)
    return row


async def _release_fence(db, row):
    scope = {"user_id": row["user_id"], "account_id": row["account_id"], "proposal_id": row["proposal_id"]}
    if row.get("execution_claim_id"):
        scope["execution_claim_id"] = row["execution_claim_id"]
    await db[FENCES].update_one(scope, {"$set": {"status": "released", "released_at": now().isoformat()}})


async def preview_google_action(db, actor, body, *, client_factory=GoogleAdsClient, prepared_asset=None, ai_source=None):
    if actor.get("actor_id") == "mezan_google_automation_v1":
        raise problem("google_automation_gateway_required", "استخدم بوابة التفويض الآلي المستقلة.", 403)
    return await _preview_google_action(db, actor, body, client_factory=client_factory, prepared_asset=prepared_asset, ai_source=ai_source)


async def _preview_google_action(db, actor, body, *, client_factory=GoogleAdsClient, prepared_asset=None, ai_source=None, automation_source=None):
    if (automation_source is not None) != (actor.get("actor_id") == "mezan_google_automation_v1"):
        raise problem("google_automation_gateway_required", "مسار المعاينة غير مطابق للتفويض.", 403)
    from .google_ads_campaign_builder import GoogleCampaignBuildError, build_campaign_operations, build_update_operations
    from .google_ads_campaign_contract import GoogleCampaignDraft
    await ensure_google_management_indexes(db)
    payload = body.model_dump() if hasattr(body, "model_dump") else dict(body)
    action = payload["action"]
    action = "create_campaign" if action == "create" else action
    account_id = identifier(payload["account_id"])
    request_hash = digest({**payload, "prepared_asset": prepared_asset, **({"ai_source": ai_source} if ai_source else {}), **({"automation_source": automation_source} if automation_source is not None else {})})
    prior = await db[PROPOSALS].find_one({"user_id": actor["user_id"], "idempotency_key": payload["idempotency_key"]}, {"_id": 0})
    if prior:
        if prior.get("input_digest") != request_hash:
            raise problem("google_ads_idempotency_conflict", "طلب الحفظ السابق مرتبط بإعدادات مختلفة؛ راجع الاقتراح الحالي.")
        return public_proposal(prior)
    client, epoch = await account_client(db, actor["user_id"], account_id, client_factory=client_factory)
    proposal_id = uuid.uuid4().hex
    snapshot, draft_row, correlation = {}, None, None
    try:
        metadata = await client.account_metadata()
        if ai_source and (ai_source["account_id"] != account_id or ai_source["campaign_id"] != payload.get("campaign_id")
            or ai_source["currency"] != metadata["currency_code"] or ai_source["time_zone"] != metadata["time_zone"]):
            raise problem("google_ai_recommendation_account_changed", "تغيرت عملة الحساب أو توقيته؛ أعد تحليل الحملة.")
        if prepared_asset:
            operations = prepared_asset["operations"]
            planned = prepared_asset["summary"]
            financial = None
        elif action in {"create_campaign", "duplicate"}:
            if action == "duplicate":
                from .google_ads_creative_projection import duplicate_campaign_draft
                snapshot = await client.campaign_snapshot(payload["campaign_id"], children=True, duplicate_controls=True)
                draft = duplicate_campaign_draft(snapshot, account_id, (payload.get("changes") or {}).get("name"))
            else:
                draft_row = await db[DRAFTS].find_one({"user_id": actor["user_id"], "draft_id": payload.get("draft_id"), "revision": payload.get("draft_revision")}, {"_id": 0})
                if not draft_row or draft_row["account_id"] != account_id:
                    raise problem("google_ads_draft_stale", "المسودة غير موجودة أو تغيرت بعد فتحها؛ حدّثها قبل المعاينة.")
                draft = GoogleCampaignDraft.model_validate(draft_row["draft"])
            trusted_products = []
            verified_offers = []
            if draft.product_ids:
                from .google_ads_ai import _products
                trusted_products, missing_products = await _products(db, actor["user_id"], draft.product_ids)
                if missing_products:
                    raise problem("google_ads_product_intent_unavailable", "منتجات الحملة غير مثبتة ضمن كتالوج متجرك؛ حدّث الاختيار.", 422)
                if draft.shopping:
                    mappings = draft.product_offer_selections
                    expected = {selection.offer_id for selection in mappings}
                    selected = {offer for group in draft.groups for offer in group.product_offer_ids}
                    if {selection.product_id for selection in mappings} != set(draft.product_ids) or any(selection.merchant_id != draft.shopping.merchant_id for selection in mappings) or selected != expected or any(not group.product_offer_ids for group in draft.groups):
                        raise problem("google_ads_product_feed_scope_unproved", "حدد منتج جوجل المقابل لكل منتج ميزان، وحصر منتجات كل مجموعة قبل المعاينة.", 422)
                    from .google_ads_workspace import google_lookup
                    merchants = await google_lookup(client, "merchants")
                    if not merchants["complete"] or draft.shopping.merchant_id not in {row.get("merchant_id") for row in merchants["items"]}:
                        raise problem("google_ads_product_merchant_unproved", "لم يثبت ربط Merchant Center لهذا الحساب.", 422)
                    inventory = await google_lookup(client, "products", merchant_id=draft.shopping.merchant_id)
                    eligible = {row.get("offer_id") for row in inventory["items"] if row.get("approved") is True and row.get("availability") != "OUT_OF_STOCK"}
                    if not inventory["complete"] or expected - eligible:
                        raise problem("google_ads_product_offer_not_eligible", "قائمة منتجات جوجل المختارة غير مثبتة أو غير مؤهّلة بالكامل.", 422)
                    verified_offers = [{"offer_id": offer["offer_id"], "merchant_id": draft.shopping.merchant_id, "name": offer.get("title") or offer.get("name") or "منتج جوجل"} for offer in inventory["items"] if offer["offer_id"] in expected]
            operations = build_campaign_operations(draft, account_id)
            planned = draft.model_dump(mode="json")
            if draft.campaign_type == "SEARCH":
                planned["language_ids"] = []
            planned["products"] = [{"product_id": product["product_id"], "name": product["name"]} for product in trusted_products]
            planned["product_offers"] = verified_offers
            planned["initial_status"] = "PAUSED"
            financial = {"budget_native": draft.budget_native, "budget_mode": draft.budget_mode, "average_daily_not_hard_cap": draft.budget_mode == "DAILY"}
            correlation = f"MEZAN-{proposal_id}"
            temporary_campaign = next(op["campaignOperation"]["create"]["resourceName"] for op in operations if "campaignOperation" in op and "create" in op["campaignOperation"])
            label_resource = f"customers/{account_id}/labels/-9000000000000"
            operations += [{"labelOperation": {"create": {"resourceName": label_resource, "name": correlation}}}, {"campaignLabelOperation": {"create": {"campaign": temporary_campaign, "label": label_resource}}}]
        else:
            snapshot = await client.campaign_snapshot(payload["campaign_id"], children=True)
            operations = build_update_operations(action, account_id, snapshot, payload.get("changes") or {})
            planned = {"action": action, **(payload.get("changes") or {})}
            before = human_snapshot(snapshot)
            budget = (payload.get("changes") or {}).get("budget_native", before["budget_native"])
            mode = (payload.get("changes") or {}).get("budget_mode", before["budget_mode"])
            delivery_actions = {"enable", "enable_group", "enable_ad", "set_budget", "set_bidding", "set_group_bidding", "add_group", "add_ad", "update_ad", "update_group_assets", "set_targeting", "set_conversion_goals", "set_keywords", "set_channels"}
            if action in delivery_actions and budget is None:
                raise problem("google_ads_budget_readback_required", "تعذر إثبات ميزانية الحملة التي ستؤثر فيها هذه العملية.", 409)
            financial = {"budget_native": budget, "budget_mode": mode, "average_daily_not_hard_cap": mode == "DAILY"} if action in delivery_actions else None
        if len(json.dumps(operations, ensure_ascii=False, allow_nan=False).encode()) > MAX_PLAN_BYTES:
            raise problem("google_ads_plan_limit", "حجم مواد الحملة يتجاوز الحد المتاح للطلب.", 422)
        # Account-global assets receive a unique correlation name. This allows
        # read-only recovery after a timeout without changing campaign names.
        correlation = correlation or f"MEZAN-{proposal_id}"
        for operation in operations:
            asset = (operation.get("assetOperation") or {}).get("create")
            if asset is not None:
                asset["name"] = ((asset.get("name") or "مادة إعلان")[:100] + " " + correlation)
        if snapshot and action != "duplicate" and any("create" in next(iter(op.values())) for op in operations):
            label_resource = f"customers/{account_id}/labels/-9000000000000"
            operations += [{"labelOperation": {"create": {"resourceName": label_resource, "name": correlation}}},
                {"campaignLabelOperation": {"create": {"campaign": snapshot["campaign"]["resourceName"], "label": label_resource}}}]
        # Preflight is a provider validation only, not a write or an approval.
        await client.mutate(operations, validate_only=True)
        instant = now()
        row = {"user_id": actor["user_id"], "actor_id": actor["actor_id"], "proposal_id": proposal_id, "provider": "google_ads", "action": action, "account_id": account_id,
            "account_name": metadata["name"], "currency": metadata["currency_code"], "time_zone": metadata["time_zone"], "auth_epoch": epoch,
            "campaign_id": payload.get("campaign_id"), "draft_id": draft_row.get("draft_id") if draft_row else None, "draft_revision": draft_row.get("revision") if draft_row else None,
            "reason": payload.get("reason") or "إضافة مادة إعلان إلى الحساب المحدد", "before": human_snapshot(snapshot) if snapshot else {}, "before_snapshot": snapshot,
            "before_digest": digest(snapshot) if snapshot else None, "planned": planned, "financial_bound": financial, "operations": operations, "correlation_label": correlation,
            "created_at": instant.isoformat(), "expires_at": (instant + PROPOSAL_TTL).isoformat(), "status": "previewed", "verified": False, "provider_write_reached": False,
            "submission_started": False, "input_digest": request_hash, "idempotency_key": payload["idempotency_key"], "events": []}
        if automation_source is not None:
            row["automation_source"] = automation_source
        if ai_source:
            row["ai_source"] = ai_source
            row["expires_at"] = min(row["expires_at"], ai_source["expires_at"])
            if row["expires_at"] <= instant.isoformat():
                raise problem("google_ai_analysis_expired", "انتهت صلاحية أدلة التوصية؛ أعد تحليل الحملة.")
        row["confirmation_digest"] = digest(immutable(row))
        try:
            await db[PROPOSALS].insert_one(row)
        except DuplicateKeyError:
            existing = await db[PROPOSALS].find_one({"user_id": actor["user_id"], "idempotency_key": payload["idempotency_key"]}, {"_id": 0})
            if not existing or existing["input_digest"] != request_hash:
                raise problem("google_ads_idempotency_conflict", "تعارض طلب إنشاء الاقتراح.") from None
            return public_proposal(existing)
        await record_event(db, row, "previewed")
        return public_proposal(row)
    except GoogleCampaignBuildError as exc:
        raise problem(exc.code, "المسودة تحتاج إكمالًا أو تعديل الإعدادات قبل إرسالها إلى جوجل.", 422, missing_requirements=exc.details) from None
    finally:
        await client.close()


async def _claim_fence(db, row):
    scope = {"user_id": row["user_id"], "account_id": row["account_id"]}
    claim = {"status": "claimed", "proposal_id": row["proposal_id"], "execution_claim_id": row["execution_claim_id"], "claimed_at": now().isoformat()}
    result = await db[FENCES].update_one({**scope, "status": "released"}, {"$set": claim})
    if result.matched_count:
        return
    try:
        await db[FENCES].insert_one({**scope, **claim})
    except DuplicateKeyError:
        raise problem("google_ads_account_execution_pending", "توجد عملية جوجل معلّقة لهذا الحساب؛ تحقق منها قبل إرسال تغيير آخر.") from None


async def execute_google_proposal(db, actor, proposal_id, confirmation_digest, *, client_factory=GoogleAdsClient):
    if actor.get("actor_id") == "mezan_google_automation_v1":
        raise problem("google_automation_gateway_required", "استخدم بوابة التفويض الآلي المستقلة.", 403)
    return await _execute_google_proposal(db, actor, proposal_id, confirmation_digest, client_factory=client_factory)


async def _execute_google_proposal(db, actor, proposal_id, confirmation_digest, *, client_factory=GoogleAdsClient, automation=False):
    row = await get_proposal(db, actor["user_id"], proposal_id)
    if bool(row.get("automation_source")) != automation or (automation and actor.get("actor_id") != "mezan_google_automation_v1"):
        raise problem("google_automation_gateway_required", "مسار تفويض العملية غير مطابق.", 403)
    if row.get("confirmation_digest") != confirmation_digest or digest(immutable(row)) != confirmation_digest:
        raise problem("google_ads_approval_changed", "الإعدادات المعروضة تغيرت؛ أنشئ معاينة جديدة.")
    if row["status"] == "completed" and row.get("verified"):
        if not automation:
            await _release_fence(db, row)
        return public_proposal(row)
    if row.get("ai_source"):
        from .google_ads_ai_actions import ai_action_review_enabled
        if not ai_action_review_enabled():
            raise problem("google_ai_action_review_disabled", "تنفيذ توصيات جوجل موقوف مؤقتًا.", 503)
    if row["status"] != "previewed" or row.get("submission_started"):
        raise problem("google_ads_execution_already_started", "العملية بدأت سابقًا؛ اقرأ نتيجتها أو اطلب التحقق دون إعادة الإرسال.")
    if row["expires_at"] <= now().isoformat():
        raise problem("google_ads_approval_expired", "انتهت صلاحية المعاينة؛ حدّث إعدادات الحملة قبل اعتمادها.")
    if row.get("draft_id"):
        latest = await db[DRAFTS].find_one({"user_id": actor["user_id"], "draft_id": row["draft_id"]}, {"_id": 0, "revision": 1})
        if not latest or latest["revision"] != row["draft_revision"]:
            raise problem("google_ads_draft_stale", "المسودة تغيرت منذ المعاينة.")
    bound = row.get("financial_bound") or {}
    automation_gateway = None
    if automation:
        kind = (row.get("automation_source") or {}).get("kind")
        if kind == "standing_policy":
            from . import google_ads_automation_gateway as automation_gateway
        elif kind == "standing_creation":
            from . import google_ads_automation_creation_gateway as automation_gateway
        else:
            raise problem("google_automation_gateway_required", "نوع التفويض الآلي غير متاح.", 403)
        await automation_gateway._execution_preflight(db, row)
    else:
        await execution_permission(db, actor, row["account_id"], row["action"], budget=bound.get("budget_native"), budget_mode=bound.get("budget_mode", "DAILY"))
    client = None
    submitted = False
    provider_response_received = False
    fence_claimed = False
    proposal_claimed = False
    automation_authority_entered = False
    row["execution_claim_id"] = uuid.uuid4().hex
    try:
        await _claim_fence(db, row)
        fence_claimed = True
        claimed = await db[PROPOSALS].update_one({"user_id": actor["user_id"], "proposal_id": proposal_id, "status": "previewed", "submission_started": False}, {"$set": {"status": "executing", "approved_by": actor["actor_id"], "execution_claim_id": row["execution_claim_id"]}})
        if not claimed.matched_count:
            raise problem("google_ads_execution_already_started", "تمت المطالبة بالعملية سابقًا.")
        proposal_claimed = True
        # Bind the complete automation read bundle to its EARLIEST read. A
        # slow credential/metadata/snapshot/inventory read must not refresh the
        # apparent age of settings read earlier in this execution attempt.
        native_read_started_at = now().isoformat() if automation else None
        client, epoch = await account_client(db, actor["user_id"], row["account_id"], client_factory=client_factory)
        metadata = await client.account_metadata()
        if epoch != row["auth_epoch"] or metadata["currency_code"] != row["currency"] or metadata["time_zone"] != row["time_zone"]:
            raise problem("google_ads_account_changed", "توثيق الحساب أو العملة أو التوقيت تغير بعد المعاينة.")
        fresh = None
        if row["before_digest"]:
            fresh = await client.campaign_snapshot(row["campaign_id"], children=True, **({"duplicate_controls": True} if row["action"] == "duplicate" else {}))
            if digest(fresh) != row["before_digest"]:
                raise problem("google_ads_provider_drift", "إعدادات جوجل تغيرت منذ المعاينة؛ راجع التغيير الجديد.")
        await record_event(db, row, "approved", expected_status="executing", approved_at=now().isoformat(), approved_by=actor["actor_id"])
        if automation:
            automation_authority_entered = True
            await automation_gateway._authorize_dispatch(db, row, client, metadata, epoch, fresh,
                read_started_at=native_read_started_at)
        else:
            persisted_actor = await db.users.find_one({"id": actor["actor_id"]}, {"_id": 0})
            fresh_actor = await principal(db, persisted_actor, write=True)
            if fresh_actor["user_id"] != row["user_id"]:
                raise problem("google_ads_actor_scope_changed", "عضوية المنفّذ تغيرت منذ الموافقة.", 403)
            await execution_permission(db, fresh_actor, row["account_id"], row["action"], budget=bound.get("budget_native"), budget_mode=bound.get("budget_mode", "DAILY"))
        submitted = True
        # The durable dispatch floor has no blocking audit call between its
        # acknowledgement and provider dispatch. Events remain in the proposal.
        await record_event(db, row, "submitted", expected_status="approved", audit=False, submission_started=True, submitted_at=now().isoformat(), reconcile_after=(now() + timedelta(seconds=30)).isoformat())
        if automation:
            # No await between this kill-switch observation and the HTTP call.
            # The acknowledged floor remains durable when dispatch is stopped.
            from .google_ads_automation_authority import execution_enabled
            if not execution_enabled() or (kind == "standing_creation" and not automation_gateway.creation_dispatch_enabled()):
                # This branch has an ACKED floor and has not invoked mutate.
                # Preserve that floor and atomically record the precise proof;
                # unknown acknowledgements never enter this branch.
                await record_event(db, row, "failed", expected_status="submitted", expected_submission_started=True,
                    audit=False, provider_write_reached=False, safe_error="automation_stopped_before_provider_dispatch",
                    dispatch_cancelled_before_http=True,
                    dispatch_cancelled_before_http_claim_id=row["execution_claim_id"],
                    dispatch_cancelled_before_http_at=now().isoformat())
                return public_proposal(row)
        response = await client.mutate(row["operations"])
        provider_response_received = True
        names = mutation_resource_names(response, row["operations"], row["account_id"])
        await record_event(db, row, "verifying", **({"expected_status": "submitted", "expected_submission_started": True} if automation else {}), provider_write_reached=True, resource_names=names, accepted_receipt=True, accepted_operations_digest=digest(row["operations"]), request_id=response.get("_request_id"))
        verified, report = await client.verify_operations(row["operations"], names, accepted_receipt=True)
        if not verified:
            await record_event(db, row, "uncertain", **({"expected_status": "verifying", "expected_submission_started": True} if automation else {}), verification=report, safe_error="readback_not_confirmed")
            return public_proposal(row)
        return await _complete_google_action(db, row, client, names, report)
    except BaseException as exc:
        if automation_authority_entered and not submitted:
            # An authorization CAS may have committed even if its acknowledgement
            # was lost. Never take the human pre-submit reset/release path.
            try:
                await asyncio.shield(record_event(db, row, "uncertain", expected_status={"$in": ["executing", "approved", "uncertain"]}, expected_submission_started=False, safe_error="automation_authorization_unconfirmed"))
            except BaseException:
                pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            return public_proposal(await get_proposal(db, actor["user_id"], proposal_id))
        if submitted:
            if not provider_response_received and isinstance(exc, HTTPException) and isinstance(exc.detail, dict) and 400 <= (exc.detail.get("provider_status") or 0) < 500:
                await record_event(db, row, "failed", **({"expected_status": {"$in": ["submitted", "verifying", "uncertain"]}, "expected_submission_started": True} if automation else {}), verified=False, provider_write_reached=False, safe_error=exc.detail.get("code"), rejection={"provider_status": exc.detail["provider_status"], "provider_codes": exc.detail.get("provider_codes") or []})
                if not automation:
                    await _release_fence(db, row)
                return public_proposal(row)
            try:
                await asyncio.shield(record_event(db, row, "uncertain", **({"expected_status": {"$in": ["approved", "submitted", "verifying", "uncertain"]}, "expected_submission_started": {"$in": [False, True]}} if automation else {}), safe_error="provider_result_unconfirmed", local_finalization_pending=row.get("verified") is True))
            except BaseException:
                pass  # Submitted state and account fence are the durable floor.
            if isinstance(exc, asyncio.CancelledError):
                raise
            return public_proposal(await get_proposal(db, actor["user_id"], proposal_id))
        if fence_claimed:
            try:
                if proposal_claimed:
                    await record_event(db, row, "previewed", expected_status={"$in": ["executing", "approved"]}, safe_error="execution_not_submitted")
            finally:
                await _release_fence(db, row)
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise problem("google_ads_execution_unavailable", "تعذر بدء التنفيذ؛ لم تُرسل العملية.", 503) from None
    finally:
        if client:
            try:
                await client.close()
            except Exception:
                pass


async def _complete_google_action(db, row, client, names, report):
    created = next((name.rsplit("/", 1)[1] for op, name in zip(row["operations"], names) if "campaignOperation" in op and "create" in op["campaignOperation"]), None)
    if row["action"] in {"upload_image", "attach_video", "upload_html5"}:
        assets, complete = await client.assets()
        after = next((asset for asset in assets if asset["resource_name"] in names), None)
        if not complete or not after:
            raise problem("google_ads_asset_readback_missing", "المادة أُرسلت، لكن هوية عرضها لم تثبت بعد.")
    elif any("remove" in op.get("campaignOperation", {}) for op in row["operations"]):
        after = {"campaign_id": row["campaign_id"], "status": "REMOVED"}
    else:
        after = human_snapshot(await client.campaign_snapshot(created or row["campaign_id"], children=True))
    if created and (row.get("planned") or {}).get("product_ids"):
        from .campaign_product_associations import attach_products_to_management_proposal, adopt_management_proposal_products
        await db[PROPOSALS].update_one({"user_id": row["user_id"], "proposal_id": row["proposal_id"], "product_association_verified_at": {"$exists": False}}, {"$set": {"product_association_verified_at": now().isoformat()}})
        association = await db[PROPOSALS].find_one({"user_id": row["user_id"], "proposal_id": row["proposal_id"]}, {"_id": 0, "product_association_verified_at": 1})
        verified_at = association["product_association_verified_at"]
        await attach_products_to_management_proposal(db, row["user_id"], proposal_id=row["proposal_id"], provider="google_ads", account_id=row["account_id"], products=row["planned"]["product_ids"], actor_id=row.get("approved_by"), observed_at=row["created_at"], idempotency_prefix="google-intent:" + row["proposal_id"])
        await adopt_management_proposal_products(db, row["user_id"], proposal_id=row["proposal_id"], provider="google_ads", account_id=row["account_id"], campaign_id=created, actor_id=row.get("approved_by"), provider_verified_at=verified_at, provider_entity_verified=True, idempotency_prefix="google-campaign:" + row["proposal_id"])
    await record_event(db, row, "completed", **({"expected_status": {"$in": ["submitted", "verifying", "uncertain"]}, "expected_submission_started": True} if row.get("automation_source") else {}), verified=True, verification=report, after=after, resource_names=names,
        created_campaign_id=created, executed_at=now().isoformat(), safe_error=None, local_finalization_pending=False)
    if not row.get("automation_source"):
        await _release_fence(db, row)
    return public_proposal(row)


async def reconcile_google_proposal(db, actor, proposal_id, *, client_factory=GoogleAdsClient):
    if actor.get("actor_id") == "mezan_google_automation_v1":
        raise problem("google_automation_gateway_required", "استخدم مسار التحقق الآلي المستقل.", 403)
    return await _reconcile_google_proposal(db, actor, proposal_id, client_factory=client_factory)


async def _reconcile_google_proposal(db, actor, proposal_id, *, client_factory=GoogleAdsClient, automation=False):
    row = await get_proposal(db, actor["user_id"], proposal_id)
    if bool(row.get("automation_source")) != automation:
        raise problem("google_automation_gateway_required", "مسار التحقق من العملية غير مطابق.", 403)
    if digest(immutable(row)) != row.get("confirmation_digest"):
        raise problem("google_ads_approval_changed", "تعذر إثبات نسخة الاقتراح الأصلية.")
    if row.get("reconcile_after") and row["reconcile_after"] > now().isoformat():
        raise problem("google_ads_reconcile_wait", "انتظر مهلة التحقق الظاهرة قبل قراءة نتيجة الإرسال.")
    if row["status"] == "completed" and row.get("verified"):
        if not automation:
            await _release_fence(db, row)
        return public_proposal(row)
    if actor.get("owner") and not row.get("submission_started"):
        fence = await db[FENCES].find_one({"user_id": row["user_id"], "account_id": row["account_id"], "proposal_id": row["proposal_id"], "status": "claimed"}, {"_id": 0})
        if fence and fence.get("execution_claim_id") and fence.get("claimed_at", now().isoformat()) <= (now() - timedelta(minutes=5)).isoformat():
            # Cancel the durable dispatch gate first. A delayed worker can no
            # longer claim/approve/submit this proposal after this CAS wins.
            cancelled = await db[PROPOSALS].update_one({"user_id": row["user_id"], "proposal_id": row["proposal_id"], "submission_started": False, "$or": [{"status": "previewed"}, {"status": {"$in": ["executing", "approved"]}, "execution_claim_id": fence["execution_claim_id"]}]}, {"$set": {"status": "failed", "safe_error": "execution_cancelled_before_dispatch", "execution_claim_id": fence["execution_claim_id"], "provider_write_reached": False}})
            if cancelled.matched_count:
                row.update(status="failed", safe_error="execution_cancelled_before_dispatch", execution_claim_id=fence["execution_claim_id"], provider_write_reached=False)
                await _release_fence(db, row)
                await record_event(db, row, "failed", expected_status="failed")
                return public_proposal(row)
    if not row.get("submission_started") or row["status"] not in {"submitted", "verifying", "uncertain"}:
        raise problem("google_ads_reconcile_not_needed", "لا يوجد تنفيذ معلّق يحتاج التحقق.")
    client, _ = await account_client(db, actor["user_id"], row["account_id"], client_factory=client_factory)
    try:
        names = row.get("resource_names") or []
        has_creates = any("create" in next(iter(op.values())) for op in row["operations"])
        if not names and row.get("correlation_label") and has_creates:
            from .google_ads_verification import recover_create_operation_names
            def resources(value):
                if isinstance(value, dict):
                    return [ref for item in value.values() for ref in resources(item)]
                if isinstance(value, list):
                    return [ref for item in value for ref in resources(item)]
                return [value] if isinstance(value, str) and value.startswith(f"customers/{row['account_id']}/") else []
            before_names = resources(row.get("before_snapshot") or {}) if row.get("before_snapshot") else None
            names = await recover_create_operation_names(client, row["operations"], row["correlation_label"], known_before_resource_names=before_names)
        elif not names and row.get("campaign_id"):
            # Update/remove identities are already frozen before submission.
            names = [next(iter(op.values())).get("remove") or (next(iter(op.values())).get("update") or {}).get("resourceName") for op in row["operations"]]
            if any(not name for name in names):
                raise problem("google_ads_reconcile_identity_pending", "تحتاج العناصر المنشأة ضمن العملية إلى إثبات هويتها؛ لن تُرسل العملية مرة أخرى.")
        if not names:
            raise problem("google_ads_reconcile_identity_pending", "هوية نتيجة الإرسال لم تثبت بعد؛ لا تعِد العملية.")
        accepted = row.get("accepted_receipt") is True and row.get("accepted_operations_digest") == digest(row["operations"]) and row.get("resource_names") == names
        verified, report = await client.verify_operations(row["operations"], names, accepted_receipt=accepted)
        if verified:
            return await _complete_google_action(db, row, client, names, report)
        await record_event(db, row, "uncertain", **({"expected_status": {"$in": ["submitted", "verifying", "uncertain"]}, "expected_submission_started": True} if automation else {}), verification=report)
        return public_proposal(row)
    finally:
        await client.close()

