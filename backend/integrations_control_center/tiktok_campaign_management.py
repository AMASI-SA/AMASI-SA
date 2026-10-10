"""Bounded native TikTok campaign controls: preview, owner approval, verify.

The lifecycle follows Mezan's existing provider management contract. New
campaigns use Upgraded Smart+ and are always DISABLE. AI has no write tool.
Only one selected campaign is read; no media, customer or order data enters
this control plane. Submitted but unverified writes cannot be retried.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_UP
import hashlib
import json
import os
import secrets
import uuid
from typing import Any, Callable, Literal

import httpx
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError
from resource_governor import governor

from .tiktok_oauth_security import (
    TIKTOK_CREDENTIALS_COLLECTION, TIKTOK_PROVIDER_ID,
    decrypt_tiktok_token, tiktok_oauth_configured,
)
from .tiktok_native_hierarchy import ENTITY_COLLECTION, MAX_ENTITIES

COLLECTION = "mezan_tiktok_campaign_proposals_v1"
FENCE_COLLECTION = "mezan_tiktok_campaign_fences_v1"
SOURCE_MODE = "tiktok_governed_campaign_management_v1"
API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
MAX_RESPONSE_BYTES = 131_072
MAX_PROPOSAL_BYTES = 12_000
PROPOSAL_TTL = timedelta(minutes=30)
QUERY_TIMEOUT_MS = 1500
_slot = asyncio.Semaphore(1)
COMMON_FIELDS = (
    "advertiser_id", "campaign_id", "campaign_name", "operation_status",
    "secondary_status", "objective_type", "budget", "budget_mode",
)
SMART_FIELDS = ("budget_optimize_on", "budget_auto_adjust_strategy", "sales_destination")
PUBLIC_FIELDS = (
    "proposal_id", "provider", "action", "account_id", "account_name", "currency",
    "campaign_id", "campaign_name", "flow", "before", "planned", "reason",
    "financial_bound", "confirmation_digest", "status", "created_at", "expires_at",
    "approved_at", "executed_at", "provider_write_reached", "verified", "after",
    "created_campaign_id", "request_id", "safe_error", "events", "updated_at",
    "reconcile_after",
    "local_finalization_pending", "metadata_refresh_deferred",
)


def _now():
    return datetime.now(timezone.utc)


def _problem(code, message, status=409):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _money(value):
    if isinstance(value, bool):
        raise _problem("tiktok_budget_invalid", "قيمة الميزانية غير صالحة.", 422)
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > 10_000_000 or number != number.quantize(Decimal("0.01")):
            raise InvalidOperation
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise _problem("tiktok_budget_invalid", "أدخل مبلغًا موجبًا بدقة منزلتين في عملة الحساب.", 422) from None


class TikTokCampaignProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    action: Literal["create", "rename", "pause", "set_budget", "enable"]
    account_id: str = Field(min_length=1, max_length=30, pattern=r"^[0-9]+$")
    campaign_id: str | None = Field(default=None, min_length=1, max_length=30, pattern=r"^[0-9]+$")
    campaign_name: str | None = Field(default=None, min_length=1, max_length=240)
    objective_type: Literal["WEB_CONVERSIONS", "LEAD_GENERATION"] = "WEB_CONVERSIONS"
    budget_native: float | None = Field(default=None, strict=True, gt=0, le=10_000_000)
    budget_mode: Literal["BUDGET_MODE_DAY", "BUDGET_MODE_TOTAL"] = "BUDGET_MODE_DAY"
    spend_ceiling_native: float | None = Field(default=None, strict=True, gt=0, le=10_000_000)
    reason: str = Field(min_length=5, max_length=400)
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("campaign_name", "reason")
    @classmethod
    def text_fields(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("text must be nonempty without control characters")
        return value

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "create" and self.campaign_id is not None:
            raise ValueError("new campaigns have no campaign_id")
        if self.action != "create" and not self.campaign_id:
            raise ValueError("campaign_id required")
        if self.action in {"create", "rename"} and not self.campaign_name:
            raise ValueError("campaign_name required")
        if self.action in {"create", "set_budget"} and self.budget_native is None:
            raise ValueError("budget_native required")
        if self.action in {"set_budget", "enable"} and self.spend_ceiling_native is None:
            raise ValueError("explicit native currency spend ceiling required")
        if self.action not in {"create", "set_budget"} and self.budget_native is not None:
            raise ValueError("unexpected budget")
        if self.action not in {"create", "rename"} and self.campaign_name is not None:
            raise ValueError("unexpected name")
        return self


class TikTokCampaignApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_digest: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]+$")


def _enabled():
    return os.environ.get("TIKTOK_NATIVE_MANAGEMENT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


@asynccontextmanager
async def _admission():
    if _slot.locked() or governor.peek()[0] in {"blocked", "cancel"}:
        raise _problem("tiktok_management_busy", "إدارة TikTok مشغولة؛ حاول بعد قليل.", 503)
    await _slot.acquire()
    try:
        async with asyncio.timeout(50):
            yield
    except TimeoutError:
        raise _problem("tiktok_management_timeout", "انتهت مهلة الإدارة؛ راجع حالة الاقتراح قبل أي إعادة.", 503) from None
    finally:
        _slot.release()


async def ensure_tiktok_management_indexes(db):
    await db[COLLECTION].create_index([("user_id", 1), ("proposal_id", 1)], unique=True, name="tiktok_management_proposal_unique")
    await db[COLLECTION].create_index([("user_id", 1), ("idempotency_key", 1)], unique=True, name="tiktok_management_idempotency_unique")
    await db[COLLECTION].create_index([("user_id", 1), ("created_at", -1)], name="tiktok_management_history")
    # Fences intentionally have no TTL. A submitted uncertain write never
    # becomes eligible merely because a lease or document expires.
    await db[FENCE_COLLECTION].create_index([("user_id", 1), ("scope", 1)], unique=True, name="tiktok_management_entity_fence_unique")


async def _account(db, user_id, account_id):
    if not tiktok_oauth_configured():
        raise _problem("tiktok_oauth_not_configured", "إعدادات اتصال TikTok غير مكتملة.", 503)
    query = {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID,
             "connection_status": "connected", "connection_provenance": "api_connection",
             "$or": [{"ad_account_id": account_id}, {"external_account_id": account_id}]}
    row = await db.mezan_integration_accounts_v2.find_one(query,
        {"_id": 0, "display_name": 1, "currency": 1}, max_time_ms=QUERY_TIMEOUT_MS)
    connection = await db.mezan_integrations_v2.find_one(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID,
         "connection_status": "connected", "connection_provenance": "api_connection"},
        {"_id": 0, "connection_status": 1}, max_time_ms=QUERY_TIMEOUT_MS)
    credential = await db[TIKTOK_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID, "advertiser_ids": account_id},
        {"_id": 0, "access_token_ciphertext": 1, "updated_at": 1}, max_time_ms=QUERY_TIMEOUT_MS)
    if not connection or not row or not credential:
        raise _problem("tiktok_management_account_unavailable", "الحساب غير متاح ضمن اتصالك المباشر.", 404)
    try:
        token = decrypt_tiktok_token(credential.get("access_token_ciphertext"))
    except ValueError:
        raise _problem("tiktok_needs_reauth", "تعذر فتح التوثيق المحفوظ؛ أعد ربط TikTok من صفحة التكاملات.") from None
    if not token:
        raise _problem("tiktok_needs_reauth", "أعد توثيق اتصال TikTok من صفحة التكاملات.")
    return row, token, str(credential.get("updated_at") or "")


class TikTokCampaignProvider:
    """Fixed provider origins, bounded streamed JSON and no HTTP retries."""
    def __init__(self, token, *, transport=None):
        self.token = token
        self.client = httpx.AsyncClient(timeout=12.0, follow_redirects=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1), transport=transport)

    async def close(self):
        try:
            async with asyncio.timeout(2):
                await self.client.aclose()
        except (TimeoutError, httpx.HTTPError):
            pass

    async def _request(self, method, path, *, params=None, body=None):
        if governor.peek()[0] in {"blocked", "cancel"}:
            raise _problem("tiktok_management_busy", "موارد التطبيق مشغولة؛ أعد المحاولة لاحقًا.", 503)
        headers = {"Access-Token": self.token, "Content-Type": "application/json"}
        raw = bytearray()
        async with asyncio.timeout(14):
            async with self.client.stream(method, API_BASE + path, headers=headers, params=params, json=body) as response:
                if response.status_code != 200:
                    raise _problem("tiktok_management_provider_http_error", "تعذر تأكيد استجابة TikTok؛ راجع الاقتراح.", 502)
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise _problem("tiktok_management_response_limit", "استجابة TikTok تجاوزت حد القراءة.", 502)
                    raw.extend(chunk)
        try:
            result = json.loads(raw)
        except (ValueError, TypeError):
            raise _problem("tiktok_management_invalid_response", "استجابة TikTok غير صالحة.", 502) from None
        if not isinstance(result, dict) or result.get("code") not in (0, "0") or not isinstance(result.get("data"), dict):
            raise _problem("tiktok_management_provider_rejected", "لم يؤكد TikTok العملية؛ تحقق من صلاحيات الحساب والإعدادات.", 502)
        return result["data"]

    async def currency(self, account_id):
        data = await self._request("GET", "/advertiser/info/", params={
            "advertiser_ids": json.dumps([account_id]), "fields": json.dumps(["advertiser_id", "currency"])})
        rows = data.get("list")
        if not isinstance(rows, list) or len(rows) != 1 or str(rows[0].get("advertiser_id") or "") != account_id:
            raise _problem("tiktok_management_account_identity_mismatch", "تعذر إثبات هوية الحساب من TikTok.")
        currency = rows[0].get("currency")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
            raise _problem("tiktok_management_currency_unknown", "عملة الحساب غير مثبتة؛ أوقف التنفيذ المالي.")
        return currency

    async def read(self, account_id, campaign_id, flow=None):
        flows = [flow] if flow else ["smart_plus", "manual"]
        for current in flows:
            prefix = "/smart_plus" if current == "smart_plus" else ""
            fields = COMMON_FIELDS + (SMART_FIELDS if current == "smart_plus" else ())
            data = await self._request("GET", prefix + "/campaign/get/", params={
                "advertiser_id": account_id, "filtering": json.dumps({"campaign_ids": [campaign_id]}),
                "fields": json.dumps(fields), "page": 1, "page_size": 1})
            rows, page = data.get("list"), data.get("page_info")
            if not isinstance(rows, list) or len(rows) > 1 or not isinstance(page, dict) or page.get("total_number") not in (0, 1):
                raise _problem("tiktok_management_entity_response_incomplete", "هوية الحملة أو اكتمال الاستجابة غير مثبت.")
            if not rows and page.get("total_number") == 0:
                continue
            if len(rows) != 1 or page.get("total_number") != 1:
                raise _problem("tiktok_management_entity_response_incomplete", "هوية الحملة غير مثبتة.")
            row = rows[0]
            if not isinstance(row, dict) or str(row.get("campaign_id") or "") != campaign_id or str(row.get("advertiser_id") or "") != account_id:
                raise _problem("tiktok_management_campaign_identity_mismatch", "الحملة لا تتبع الحساب المحدد.", 404)
            result = {key: row.get(key) for key in fields}
            result["flow"] = current
            for key, value in result.items():
                if isinstance(value, (list, dict)) or isinstance(value, str) and len(value) > 512:
                    raise _problem("tiktok_management_entity_invalid", "إعدادات الحملة غير صالحة للتنفيذ.")
            if result.get("budget") not in (None, ""):
                try:
                    number = Decimal(str(result["budget"]))
                    if isinstance(result["budget"], bool) or not number.is_finite() or number < 0:
                        raise InvalidOperation
                    result["budget"] = str(number)
                except (InvalidOperation, ValueError):
                    raise _problem("tiktok_management_entity_invalid", "ميزانية الحملة غير صالحة للمعاينة.") from None
            return result
        raise _problem("tiktok_management_campaign_not_found", "الحملة غير متاحة في الحساب المحدد.", 404)

    async def write(self, proposal):
        action = proposal["action"]
        prefix = "/smart_plus" if proposal["flow"] == "smart_plus" else ""
        body = {"advertiser_id": proposal["account_id"], **proposal["planned"]}
        if action == "create":
            body["request_id"] = proposal["request_id"]
            return await self._request("POST", "/smart_plus/campaign/create/", body=body)
        if action in {"pause", "enable"}:
            body["campaign_ids"] = [proposal["campaign_id"]]
            return await self._request("POST", prefix + "/campaign/status/update/", body=body)
        body["campaign_id"] = proposal["campaign_id"]
        return await self._request("POST", prefix + "/campaign/update/", body=body)


def _deleted(row):
    return "DELETE" in str(row.get("secondary_status") or "").upper() or str(row.get("operation_status") or "").upper() == "DELETE"


def _bounded_budget_mode(before):
    # The current Smart+ budget contract is proven by the provider read, not
    # inferred from a reporting snapshot, an AI recommendation or UI value.
    optimize = before.get("budget_optimize_on")
    strategy = before.get("budget_auto_adjust_strategy")
    if before.get("flow") != "smart_plus" or not isinstance(optimize, bool):
        raise _problem("tiktok_management_financial_basis_unknown", "أوقف التنفيذ المالي: يلزم إثبات ميزانية Smart+ ثابتة دون زيادة تلقائية.")
    mode = before.get("budget_mode")
    if mode not in {"BUDGET_MODE_DAY", "BUDGET_MODE_TOTAL", "BUDGET_MODE_DYNAMIC_DAILY_BUDGET"}:
        raise _problem("tiktok_management_unbounded_budget", "لا يمكن تشغيل أو تعديل ميزانية بلا حد مثبت.")
    # TikTok only returns the auto-adjust field for CBO dynamic daily
    # budgets. Absence is expected for fixed DAY/TOTAL, not proof for dynamic.
    if (strategy not in {None, "UNSET"}
            or mode == "BUDGET_MODE_DYNAMIC_DAILY_BUDGET" and (optimize is not True or strategy != "UNSET")
            or mode == "BUDGET_MODE_DAY" and optimize is not False):
        raise _problem("tiktok_management_financial_basis_unknown", "لم تثبت مطابقة نوع الميزانية وحدود الزيادة التلقائية.")
    return mode


def _financial_bound(payload, before):
    if payload.action not in {"set_budget", "enable"}:
        return None
    mode = _bounded_budget_mode(before)
    amount = _money(payload.budget_native if payload.action == "set_budget" else before.get("budget"))
    ceiling = _money(payload.spend_ceiling_native)
    factor = Decimal("1.25") if mode == "BUDGET_MODE_DYNAMIC_DAILY_BUDGET" else Decimal("1")
    maximum = (amount * factor).quantize(Decimal("0.01"), rounding=ROUND_UP)
    if maximum > ceiling:
        raise _problem("tiktok_management_spend_ceiling_exceeded", "الميزانية تتجاوز السقف الذي وافقت عليه؛ راجع مبلغ الميزانية والسقف.")
    return {"budget_mode": mode, "budget_native": str(amount), "approved_ceiling_native": str(ceiling),
            "maximum_native": str(maximum), "period": "lifetime" if mode == "BUDGET_MODE_TOTAL" else "day",
            "dynamic_daily_factor": str(factor), "financial_automation_allowed": False}


def _plan(payload, before):
    if payload.action == "create":
        planned = {"campaign_name": payload.campaign_name, "objective_type": payload.objective_type,
                   "operation_status": "DISABLE", "budget": float(_money(payload.budget_native)),
                   "budget_mode": payload.budget_mode, "budget_optimize_on": False}
        if payload.objective_type == "WEB_CONVERSIONS":
            planned["sales_destination"] = "WEBSITE"
        return planned
    if _deleted(before) or before.get("operation_status") not in {"ENABLE", "DISABLE"}:
        raise _problem("tiktok_management_campaign_not_mutable", "الحملة محذوفة أو حالتها غير صالحة للتعديل.")
    if payload.action == "rename":
        return {"campaign_name": payload.campaign_name}
    if payload.action in {"pause", "enable"}:
        return {"operation_status": "DISABLE" if payload.action == "pause" else "ENABLE"}
    return {"budget": float(_money(payload.budget_native))}


def _immutable(row):
    return {key: row.get(key) for key in (
        "action", "account_id", "currency", "campaign_id", "flow", "before", "planned",
        "reason", "financial_bound", "request_id", "auth_epoch", "created_at", "expires_at",
    )}


def _public(row):
    return {key: row.get(key) for key in PUBLIC_FIELDS if key in row}


async def _get(db, user_id, proposal_id):
    row = await db[COLLECTION].find_one({"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}, max_time_ms=QUERY_TIMEOUT_MS)
    if not row:
        raise _problem("tiktok_management_proposal_not_found", "الاقتراح غير متاح.", 404)
    return row


async def _event(db, user_id, proposal_id, status, **fields):
    now = _now().isoformat()
    values = {"status": status, "updated_at": now, **fields}
    result = await db[COLLECTION].update_one({"user_id": user_id, "proposal_id": proposal_id},
        {"$set": values,
         "$push": {"events": {"$each": [{"status": status, "actor_id": user_id, "at": now}], "$slice": -12}}})
    if result.matched_count != 1:
        raise _problem("tiktok_management_journal_unavailable", "تعذر حفظ حالة الاقتراح.", 503)
    return values


def _scope(row):
    return row["account_id"] + ":" + (row["campaign_id"] or "create:" + _digest(row["planned"]))


async def _refresh_verified_metadata(db, row):
    after = row["after"]
    campaign_id = row.get("created_campaign_id") or row["campaign_id"]
    entity = {"entity_id": campaign_id, "entity_name": after.get("campaign_name"),
              "campaign_id": campaign_id, "adgroup_id": None,
              "status": after.get("operation_status"), "delivery_status": after.get("secondary_status"),
              "objective": after.get("objective_type"), "budget_native": after.get("budget"),
              "budget_mode": after.get("budget_mode")}
    query = {"user_id": row["user_id"], "ad_account_id": row["account_id"], "entity_type": "campaign", "complete": True}
    # Mongo updates one matched array element. The 5,000-entity catalogue is
    # never read into Python or transported back to the client here.
    result = await db[ENTITY_COLLECTION].update_one({**query, "entities.entity_id": campaign_id},
        {"$set": {"entities.$[target]." + key: value for key, value in entity.items()}},
        array_filters=[{"target.entity_id": campaign_id}])
    if result.matched_count == 1:
        return True
    if row["action"] != "create":
        return False
    # Append exactly one verified creation only to an existing complete
    # catalogue below its established cap. No upsert or full-array replacement.
    result = await db[ENTITY_COLLECTION].update_one(
        {**query, "entities.entity_id": {"$ne": campaign_id},
         "$expr": {"$lt": [{"$size": {"$ifNull": ["$entities", []]}}, MAX_ENTITIES]}},
        {"$push": {"entities": entity}, "$inc": {"entity_count": 1}})
    return result.matched_count == 1


async def _finalize_verified(db, row, *, refresh_metadata=False):
    # Provider verification and its durable completed journal already succeeded.
    # Cache/fence housekeeping cannot change that outcome or repeat the POST.
    flags = {"local_finalization_pending": False}
    if refresh_metadata:
        try:
            async with asyncio.timeout(3):
                flags["metadata_refresh_deferred"] = not await _refresh_verified_metadata(db, row)
        except Exception:
            flags["metadata_refresh_deferred"] = True
    try:
        async with asyncio.timeout(3):
            await db[FENCE_COLLECTION].update_one(
                {"user_id": row["user_id"], "scope": _scope(row), "proposal_id": row["proposal_id"]},
                {"$set": {"status": "completed" if row["action"] == "create" else "released"}})
    except Exception:
        flags["local_finalization_pending"] = True
    row.update(flags)
    try:
        async with asyncio.timeout(2):
            await db[COLLECTION].update_one(
                {"user_id": row["user_id"], "proposal_id": row["proposal_id"], "status": "completed"},
                {"$set": flags})
    except Exception:
        pass
    return _public(row)


async def _complete_verified(db, row, after, campaign_id):
    fields = {"after": after, "verified": True, "provider_write_reached": True,
              "executed_at": _now().isoformat(), "safe_error": None,
              "local_finalization_pending": True, "metadata_refresh_deferred": True}
    if row["action"] == "create":
        fields["created_campaign_id"] = campaign_id
    values = await _event(db, row["user_id"], row["proposal_id"], "completed", **fields)
    row.update(values)
    row["events"] = (row.get("events", []) + [{"status": "completed", "actor_id": row["user_id"], "at": values["updated_at"]}])[-12:]


async def preview_tiktok_campaign(db, user_id, payload, *, provider_factory=TikTokCampaignProvider):
    async with _admission():
        await ensure_tiktok_management_indexes(db)
        account, token, epoch = await _account(db, user_id, payload.account_id)
        request_fingerprint = _digest(payload.model_dump(mode="json"))
        existing = await db[COLLECTION].find_one({"user_id": user_id, "idempotency_key": payload.idempotency_key}, {"_id": 0}, max_time_ms=QUERY_TIMEOUT_MS)
        if existing:
            if existing.get("input_digest") != request_fingerprint:
                raise _problem("tiktok_management_idempotency_conflict", "المفتاح نفسه مرتبط باقتراح مختلف.")
            return _public(existing)
        client = provider_factory(token)
        try:
            currency = await client.currency(payload.account_id)
            before = {} if payload.action == "create" else await client.read(payload.account_id, payload.campaign_id)
            planned = _plan(payload, before)
            bound = _financial_bound(payload, before)
        finally:
            await client.close()
        now = _now()
        row = {"proposal_id": str(uuid.uuid4()), "user_id": user_id, "provider": TIKTOK_PROVIDER_ID,
               "action": payload.action, "account_id": payload.account_id, "account_name": str(account.get("display_name") or "")[:240],
               "currency": currency, "campaign_id": payload.campaign_id,
               "campaign_name": payload.campaign_name or before.get("campaign_name"),
               "flow": "smart_plus" if payload.action == "create" else before["flow"], "before": before, "planned": planned,
               "reason": payload.reason, "financial_bound": bound, "status": "previewed",
               "idempotency_key": payload.idempotency_key, "input_digest": request_fingerprint,
               "request_id": str(secrets.randbelow(2**63 - 1) + 1) if payload.action == "create" else None,
               "auth_epoch": epoch, "created_at": now.isoformat(), "expires_at": (now + PROPOSAL_TTL).isoformat(),
               "updated_at": now.isoformat(), "provider_write_reached": False, "verified": False,
               "events": [{"status": "previewed", "actor_id": user_id, "at": now.isoformat()}]}
        row["confirmation_digest"] = _digest(_immutable(row))
        if len(json.dumps(row, ensure_ascii=False).encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise _problem("tiktok_management_proposal_limit", "الاقتراح يتجاوز حد البيانات.", 422)
        try:
            await db[COLLECTION].insert_one(row)
        except DuplicateKeyError:
            existing = await db[COLLECTION].find_one({"user_id": user_id, "idempotency_key": payload.idempotency_key}, {"_id": 0}, max_time_ms=QUERY_TIMEOUT_MS)
            if not existing or existing.get("input_digest") != request_fingerprint:
                raise _problem("tiktok_management_idempotency_conflict", "تعارض في حفظ الاقتراح.") from None
            return _public(existing)
        return _public(row)


def _matches(row, after):
    if _deleted(after):
        return False
    for key, expected in row["planned"].items():
        actual = after.get(key)
        if key == "budget":
            try:
                if _money(actual) != _money(expected):
                    return False
            except HTTPException:
                return False
        elif actual != expected:
            return False
    if row.get("financial_bound"):
        try:
            bound = row["financial_bound"]
            if (_bounded_budget_mode(after) != bound["budget_mode"]
                    or after.get("budget_optimize_on") is not row["before"].get("budget_optimize_on")
                    or _money(after.get("budget")) != _money(bound["budget_native"])):
                return False
        except HTTPException:
            return False
    return True


async def execute_tiktok_campaign(db, user_id, proposal_id, digest, *, provider_factory=TikTokCampaignProvider):
    async with _admission():
        row = await _get(db, user_id, proposal_id)
        if not secrets.compare_digest(row.get("confirmation_digest", ""), digest) or _digest(_immutable(row)) != digest:
            raise _problem("tiktok_management_confirmation_changed", "المعاينة تغيّرت؛ أنشئ اقتراحًا جديدًا.")
        if row["status"] == "completed":
            return await _finalize_verified(db, row)
        if row["status"] != "previewed" or row["expires_at"] <= _now().isoformat():
            raise _problem("tiktok_management_proposal_not_executable", "الاقتراح منتهٍ أو سبق بدء تنفيذه؛ راجع حالته.")
        if not _enabled():
            raise _problem("tiktok_management_disabled", "تنفيذ إدارة TikTok متوقف في إعدادات التشغيل.", 503)
        _, token, epoch = await _account(db, user_id, row["account_id"])
        if epoch != row["auth_epoch"]:
            raise _problem("tiktok_management_connection_changed", "تغيّر توثيق الحساب؛ أعد معاينة الاقتراح.")
        client = provider_factory(token)
        claimed, submitted, fence_owned = False, False, False
        scope = _scope(row)
        try:
            if await client.currency(row["account_id"]) != row["currency"]:
                raise _problem("tiktok_management_currency_changed", "تغيرت عملة الحساب؛ أعد المعاينة.")
            if row["action"] != "create":
                before = await client.read(row["account_id"], row["campaign_id"], row["flow"])
                if before != row["before"] or _deleted(before):
                    raise _problem("tiktok_management_campaign_changed", "تغيّرت إعدادات الحملة بعد المعاينة؛ أنشئ اقتراحًا جديدًا.")
            try:
                await db[FENCE_COLLECTION].update_one({"user_id": user_id, "scope": scope},
                    {"$setOnInsert": {"user_id": user_id, "scope": scope, "status": "released"}}, upsert=True)
            except DuplicateKeyError:
                pass
            fence = await db[FENCE_COLLECTION].update_one({"user_id": user_id, "scope": scope, "status": "released"},
                {"$set": {"status": "claimed", "proposal_id": proposal_id, "updated_at": _now().isoformat()}})
            if fence.modified_count != 1:
                raise _problem("tiktok_management_campaign_busy", "هناك تنفيذ سابق لهذه الحملة أو الإنشاء ينتظر التحقق.")
            fence_owned = True
            claim = await db[COLLECTION].update_one({"user_id": user_id, "proposal_id": proposal_id, "status": "previewed",
                "confirmation_digest": digest, "expires_at": {"$gt": _now().isoformat()}},
                {"$set": {"status": "executing", "approved_at": _now().isoformat(), "updated_at": _now().isoformat()}})
            if claim.modified_count != 1:
                raise _problem("tiktok_management_already_claimed", "سبق بدء تنفيذ الاقتراح.")
            claimed = True
            # Persist submission before the socket call. A crash after here is
            # deliberately uncertain, even if the provider never received it.
            submitted = True
            await _event(db, user_id, proposal_id, "submitted", provider_write_reached=True,
                reconcile_after=(_now() + timedelta(minutes=5)).isoformat())
            response = await client.write(row)
            campaign_id = row["campaign_id"]
            if row["action"] == "create":
                value = response.get("campaign_id")
                if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).isdigit() or len(str(value)) > 30:
                    raise _problem("tiktok_management_created_identity_unknown", "لم يُرجع TikTok معرّفًا مثبتًا؛ لا تعِد إنشاء الحملة.", 502)
                campaign_id = str(value)
                await _event(db, user_id, proposal_id, "verifying", created_campaign_id=campaign_id)
            after = await client.read(row["account_id"], campaign_id, row["flow"])
            if not _matches(row, after):
                await _event(db, user_id, proposal_id, "uncertain", after=after, safe_error="verification_mismatch")
            else:
                await _complete_verified(db, row, after, campaign_id)
                return await _finalize_verified(db, row, refresh_metadata=True)
            return _public(await _get(db, user_id, proposal_id))
        except BaseException as exc:
            if submitted and row["status"] != "completed":
                try:
                    await asyncio.shield(_event(db, user_id, proposal_id, "uncertain", safe_error="provider_result_unconfirmed"))
                except BaseException:
                    pass  # Durable submitted state and claimed fence survive.
            elif claimed and not submitted:
                await _event(db, user_id, proposal_id, "previewed", safe_error="execution_not_submitted")
            if fence_owned and not submitted:
                await db[FENCE_COLLECTION].update_one({"user_id": user_id, "scope": scope, "proposal_id": proposal_id}, {"$set": {"status": "released"}})
            if isinstance(exc, asyncio.CancelledError):
                raise
            if row["status"] == "completed":
                return _public(row)
            if submitted:
                raise _problem("tiktok_management_result_uncertain", "نتيجة التنفيذ غير مؤكدة؛ تحقق من الاقتراح، ولا تعِد العملية.", 502) from None
            if isinstance(exc, HTTPException):
                raise
            raise _problem("tiktok_management_unavailable", "تعذرت الإدارة قبل إرسال التعديل.", 503) from None
        finally:
            await client.close()


async def reconcile_tiktok_campaign(db, user_id, proposal_id, *, provider_factory=TikTokCampaignProvider):
    async with _admission():
        row = await _get(db, user_id, proposal_id)
        if _digest(_immutable(row)) != row.get("confirmation_digest"):
            raise _problem("tiktok_management_confirmation_changed", "تعذر إثبات الاقتراح الأصلي.")
        if row["status"] == "completed":
            return await _finalize_verified(db, row)
        if row["status"] not in {"submitted", "verifying", "uncertain", "executing"}:
            raise _problem("tiktok_management_reconciliation_not_needed", "لا يوجد تنفيذ معلق للتحقق.")
        if row.get("reconcile_after") and row["reconcile_after"] > _now().isoformat():
            raise _problem("tiktok_management_reconciliation_wait", "انتظر انتهاء فترة التحقق الظاهرة قبل قراءة نتيجة تنفيذ غير مؤكد.")
        campaign_id = row.get("created_campaign_id") or row.get("campaign_id")
        if not campaign_id:
            raise _problem("tiktok_management_created_identity_unknown", "راجع معرّف الطلب والحملة داخل TikTok؛ لا يوجد معرّف مثبت للقراءة ولا يجوز تكرار الإنشاء.")
        _, token, _ = await _account(db, user_id, row["account_id"])
        client = provider_factory(token)
        try:
            after = await client.read(row["account_id"], campaign_id, row["flow"])
        finally:
            await client.close()
        if _matches(row, after):
            await _complete_verified(db, row, after, campaign_id)
            return await _finalize_verified(db, row, refresh_metadata=True)
        return _public(await _get(db, user_id, proposal_id))


def attach_tiktok_campaign_management_routes(router, db, current_user: Callable, require_owner: Callable):
    prefix = f"/{TIKTOK_PROVIDER_ID}/management/proposals"

    @router.post(prefix, status_code=201)
    async def preview(payload: TikTokCampaignProposalInput, user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await preview_tiktok_campaign(db, str(owner["id"]), payload)

    @router.get(prefix)
    async def history(limit: int = Query(default=12, ge=1, le=25), user: dict = Depends(current_user)):
        owner = require_owner(user)
        async with _admission():
            rows = await db[COLLECTION].find({"user_id": str(owner["id"])},
                {"_id": 0, **{key: 1 for key in PUBLIC_FIELDS}}).max_time_ms(QUERY_TIMEOUT_MS).sort("created_at", -1).limit(limit).to_list(length=limit)
            return {"items": [_public(row) for row in rows], "source_mode": SOURCE_MODE}

    @router.get(prefix + "/{proposal_id}")
    async def get_proposal(proposal_id: str, user: dict = Depends(current_user)):
        owner = require_owner(user)
        async with _admission():
            return _public(await _get(db, str(owner["id"]), proposal_id))

    @router.post(prefix + "/{proposal_id}/approve-and-execute")
    async def execute(proposal_id: str, payload: TikTokCampaignApprovalInput, user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await execute_tiktok_campaign(db, str(owner["id"]), proposal_id, payload.confirmation_digest)

    @router.post(prefix + "/{proposal_id}/reconcile")
    async def reconcile(proposal_id: str, user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await reconcile_tiktok_campaign(db, str(owner["id"]), proposal_id)
