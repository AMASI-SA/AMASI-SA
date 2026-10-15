"""Shared revision for cached reports that depend on current product costs.

The revision lives in Mongo rather than process memory so every application
worker observes a successful Products V2 cost write before serving another
cached profitability response.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


PRODUCT_COST_REVISIONS = "mezan_product_cost_revisions_v2"


def _revision(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _revision_id(user_id: str) -> str:
    # Mongo's unique _id makes the first concurrent upsert safe without
    # requiring an index-creation round trip on every application worker.
    return f"product-cost:{str(user_id)}"


def _cost_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "total"):
            if key in value:
                parsed = _cost_number(value.get(key))
                if parsed is not None:
                    return parsed
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def salla_cost_fingerprint(product: dict[str, Any] | None) -> tuple[Any, ...]:
    """Return the Salla costs currently stored in the canonical Mezan record."""
    product = product or {}
    base = _cost_number(product.get("cost_price_from_salla"))
    variants: list[tuple[str, float | None]] = []
    for index, variant in enumerate(product.get("variants") or []):
        if not isinstance(variant, dict):
            continue
        identity = str(variant.get("id") or variant.get("sku") or index)
        cost = _cost_number(
            variant.get("cost_price_from_salla")
            if variant.get("cost_price_from_salla") not in (None, "")
            else variant.get("cost_price")
            if variant.get("cost_price") not in (None, "")
            else variant.get("cost")
        )
        if cost is not None:
            variants.append((identity, cost))
    return (base, *sorted(variants, key=lambda row: row[0]))


async def get_product_cost_revision(db: Any, user_id: str) -> int:
    row = await db[PRODUCT_COST_REVISIONS].find_one(
        {"_id": _revision_id(user_id)},
        {"_id": 0, "revision": 1},
    )
    return _revision((row or {}).get("revision"))


async def bump_product_cost_revision(
    db: Any,
    user_id: str,
    *,
    session: Any = None,
) -> None:
    """Invalidate cost-dependent caches after the authoritative write commits."""
    now = datetime.now(timezone.utc)
    options: dict[str, Any] = {"upsert": True}
    if session is not None:
        # Supplier receiving writes costs inside a Mongo transaction. Keeping
        # the revision in the same session means rollback cannot publish a
        # cache revision for costs that never committed.
        options["session"] = session
    await db[PRODUCT_COST_REVISIONS].update_one(
        {"_id": _revision_id(user_id)},
        {
            "$inc": {"revision": 1},
            "$set": {"updated_at": now},
            "$setOnInsert": {
                "user_id": str(user_id),
                "created_at": now,
            },
        },
        **options,
    )


__all__ = [
    "PRODUCT_COST_REVISIONS",
    "bump_product_cost_revision",
    "get_product_cost_revision",
    "salla_cost_fingerprint",
]
