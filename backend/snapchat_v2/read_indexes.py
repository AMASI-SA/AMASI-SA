"""Idempotent indexes for Snapchat V2 read paths."""
from __future__ import annotations

from typing import Any, Iterable

from pymongo.errors import OperationFailure

from .facts import SNAPCHAT_HOURLY_FACTS_COLLECTION


def _field_prefix_covers(
    index: dict[str, Any],
    fields: Iterable[str],
    *,
    allow_filtered: bool,
) -> bool:
    if index.get("hidden"):
        return False
    if not allow_filtered and (
        index.get("partialFilterExpression") or index.get("sparse")
    ):
        return False
    collation = index.get("collation") or {}
    if collation and collation.get("locale") not in {None, "simple"}:
        return False
    key_items = list(index.get("key", []))
    if any(direction not in {1, -1} for _field, direction in key_items):
        return False
    keys = [str(field) for field, _direction in key_items]
    required = [str(field) for field in fields]
    return keys[: len(required)] == required


async def _ensure_covering_index(
    collection: Any,
    *,
    keys: list[tuple[str, int]],
    name: str,
    allow_filtered: bool = False,
) -> bool:
    fields = [field for field, _direction in keys]
    async def has_covering_index() -> bool:
        indexes = await collection.index_information()
        return any(
            _field_prefix_covers(
                index,
                fields,
                allow_filtered=allow_filtered,
            )
            for index in indexes.values()
        )

    if await has_covering_index():
        return False
    try:
        await collection.create_index(keys, name=name)
    except OperationFailure:
        if await has_covering_index():
            return False
        raise
    return True


async def ensure_snapchat_v2_read_indexes(db: Any) -> dict[str, bool]:
    """Inspect existing shapes first, then create only missing read indexes."""
    users_created = await _ensure_covering_index(
        db.users,
        keys=[("id", 1)],
        name="users_id_lookup",
        allow_filtered=True,
    )
    latest_fact_created = await _ensure_covering_index(
        db[SNAPCHAT_HOURLY_FACTS_COLLECTION],
        keys=[
            ("user_id", 1),
            ("provider", 1),
            ("ad_account_id", 1),
            ("updated_at", -1),
        ],
        name="snapchat_v2_hourly_account_updated_latest",
    )
    return {
        "users_id_lookup_created": users_created,
        "latest_hourly_fact_created": latest_fact_created,
    }


__all__ = ["ensure_snapchat_v2_read_indexes"]
