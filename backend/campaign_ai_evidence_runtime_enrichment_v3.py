"""Runtime enrichment hook for Decision Intelligence V3 evidence packs."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from campaign_ai_product_change_history_v3 import (
    build_product_change_history_evidence,
)


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
        product_ids: list[str] = []
        for block in (((pack.get("product_intelligence") or {}).get("entities") or {}).values()):
            for product in block.get("products") or []:
                if product.get("product_id") is not None:
                    product_ids.append(str(product["product_id"]))
        try:
            pack["product_change_history"] = await build_product_change_history_evidence(
                db,
                user_id,
                product_ids,
                now=kwargs.get("current"),
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
