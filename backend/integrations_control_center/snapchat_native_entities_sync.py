"""Read-only Snapchat campaign, ad-squad, ad and creative synchronization."""
from __future__ import annotations

from typing import Any

import httpx

from .snapchat_native_data_common import (
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

ENTITY_PAGE_SIZE = 1000
MAX_ENTITY_PAGES = 50
MAX_ENTITY_ROWS_PER_TYPE = 50_000

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
            if any(
                fragment in normalized
                for fragment in (
                    "access_token",
                    "refresh_token",
                    "client_secret",
                    "authorization",
                    "password",
                    "credential",
                    "ciphertext",
                )
            ):
                continue
            safe[str(key)] = _safe_provider_value(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [
            _safe_provider_value(item, depth=depth + 1)
            for item in list(value)[:200]
        ]
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def _identity(
    entity_type: str,
    entity: dict[str, Any],
) -> tuple[str, str | None, str | None]:
    external_id = str(entity.get("id") or "").strip()
    campaign_id = str(entity.get("campaign_id") or "").strip() or None
    ad_squad_id = (
        str(entity.get("ad_squad_id") or entity.get("adsquad_id") or "").strip()
        or None
    )
    if entity_type == "campaign":
        campaign_id = external_id or campaign_id
    if entity_type == "ad_squad":
        ad_squad_id = external_id or ad_squad_id
    return external_id, campaign_id, ad_squad_id


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
                "mezan_integration_account_id": account.get(
                    "mezan_integration_account_id"
                ),
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
                "objective_v2_properties": _safe_provider_value(
                    entity.get("objective_v2_properties")
                ),
                "daily_budget_micro": _as_number(entity.get("daily_budget_micro")),
                "lifetime_spend_cap_micro": _as_number(
                    entity.get("lifetime_spend_cap_micro")
                ),
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


async def _sync_entity_type(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *,
    entity_type: str,
    plural_key: str,
    singular_key: str,
    extra_params: dict[str, Any],
) -> tuple[int, int, list[dict[str, str]]]:
    """Stream provider pages and persist each unique entity immediately."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account['ad_account_id']}/{plural_key}"
    params: dict[str, Any] | None = {
        "limit": ENTITY_PAGE_SIZE,
        "sort": "updated_at-desc",
        **extra_params,
    }
    saved = 0
    observed = 0
    errors: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    next_url: str | None = None

    for page_number in range(1, MAX_ENTITY_PAGES + 1):
        payload = await context.get_json(client, url, headers=headers, params=params)
        wrapped_rows = payload.get(plural_key) or []
        if not isinstance(wrapped_rows, list):
            raise SnapchatNativeSyncError(
                "snapchat_entity_payload_invalid",
                f"Snapchat returned invalid {plural_key} data.",
                status_code=502,
                retryable=True,
            )

        row_limit_reached = False
        for wrapped in wrapped_rows:
            if not isinstance(wrapped, dict):
                continue
            status = str(wrapped.get("sub_request_status") or "SUCCESS").upper()
            if "FAIL" in status or "ERROR" in status:
                errors.append({"kind": plural_key, "error": status[:80]})
                continue
            entity = wrapped.get(singular_key, wrapped)
            if not isinstance(entity, dict):
                continue
            external_id = str(entity.get("id") or "").strip()
            if not external_id or external_id in seen_ids:
                continue
            if observed >= MAX_ENTITY_ROWS_PER_TYPE:
                row_limit_reached = True
                break
            seen_ids.add(external_id)
            observed += 1
            saved += int(
                await _upsert_entity(
                    context,
                    account=account,
                    entity_type=entity_type,
                    entity=entity,
                )
            )

        raw_next = (payload.get("paging") or {}).get("next_link")
        next_url = _safe_next_url(raw_next)
        if raw_next and not next_url:
            errors.append(
                {
                    "kind": plural_key,
                    "error": "entity_paging_untrusted",
                    "page": str(page_number),
                }
            )
            break
        if row_limit_reached or (
            observed >= MAX_ENTITY_ROWS_PER_TYPE and next_url is not None
        ):
            errors.append(
                {
                    "kind": plural_key,
                    "error": "entity_row_limit_reached",
                    "rows_observed": str(observed),
                    "row_limit": str(MAX_ENTITY_ROWS_PER_TYPE),
                    "next_page_present": str(next_url is not None).lower(),
                }
            )
            break
        if not next_url:
            return saved, observed, errors
        url, params = next_url, None
    else:
        if next_url:
            errors.append(
                {
                    "kind": plural_key,
                    "error": "entity_page_limit_reached",
                    "pages_fetched": str(MAX_ENTITY_PAGES),
                    "next_page_present": "true",
                }
            )

    return saved, observed, errors


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
            entity_saved, observed, entity_errors = await _sync_entity_type(
                context,
                client,
                access_token,
                account,
                entity_type=entity_type,
                plural_key=plural_key,
                singular_key=singular_key,
                extra_params=extra_params,
            )
            saved += entity_saved
            counts[entity_type] = observed
            errors.extend(entity_errors)
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            counts[entity_type] = 0
            errors.append({"kind": entity_type, "error": exc.code})
    return saved, counts, errors


__all__ = [
    "ENTITY_ENDPOINTS",
    "ENTITY_PAGE_SIZE",
    "MAX_ENTITY_PAGES",
    "MAX_ENTITY_ROWS_PER_TYPE",
    "sync_snapchat_entities",
]
