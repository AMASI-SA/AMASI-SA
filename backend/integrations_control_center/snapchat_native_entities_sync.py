"""Read-only Snapchat campaign, ad-squad, ad and creative synchronization."""
from __future__ import annotations

from typing import Any

import httpx

from .snapchat_native_data_common import (
    MAX_ENTITY_ROWS_PER_TYPE,
    MAX_PAGES,
    SNAPCHAT_API_BASE,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _as_number,
    _collection,
    _safe_next_url,
)

ENTITY_ENDPOINTS = (
    ("campaign", "campaigns", "campaign", {}),
    ("ad_squad", "adsquads", "adsquad", {"return_placement_v2": "true"}),
    ("ad", "ads", "ad", {}),
    ("creative", "creatives", "creative", {}),
)


def _safe_provider_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 7:
        return None
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 200:
                break
            normalized = str(key or "").lower().replace("-", "_")
            if any(fragment in normalized for fragment in (
                "access_token", "refresh_token", "client_secret", "authorization",
                "password", "credential", "ciphertext",
            )):
                continue
            safe[str(key)] = _safe_provider_value(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_provider_value(item, depth=depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def _identity(entity_type: str, entity: dict[str, Any]) -> tuple[str, str | None, str | None]:
    external_id = str(entity.get("id") or "").strip()
    campaign_id = str(entity.get("campaign_id") or "").strip() or None
    ad_squad_id = str(entity.get("ad_squad_id") or entity.get("adsquad_id") or "").strip() or None
    if entity_type == "campaign":
        campaign_id = external_id or campaign_id
    if entity_type == "ad_squad":
        ad_squad_id = external_id or ad_squad_id
    return external_id, campaign_id, ad_squad_id


async def _fetch_entities(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account_id: str,
    *,
    plural_key: str,
    singular_key: str,
    extra_params: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/{plural_key}"
    params: dict[str, Any] | None = {"limit": 1000, "sort": "updated_at-desc", **extra_params}
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for _ in range(MAX_PAGES):
        payload = await context.get_json(client, url, headers=headers, params=params)
        wrapped_rows = payload.get(plural_key) or []
        if not isinstance(wrapped_rows, list):
            raise SnapchatNativeSyncError(
                "snapchat_entity_payload_invalid",
                f"Snapchat returned invalid {plural_key} data.",
                status_code=502, retryable=True,
            )
        for wrapped in wrapped_rows:
            if not isinstance(wrapped, dict):
                continue
            status = str(wrapped.get("sub_request_status") or "SUCCESS").upper()
            if "FAIL" in status or "ERROR" in status:
                errors.append({"kind": plural_key, "error": status[:80]})
                continue
            entity = wrapped.get(singular_key, wrapped)
            if isinstance(entity, dict) and entity.get("id"):
                rows.append(entity)
                if len(rows) >= MAX_ENTITY_ROWS_PER_TYPE:
                    return rows[:MAX_ENTITY_ROWS_PER_TYPE], errors
        next_url = _safe_next_url((payload.get("paging") or {}).get("next_link"))
        if not next_url:
            break
        url, params = next_url, None
    return rows[:MAX_ENTITY_ROWS_PER_TYPE], errors


async def _upsert_entity(
    context: SnapchatSyncContext,
    *,
    account: dict[str, Any],
    entity_type: str,
    entity: dict[str, Any],
) -> bool:
    external_id, campaign_id, ad_squad_id = _identity(entity_type, entity)
    if not external_id:
        return False
    now_iso = context.now_iso()
    await _collection(context.db, SNAPCHAT_ENTITY_COLLECTION).update_one(
        {
            "user_id": context.user_id,
            "ad_account_id": account["ad_account_id"],
            "entity_type": entity_type,
            "external_id": external_id,
        },
        {
            "$set": {
                "user_id": context.user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "ad_account_id": account["ad_account_id"],
                "mezan_integration_account_id": account.get("mezan_integration_account_id"),
                "entity_type": entity_type,
                "external_id": external_id,
                "campaign_id": campaign_id,
                "ad_squad_id": ad_squad_id,
                "creative_id": str(entity.get("creative_id") or "").strip() or None,
                "display_name": entity.get("name") or external_id,
                "status": entity.get("status"),
                "delivery_status": entity.get("delivery_status"),
                "review_status": entity.get("review_status"),
                "objective": entity.get("objective"),
                "objective_v2_properties": _safe_provider_value(entity.get("objective_v2_properties")),
                "daily_budget_micro": _as_number(entity.get("daily_budget_micro")),
                "lifetime_spend_cap_micro": _as_number(entity.get("lifetime_spend_cap_micro")),
                "bid_micro": _as_number(entity.get("bid_micro")),
                "bid_strategy": entity.get("bid_strategy"),
                "optimization_goal": entity.get("optimization_goal"),
                "billing_event": entity.get("billing_event"),
                "placement_v2": _safe_provider_value(entity.get("placement_v2")),
                "targeting": _safe_provider_value(entity.get("targeting")),
                "start_time": entity.get("start_time"),
                "end_time": entity.get("end_time"),
                "created_at_provider": entity.get("created_at"),
                "updated_at_provider": entity.get("updated_at"),
                "provider_snapshot": _safe_provider_value(entity),
                "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
                "last_observed_at": now_iso,
                "updated_at": now_iso,
            },
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )
    return True


async def sync_snapchat_entities(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
) -> tuple[int, dict[str, int], list[dict[str, str]]]:
    saved = 0
    counts: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    for entity_type, plural_key, singular_key, extra_params in ENTITY_ENDPOINTS:
        try:
            entities, entity_errors = await _fetch_entities(
                context, client, access_token, account["ad_account_id"],
                plural_key=plural_key, singular_key=singular_key,
                extra_params=extra_params,
            )
            counts[entity_type] = len(entities)
            errors.extend(entity_errors)
            for entity in entities:
                saved += int(await _upsert_entity(
                    context, account=account, entity_type=entity_type, entity=entity
                ))
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            counts[entity_type] = 0
            errors.append({"kind": entity_type, "error": exc.code})
    return saved, counts, errors


__all__ = ["ENTITY_ENDPOINTS", "sync_snapchat_entities"]
