"""Idempotent indexes for bounded Snapchat V2 read paths."""
from __future__ import annotations

from typing import Any, Iterable

from pymongo.errors import OperationFailure

from integrations_control_center.snapchat_native_data_common import SNAPCHAT_ENTITY_COLLECTION

from .entities import SNAPCHAT_ENTITY_FACTS_COLLECTION
from .facts import SNAPCHAT_HOURLY_FACTS_COLLECTION


def _field_prefix_covers(index: dict[str, Any], fields: Iterable[str]) -> bool:
    if index.get("hidden") or index.get("partialFilterExpression") or index.get("sparse"):
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


async def _ensure(collection: Any, keys: list[tuple[str, int]], name: str) -> bool:
    fields = [field for field, _direction in keys]

    async def covered() -> bool:
        indexes = await collection.index_information()
        return any(_field_prefix_covers(index, fields) for index in indexes.values())

    if await covered():
        return False
    try:
        await collection.create_index(keys, name=name)
    except OperationFailure:
        if await covered():
            return False
        raise
    return True


async def ensure_snapchat_v2_read_indexes(db: Any) -> dict[str, bool]:
    base = [
        ("user_id", 1),
        ("provider", 1),
        ("ad_account_id", 1),
        ("entity_type", 1),
    ]
    entity = db[SNAPCHAT_ENTITY_FACTS_COLLECTION]
    return {
        "latest_hourly_fact_created": await _ensure(
            db[SNAPCHAT_HOURLY_FACTS_COLLECTION],
            [
                ("user_id", 1),
                ("provider", 1),
                ("ad_account_id", 1),
                ("updated_at", -1),
            ],
            "snapchat_v2_hourly_account_updated_latest",
        ),
        "campaign_page_created": await _ensure(
            entity,
            [*base, ("active", -1), ("name", 1), ("external_id", 1)],
            "snapchat_v2_campaign_page",
        ),
        "ad_squad_parent_page_created": await _ensure(
            entity,
            [*base, ("campaign_id", 1), ("active", -1), ("name", 1), ("external_id", 1)],
            "snapchat_v2_ad_squad_parent_page",
        ),
        "ad_parent_page_created": await _ensure(
            entity,
            [*base, ("ad_squad_id", 1), ("active", -1), ("name", 1), ("external_id", 1)],
            "snapchat_v2_ad_parent_page",
        ),
        "settings_visible_ids_created": await _ensure(
            db[SNAPCHAT_ENTITY_COLLECTION],
            [("user_id", 1), ("provider", 1), ("entity_type", 1), ("external_id", 1)],
            "snapchat_settings_visible_ids",
        ),
        "settings_campaign_children_created": await _ensure(
            db[SNAPCHAT_ENTITY_COLLECTION],
            [
                ("user_id", 1),
                ("provider", 1),
                ("entity_type", 1),
                ("ad_account_id", 1),
                ("campaign_id", 1),
                ("last_observed_at", -1),
            ],
            "snapchat_settings_campaign_children",
        ),
    }


__all__ = ["ensure_snapchat_v2_read_indexes"]
