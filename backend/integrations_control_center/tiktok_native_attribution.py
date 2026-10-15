"""Bounded owner-scoped native TikTok identities for order attribution.

No provider requests, media, full catalogue arrays or inferred name matching.
"""
from __future__ import annotations

import asyncio
from typing import Any
from pymongo.errors import ExecutionTimeout
from salla_marketing_attribution import campaign_id_candidates, canonical_ad_platform
from integrations_control_center.campaign_product_associations import CAMPAIGN_PRODUCT_LINK_COLLECTION
from .tiktok_native_hierarchy import ENTITY_COLLECTION, QUERY_TIMEOUT_MS
from .tiktok_native_reporting import _accounts, MAX_REPORTING_ACCOUNTS, TikTokReportingError

MAX_ORDER_CAMPAIGN_IDS = 20
MAX_LINKS = 100
MAX_IDENTITIES = MAX_ORDER_CAMPAIGN_IDS * MAX_REPORTING_ACCOUNTS


def _problem(code: str) -> TikTokReportingError:
    return TikTokReportingError(code, "تعذر التحقق من إسناد طلب TikTok ضمن حدود القراءة.", status_code=503, retryable=True)


async def load_tiktok_order_evidence(db: Any, user_id: str, order: dict) -> tuple[list[dict], list[dict]]:
    if canonical_ad_platform(order) != "tiktok":
        return [], []
    candidates = list(dict.fromkeys(x for x in campaign_id_candidates(order) if x and len(x) <= 120))
    if not candidates:
        return [], []
    if len(candidates) > MAX_ORDER_CAMPAIGN_IDS:
        raise _problem("tiktok_order_campaign_candidate_limit")
    try:
        async with asyncio.timeout(4):
            try:
                accounts = await _accounts(db, user_id)
            except TikTokReportingError as exc:
                if exc.code == "tiktok_reporting_accounts_missing":
                    return [], []
                raise
            account_ids = [x["ad_account_id"] for x in accounts]
            pipeline = [
                {"$match": {"user_id": user_id, "ad_account_id": {"$in": account_ids},
                            "entity_type": "campaign", "complete": True}},
                {"$project": {"_id": 0, "ad_account_id": 1, "matches": {"$filter": {
                    "input": "$entities", "as": "entity", "cond": {"$in": ["$$entity.entity_id", candidates]}}}}},
                {"$unwind": "$matches"}, {"$limit": MAX_IDENTITIES + 1},
                {"$project": {"_id": 0, "provider": {"$literal": "tiktok"},
                    "account_id": "$ad_account_id", "campaign_id": "$matches.entity_id",
                    "campaign_name": {"$substrCP": [{"$ifNull": ["$matches.entity_name", ""]}, 0, 240]}}},
            ]
            identities = await db[ENTITY_COLLECTION].aggregate(pipeline, allowDiskUse=False,
                maxTimeMS=QUERY_TIMEOUT_MS).to_list(length=MAX_IDENTITIES + 1)
            if len(identities) > MAX_IDENTITIES:
                raise _problem("tiktok_order_campaign_identity_limit")
            if len({x["campaign_id"] for x in identities}) > 1:
                raise _problem("tiktok_order_campaign_identity_conflict")
            if not identities:
                return [], []
            # Product associations enrich proven scope; they never supply the
            # identity graph or fabricate conversion attribution.
            cursor = db[CAMPAIGN_PRODUCT_LINK_COLLECTION].find({
                "user_id": user_id, "provider": {"$in": ["tiktok", "tiktok_ads"]},
                "account_id": {"$in": account_ids}, "campaign_id": {"$in": candidates},
                "state": "active", "evidence.verification_status": "verified"},
                {"_id": 0, "provider": 1, "account_id": 1, "campaign_id": 1,
                 "campaign_name": 1, "product_id": 1, "product_variant_id": 1,
                 "association_id": 1, "evidence.verification_status": 1, "evidence.source": 1})
            links = await cursor.max_time_ms(QUERY_TIMEOUT_MS).limit(MAX_LINKS + 1).to_list(length=MAX_LINKS + 1)
            if len(links) > MAX_LINKS:
                raise _problem("tiktok_order_campaign_product_link_limit")
            return identities, links
    except (TimeoutError, ExecutionTimeout):
        raise _problem("tiktok_order_evidence_timeout") from None
