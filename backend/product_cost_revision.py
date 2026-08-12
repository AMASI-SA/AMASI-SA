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
]
