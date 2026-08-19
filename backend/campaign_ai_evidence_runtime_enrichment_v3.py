"""Runtime enrichment hook for Decision Intelligence V3 evidence packs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from campaign_ai_customer_voice_evidence_v3 import build_customer_voice_evidence
from campaign_ai_offer_schedule_v3 import build_offer_schedule_evidence
from campaign_ai_product_change_history_v3 import (
    build_product_change_history_evidence,
)
from campaign_ai_product_health_score_v3 import enrich_product_health_scores
from product_v2_routes import PRODUCTS as PRODUCT_V2_COLLECTION


async def _enrich_offer_schedules(
    db: Any,
    user_id: str,
    pack: dict[str, Any],
    *,
    product_ids: list[str],
    current: datetime,
) -> None:
    unique_ids = list(dict.fromkeys(product_ids))[:500]
    if not unique_ids:
        pack["offer_schedule_evidence"] = {
            "schema_version": "campaign_ai_offer_schedule_v3",
            "products": {},
            "limitations": ["no_verified_campaign_products"],
        }
        return

    rows = await db[PRODUCT_V2_COLLECTION].find(
        {"user_id": user_id, "salla_product_id": {"$in": unique_ids}},
        {
            "_id": 0,
            "salla_product_id": 1,
            "name": 1,
            "description": 1,
            "short_description": 1,
            "price": 1,
            "sale_price": 1,
            "sale_starts_at": 1,
            "sale_ends_at": 1,
        },
    ).limit(len(unique_ids)).to_list(length=len(unique_ids))

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        product_id = str(row.get("salla_product_id") or "")
        if not product_id:
            continue
        evidence_by_id[product_id] = build_offer_schedule_evidence(
            row,
            now=current,
        )

    for block in (((pack.get("product_intelligence") or {}).get("entities") or {}).values()):
        for product in block.get("products") or []:
            product_id = str(product.get("product_id") or "")
            if product_id in evidence_by_id:
                product["offer_schedule"] = evidence_by_id[product_id]

    pack["offer_schedule_evidence"] = {
        "schema_version": "campaign_ai_offer_schedule_v3",
        "products": evidence_by_id,
        "contract": (
            "Sale timing/copy is evidence only. OpenAI decides whether to extend the offer, "
            "refresh creative/copy, or take no action based on profitability and context."
        ),
        "limitations": [],
    }


def wrap_evidence_builder(
    base_builder: Callable[..., Awaitable[dict[str, Any]]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def build(
        db: Any,
        user_id: str,
        candidates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        pack = await base_builder(db, user_id, candidates, **kwargs)

        # Attach a coverage-aware product health summary before any downstream
        # enrichments or model call. Unknown checks remain unknown and no score
        # threshold is allowed to select a marketing action.
        try:
            enrich_product_health_scores(pack)
            pack["product_health_score_contract"] = (
                "Evidence summary only. Unknown checks are excluded from coverage and no "
                "product-health score threshold may automatically pause, scale or classify a campaign."
            )
        except Exception as exc:
            limitations = list(pack.get("limitations") or [])
            limitations.append(f"product_health_score_unavailable:{type(exc).__name__}")
            pack["limitations"] = list(dict.fromkeys(limitations))

        product_ids: list[str] = []
        for block in (((pack.get("product_intelligence") or {}).get("entities") or {}).values()):
            for product in block.get("products") or []:
                if product.get("product_id") is not None:
                    product_ids.append(str(product["product_id"]))

        current = kwargs.get("current")
        if not isinstance(current, datetime):
            current = datetime.now(timezone.utc)
        elif current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        try:
            await _enrich_offer_schedules(
                db,
                user_id,
                pack,
                product_ids=product_ids,
                current=current,
            )
        except Exception as exc:
            pack["offer_schedule_evidence"] = {
                "schema_version": "campaign_ai_offer_schedule_v3",
                "products": {},
                "limitations": [f"offer_schedule_unavailable:{type(exc).__name__}"],
            }
            limitations = list(pack.get("limitations") or [])
            limitations.append(f"offer_schedule_unavailable:{type(exc).__name__}")
            pack["limitations"] = list(dict.fromkeys(limitations))

        try:
            pack["customer_voice"] = await build_customer_voice_evidence(
                db,
                user_id,
                candidates,
                product_ids,
                current=current,
            )
        except Exception as exc:
            pack["customer_voice"] = {
                "schema_version": "campaign_ai_customer_voice_evidence_v3",
                "available": False,
                "contracts": {
                    "raw_conversations_included": False,
                    "pii_included": False,
                    "store_or_product_feedback_becomes_campaign_attribution": False,
                    "single_complaint_forces_marketing_action": False,
                },
                "limitations": [f"customer_voice_unavailable:{type(exc).__name__}"],
            }
            limitations = list(pack.get("limitations") or [])
            limitations.append(f"customer_voice_unavailable:{type(exc).__name__}")
            pack["limitations"] = list(dict.fromkeys(limitations))

        try:
            pack["product_change_history"] = await build_product_change_history_evidence(
                db,
                user_id,
                product_ids,
                now=current,
            )
        except Exception as exc:
            pack["product_change_history"] = {
                "schema_version": "campaign_ai_product_change_history_v3",
                "products": {},
                "limitations": [f"product_change_history_unavailable:{type(exc).__name__}"],
            }
            limitations = list(pack.get("limitations") or [])
            limitations.append(f"product_change_history_unavailable:{type(exc).__name__}")
            pack["limitations"] = list(dict.fromkeys(limitations))
        return pack

    return build


__all__ = ["wrap_evidence_builder"]
