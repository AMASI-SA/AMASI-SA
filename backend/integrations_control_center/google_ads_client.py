"""Bounded native Google Ads transport for the existing Mezan V2 account.

ADR-001: provider shapes stay at this boundary (4), fixed origin and backend
credentials (14), tenant/account identity is proved before use (11). Mutations
are never retried: the durable management gateway owns uncertain outcomes (10).
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
from fastapi import HTTPException

from .google_ads_reporting import _developer_token, google_ads_api_version

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 2000
MAX_OPERATIONS = 10000
ID_RE = re.compile(r"^[0-9]{1,30}$")
RESOURCE_RE = re.compile(r"^customers/([0-9]+)/([A-Za-z]+)/([A-Za-z0-9_~.]+)$")


def problem(code: str, message: str, status: int = 409, **evidence: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **evidence})


def identifier(value: Any) -> str:
    value = str(value or "").strip()
    if re.fullmatch(r"[0-9]{3}-[0-9]{3}-[0-9]{4}", value):
        value = value.replace("-", "")
    if not ID_RE.fullmatch(value):
        raise problem("google_ads_invalid_identity", "معرّف حساب أو عنصر جوجل غير صالح.", 422)
    return value


def gaql_string(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def owned_resource(value: str, customer_id: str, collection: str | None = None) -> str:
    match = RESOURCE_RE.fullmatch(str(value or ""))
    if not match or match.group(1) != identifier(customer_id) or collection and match.group(2) != collection:
        raise problem("google_ads_foreign_resource", "العنصر لا يتبع الحساب المحدد.", 422)
    return str(value)


class GoogleAdsClient:
    """No redirects, no retries, bounded JSON, no provider error payload leaks."""

    def __init__(self, token: str, account: dict, *, transport=None, developer_token: str | None = None):
        self.customer_id = identifier(account.get("ad_account_id") or account.get("external_account_id"))
        version = google_ads_api_version()
        if not re.fullmatch(r"v[0-9]+", version):
            raise problem("google_ads_api_version_invalid", "إصدار تكامل جوجل غير صالح.", 503)
        self.base = f"https://googleads.googleapis.com/{version}"
        self.headers = {"Authorization": f"Bearer {token}", "developer-token": developer_token or _developer_token(), "Content-Type": "application/json"}
        if account.get("login_customer_id"):
            self.headers["login-customer-id"] = identifier(account["login_customer_id"])
        self.client = httpx.AsyncClient(timeout=18.0, follow_redirects=False, max_redirects=0,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2), transport=transport)

    async def close(self):
        await self.client.aclose()

    async def request(self, path: str, body: dict) -> dict:
        raw = bytearray()
        async with asyncio.timeout(22):
            async with self.client.stream("POST", self.base + path, headers=self.headers, json=body) as response:
                status = response.status_code
                request_id = response.headers.get("request-id", "")[:100]
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise problem("google_ads_response_limit", "استجابة جوجل تجاوزت حد القراءة؛ ضيّق نطاق التقرير.", 502)
                    raw.extend(chunk)
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise problem("google_ads_invalid_response", "تعذر تفسير استجابة جوجل؛ تحقق من حالة العملية قبل إعادتها.", 502) from None
        if not isinstance(data, dict):
            raise problem("google_ads_invalid_response", "استجابة جوجل غير مكتملة.", 502)
        if not 200 <= status < 300 or data.get("error"):
            codes, fields = [], []
            for detail in (data.get("error") or {}).get("details", [])[:10]:
                if not isinstance(detail, dict):
                    continue
                for row in (detail.get("errors") or [])[:20]:
                    if not isinstance(row, dict):
                        continue
                    for category, value in (row.get("errorCode") or {}).items():
                        safe = f"{category}:{value}"
                        if re.fullmatch(r"[A-Za-z_]+:[A-Z0-9_]+", safe):
                            codes.append(safe)
                    path_parts = (row.get("location") or {}).get("fieldPathElements") or []
                    field = ".".join(str(part.get("fieldName") or "") for part in path_parts if isinstance(part, dict))
                    if re.fullmatch(r"[A-Za-z_.]+", field or ""):
                        fields.append(field[:180])
            if status in {401, 403}:
                message = "جوجل رفض صلاحية الحساب أو التوثيق؛ راجع الربط والصلاحيات."
            elif status == 429:
                message = "جوجل حدّ عدد الطلبات مؤقتًا؛ حاول لاحقًا."
            else:
                message = "جوجل لم يقبل الإعدادات. راجع الحقول المحددة ومتطلبات نوع الحملة."
            raise problem("google_ads_provider_rejected", message, 429 if status == 429 else 409,
                provider_status=status, provider_codes=sorted(set(codes))[:20], fields=sorted(set(fields))[:20], request_id=request_id)
        if request_id:
            data["_request_id"] = request_id
        return data

    async def search(self, query: str, *, max_rows: int = MAX_ROWS) -> tuple[list[dict], bool]:
        data = await self.request(f"/customers/{self.customer_id}/googleAds:search", {"query": query, "searchSettings": {"returnTotalResultsCount": True}})
        rows = data.get("results", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise problem("google_ads_invalid_report", "تقرير جوجل لا يحتوي صفوفًا صالحة.", 502)
        try:
            total = int(data.get("totalResultsCount", len(rows)))
        except (ValueError, TypeError):
            raise problem("google_ads_invalid_report", "اكتمال تقرير جوجل غير مثبت.", 502) from None
        complete = not data.get("nextPageToken") and len(rows) <= max_rows and total == len(rows)
        return rows[:max_rows], complete

    async def account_metadata(self) -> dict:
        rows, complete = await self.search("SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone, customer.manager FROM customer LIMIT 1", max_rows=1)
        row = rows[0].get("customer", {}) if len(rows) == 1 else {}
        if not complete or str(row.get("id") or "") != self.customer_id or not re.fullmatch(r"[A-Z]{3}", str(row.get("currencyCode") or "")) or not row.get("timeZone"):
            raise problem("google_ads_account_identity_unproved", "لم يثبت جوجل هوية الحساب وعملته وتوقيته.")
        if row.get("manager"):
            raise problem("google_ads_manager_account", "اختر حسابًا إعلانيًا فرعيًا؛ حساب المدير لا ينشئ حملات مباشرة.")
        return {"account_id": self.customer_id, "customer_id": self.customer_id,
            "name": row.get("descriptiveName") or self.customer_id,
            "currency_code": row["currencyCode"], "time_zone": row["timeZone"], "identity_verified": True}

    async def mutate(self, operations: list[dict], *, validate_only: bool = False) -> dict:
        if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
            raise problem("google_ads_operation_limit", "عدد عناصر الحملة يتجاوز حد التنفيذ المتاح.", 422)
        return await self.request(f"/customers/{self.customer_id}/googleAds:mutate",
            {"mutateOperations": operations, "partialFailure": False, "validateOnly": validate_only, "responseContentType": "MUTABLE_RESOURCE"})

    async def field_metadata(self, names: list[str]) -> dict[str, dict]:
        names = sorted(set(names))
        if len(names) > 600 or any(not re.fullmatch(r"[a-z][a-z0-9_.]*", name) for name in names):
            raise problem("google_ads_field_limit", "عقد قراءة عناصر الحملة غير صالح.", 422)
        if not names:
            return {}
        data = await self.request("/googleAdsFields:search", {"query": "SELECT name, selectable, is_repeated, data_type WHERE name IN (" + ",".join(gaql_string(name) for name in names) + ")", "pageSize": 1000})
        rows = data.get("results", [])
        if not isinstance(rows, list) or data.get("nextPageToken"):
            raise problem("google_ads_field_metadata_incomplete", "بيانات توافق إصدار جوجل غير مكتملة.", 502)
        return {r["name"]: r for r in rows if isinstance(r, dict) and r.get("name") in names}

    async def fields_for_resource(self, root: str) -> dict[str, dict]:
        allowed = {"campaign", "campaign_budget", "ad_group", "ad_group_ad", "campaign_criterion", "ad_group_criterion", "campaign_asset", "ad_group_asset", "asset", "asset_group", "asset_group_asset", "asset_group_signal", "asset_group_listing_group_filter"}
        if root not in allowed:
            raise problem("google_ads_field_resource_invalid", "مورد عقد القراءة غير صالح.", 422)
        query = "SELECT name, category, selectable, is_repeated, data_type, selectable_with WHERE name LIKE " + gaql_string(root + ".%")
        output, token = {}, None
        for _ in range(3):
            body = {"query": query, "pageSize": 1000}
            if token:
                body["pageToken"] = token
            response = await self.request("/googleAdsFields:search", body)
            rows = response.get("results") or []
            if not isinstance(rows, list) or len(output) + len(rows) > 1000:
                raise problem("google_ads_field_catalog_limit", "دليل عناصر القراءة تجاوز الحد الآمن.")
            for field in rows:
                if not isinstance(field, dict) or not str(field.get("name") or "").startswith(root + "."):
                    raise problem("google_ads_field_catalog_invalid", "دليل عناصر القراءة غير صالح.")
                if field.get("category") == "ATTRIBUTE" and field.get("selectable") is True:
                    output[field["name"]] = field
            token = response.get("nextPageToken")
            if not token:
                return output
        raise problem("google_ads_field_catalog_incomplete", "دليل عناصر القراءة غير مكتمل.")

    async def campaign_snapshot(self, campaign_id: str, *, children: bool = False, duplicate_controls: bool = False) -> dict:
        from .google_ads_snapshot import snapshot
        return await snapshot(self, campaign_id, children=children, duplicate_controls=duplicate_controls)

    async def assets(self) -> tuple[list[dict], bool]:
        rows, complete = await self.search("SELECT asset.resource_name, asset.name, asset.type, asset.youtube_video_asset.youtube_video_id, "
            "asset.image_asset.full_size.url, asset.image_asset.full_size.width_pixels, asset.image_asset.full_size.height_pixels "
            f"FROM asset LIMIT {MAX_ROWS + 1}")
        return [{"resource_name": r.get("asset", {}).get("resourceName"), "name": r.get("asset", {}).get("name") or r.get("asset", {}).get("resourceName"),
                 "type": r.get("asset", {}).get("type"), "preview_url": r.get("asset", {}).get("imageAsset", {}).get("fullSize", {}).get("url"),
                 "dimensions": {"width": r.get("asset", {}).get("imageAsset", {}).get("fullSize", {}).get("widthPixels"),
                                "height": r.get("asset", {}).get("imageAsset", {}).get("fullSize", {}).get("heightPixels")},
                 "youtube_video_id": r.get("asset", {}).get("youtubeVideoAsset", {}).get("youtubeVideoId")} for r in rows], complete

    async def verify_operations(self, operations: list[dict], resource_names: list[str], *, accepted_receipt: bool = False) -> tuple[bool, dict]:
        """Read every changed resource independently of the mutation response.

        Batched by GAQL resource to keep the number of reads bounded. All
        explicitly set scalars and nested values selected below are compared;
        required-field coverage gaps fail verification rather than pass.
        """
        from .google_ads_verification import verify_google_operations
        return await verify_google_operations(self, operations, resource_names, accepted_receipt=accepted_receipt)


def mutation_resource_names(response: dict, operations: list[dict], customer_id: str) -> list[str]:
    results = response.get("mutateOperationResponses")
    if not isinstance(results, list) or len(results) != len(operations) or response.get("partialFailureError"):
        raise problem("google_ads_mutation_result_incomplete", "إرسال الحملة غير مثبت بالكامل؛ لا تعِد الإنشاء.", 502)
    names = []
    for operation, result in zip(operations, results):
        if not isinstance(result, dict) or len(operation) != 1:
            raise problem("google_ads_mutation_result_incomplete", "هويات عناصر العملية غير مثبتة.", 502)
        spec = next(iter(operation.values()))
        response_field = next(iter(operation)).removesuffix("Operation") + "Result"
        value = result.get(response_field) or {}
        collection = {"campaign": "campaigns", "campaignBudget": "campaignBudgets", "adGroup": "adGroups", "adGroupAd": "adGroupAds", "adGroupCriterion": "adGroupCriteria", "campaignCriterion": "campaignCriteria", "asset": "assets", "assetGroup": "assetGroups", "assetGroupAsset": "assetGroupAssets", "assetGroupSignal": "assetGroupSignals", "assetGroupListingGroupFilter": "assetGroupListingGroupFilters", "campaignAsset": "campaignAssets", "label": "labels", "campaignLabel": "campaignLabels", "campaignConversionGoal": "campaignConversionGoals", "conversionGoalCampaignConfig": "conversionGoalCampaignConfigs"}.get(next(iter(operation)).removesuffix("Operation"))
        if not collection:
            raise problem("google_ads_mutation_result_unsupported", "عقد هوية نتيجة العملية غير متاح.", 502)
        names.append(owned_resource(value.get("resourceName"), customer_id, collection))
    return names
