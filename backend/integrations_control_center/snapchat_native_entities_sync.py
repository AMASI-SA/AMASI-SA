"""Read-only Snapchat campaign, ad-squad, ad and creative synchronization."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
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
PROPOSAL_COLLECTION = "mezan_snapchat_campaign_proposals_v1"
INTEGRATION_ACCOUNTS_COLLECTION = "mezan_integration_accounts_v2"
PROPOSAL_CORRELATION_HOURS = 24
# Increment whenever monitored controls expand.  The first scan of a new
# version is a silent full baseline so newly introduced fields cannot appear as
# provider changes merely because older rows did not persist them.
PROVIDER_DIFF_BASELINE_VERSION = 3

logger = logging.getLogger(__name__)

ENTITY_ENDPOINTS = (
    ("campaign", "campaigns", "campaign", {}),
    ("ad_squad", "adsquads", "adsquad", {"return_placement_v2": "true"}),
    ("ad", "ads", "ad", {"read_deleted_entities": "true"}),
    ("creative", "creatives", "creative", {}),
)

# Only fields that can be deliberately changed in Ads Manager belong here.
# Delivery/performance fields are intentionally excluded: a normal change in
# impressions, spend, review state or delivery diagnostics must never create a
# decision-history event.
MONITORED_FIELDS: dict[str, tuple[str, ...]] = {
    "campaign": (
        "name",
        "status",
        "deleted",
        "daily_budget_micro",
        "lifetime_spend_cap_micro",
        "start_time",
        "end_time",
        "objective",
        "objective_v2_properties",
        "measurement_spec",
        "regulations",
        "buy_model",
        "pacing_level",
        "shared_properties",
        "product_properties",
    ),
    "ad_squad": (
        "name",
        "status",
        "deleted",
        "type",
        "daily_budget_micro",
        "lifetime_budget_micro",
        "bid_micro",
        "goal",
        "bid_strategy",
        "optimization_goal",
        "billing_event",
        "placement_v2",
        "targeting",
        "conversion_window",
        "pixel_id",
        "start_time",
        "end_time",
        "pacing_type",
        "brand_safety_config",
        "cap_and_exclusion_config",
        "ad_scheduling_config",
        "campaign_budget_optimization_properties",
        "child_ad_type",
        "forced_view_setting",
        "event_sources",
    ),
    "ad": (
        "name",
        "status",
        "deleted",
        "creative_id",
        "type",
        "third_party_on_swipe_tracking_urls",
        "third_party_paid_impression_tracking_urls",
    ),
    "creative": (
        "name",
        "deleted",
        "type",
        "headline",
        "call_to_action",
        "top_snap_media_id",
        "profile_properties",
        "web_view_properties",
        "shareable",
        "brand_name",
        "top_snap_crop_position",
        "forced_view_eligibility",
        "ad_product",
        "cta_color_display_mode",
        "preview_properties",
        "collection_properties",
        "app_install_properties",
        "deep_link_properties",
        "render_type",
    ),
}

_NUMBER_FIELDS = {
    "daily_budget_micro",
    "lifetime_budget_micro",
    "lifetime_spend_cap_micro",
    "bid_micro",
}


def _canonical_structured_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_structured_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        normalized = [_canonical_structured_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
    return value


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
            _safe_provider_value(item, depth=depth + 1) for item in list(value)[:200]
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
        str(entity.get("ad_squad_id") or entity.get("adsquad_id") or "").strip() or None
    )
    if entity_type == "campaign":
        campaign_id = external_id or campaign_id
    if entity_type == "ad_squad":
        ad_squad_id = external_id or ad_squad_id
    return external_id, campaign_id, ad_squad_id


def _normalized_control_value(field: str, value: Any) -> Any:
    if field in _NUMBER_FIELDS:
        return _as_number(value)
    if field == "deleted":
        return value is True
    if field in {
        "status",
        "objective",
        "bid_strategy",
        "optimization_goal",
        "billing_event",
        "type",
        "buy_model",
        "pacing_level",
        "pacing_type",
    }:
        return str(value).strip().upper() if value is not None else None
    if field == "creative_id":
        return str(value or "").strip() or None
    return _canonical_structured_value(_safe_provider_value(value))


def _control_snapshot(entity_type: str, entity: dict[str, Any]) -> dict[str, Any]:
    """Return a stable, performance-free snapshot for decision comparison."""
    snapshot: dict[str, Any] = {
        "id": str(entity.get("id") or entity.get("external_id") or "").strip(),
        "name": entity.get("name") or entity.get("display_name"),
    }
    provider_snapshot = entity.get("provider_snapshot")
    source = provider_snapshot if isinstance(provider_snapshot, dict) else entity
    for field in MONITORED_FIELDS.get(entity_type, ()):
        # Stored entity rows have normalized top-level values. Prefer them so a
        # provider snapshot added by older versions cannot change comparison.
        value = entity.get(field) if field in entity else source.get(field)
        snapshot[field] = _normalized_control_value(field, value)
    return snapshot


def _changed_control_fields(
    entity_type: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    return [
        field
        for field in MONITORED_FIELDS.get(entity_type, ())
        if before.get(field) != after.get(field)
    ]


def _change_fingerprint(
    *,
    account_id: str,
    entity_type: str,
    entity_id: str,
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    changed_fields: list[str],
) -> str:
    payload = {
        "detector_version": PROVIDER_DIFF_BASELINE_VERSION,
        "account_id": account_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before": {field: before_snapshot.get(field) for field in changed_fields},
        "after": {field: after_snapshot.get(field) for field in changed_fields},
        "changed_fields": sorted(changed_fields),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _proposal_matches_change(
    proposal: dict[str, Any],
    *,
    entity_type: str,
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    changed_fields: list[str],
    observed_at: str,
) -> bool:
    if (
        str(proposal.get("status") or "").lower() == "failed"
        and proposal.get("provider_write_reached") is not True
    ):
        return False
    operation = proposal.get("operation")
    operation = operation if isinstance(operation, dict) else {}
    if operation.get("entity_type") != entity_type:
        return False
    action = str(proposal.get("action") or operation.get("action") or "")
    provider_created = changed_fields == ["entity_created"]
    if provider_created:
        if not (
            action.endswith(".create")
            and str(proposal.get("provider_entity_id") or "")
            == str(after_snapshot.get("id") or "")
            and after_snapshot.get("entity_created") is True
        ):
            return False
    else:
        changes = operation.get("changes")
        changes = changes if isinstance(changes, dict) else {}
        monitored_changes = {
            field: _normalized_control_value(field, value)
            for field, value in changes.items()
            if field in MONITORED_FIELDS.get(entity_type, ())
        }
        if set(monitored_changes) != set(changed_fields):
            return False
        if any(
            monitored_changes.get(field) != after_snapshot.get(field)
            for field in changed_fields
        ):
            return False

        original = proposal.get("original_snapshot")
        if isinstance(original, dict):
            original_controls = _control_snapshot(entity_type, original)
            if any(
                original_controls.get(field) != before_snapshot.get(field)
                for field in changed_fields
            ):
                return False

    # A completed proposal is a race candidate only near the provider
    # observation. This prevents an old Mezan transition from hiding an equal
    # direct transition made weeks later. Approved/executing rows without a
    # usable timestamp remain eligible because the write may be in flight.
    proposal_time = _parse_datetime(
        proposal.get("executed_at")
        or proposal.get("execution_started_at")
        or proposal.get("approved_at")
        or proposal.get("created_at")
    )
    observation_time = _parse_datetime(observed_at)
    if proposal_time and observation_time:
        age_hours = abs((observation_time - proposal_time).total_seconds()) / 3600
        if age_hours > PROPOSAL_CORRELATION_HOURS:
            return False
    elif proposal.get("status") == "completed":
        return False
    return True


async def _candidate_management_proposals(
    db: Any,
    *,
    user_id: str,
    account_id: str,
    entity_id: str,
) -> list[dict[str, Any]]:
    collection = _collection(db, PROPOSAL_COLLECTION)
    finder = getattr(collection, "find", None)
    if not callable(finder):
        return []
    cursor = finder(
        {
            "user_id": user_id,
            "account_id": account_id,
            "$or": [
                {"target_id": entity_id},
                {"provider_entity_id": entity_id},
            ],
            "status": {"$in": ["approved", "executing", "completed", "failed"]},
        },
        {"_id": 0},
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("created_at", -1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(25)
    to_list = getattr(cursor, "to_list", None)
    if callable(to_list):
        return list(await to_list(length=25))
    if isinstance(cursor, list):
        return cursor[:25]
    rows: list[dict[str, Any]] = []
    if hasattr(cursor, "__aiter__"):
        async for row in cursor:
            rows.append(row)
            if len(rows) >= 25:
                break
    return rows


async def _matching_management_proposal(
    db: Any,
    *,
    user_id: str,
    account_id: str,
    entity_type: str,
    entity_id: str,
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    changed_fields: list[str],
    observed_at: str,
) -> str | None:
    candidates = await _candidate_management_proposals(
        db,
        user_id=user_id,
        account_id=account_id,
        entity_id=entity_id,
    )
    for proposal in candidates:
        if _proposal_matches_change(
            proposal,
            entity_type=entity_type,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            changed_fields=changed_fields,
            observed_at=observed_at,
        ):
            return str(proposal.get("proposal_id") or "").strip() or None
    return None


async def _record_provider_observed_change(
    db: Any,
    *,
    user_id: str,
    account_id: str,
    entity_type: str,
    entity_id: str,
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    observed_at: str,
    provider_updated_at: Any,
    changed_fields: list[str],
) -> bool:
    """Lazy import keeps the read-only sync independent during rollout."""
    try:
        from .snapchat_decision_ledger import record_provider_observed_decision
    except (ImportError, AttributeError):
        logger.info("Snapchat decision ledger is not installed; change not emitted")
        return False
    await record_provider_observed_decision(
        db,
        user_id,
        account_id=account_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        observed_at=observed_at,
        provider_updated_at=provider_updated_at,
        changed_fields=changed_fields,
        matched_proposal_id=None,
    )
    return True


async def _upsert_entity(
    context: SnapchatSyncContext,
    *,
    account: dict[str, Any],
    entity_type: str,
    entity: dict[str, Any],
    detect_provider_changes: bool = False,
    provider_diff_baseline_ready: bool | None = None,
) -> bool:
    external_id, campaign_id, ad_squad_id = _identity(entity_type, entity)
    if not external_id:
        return False
    now_iso = context.now_iso()
    collection = _collection(context.db, SNAPCHAT_ENTITY_COLLECTION)
    identity = {
        "user_id": context.user_id,
        "ad_account_id": account["ad_account_id"],
        "entity_type": entity_type,
        "external_id": external_id,
    }
    existing: dict[str, Any] | None = None
    if detect_provider_changes and entity_type in MONITORED_FIELDS:
        find_one = getattr(collection, "find_one", None)
        if callable(find_one):
            existing = await find_one(identity, {"_id": 0})
    detector_ready = (
        bool(existing)
        if provider_diff_baseline_ready is None
        else provider_diff_baseline_ready
    )

    pending_changes = [
        item
        for item in ((existing or {}).get("pending_provider_changes") or [])
        if isinstance(item, dict)
    ][:25]
    retained_pending: list[dict[str, Any]] = []
    for pending in pending_changes:
        try:
            emitted = await _record_provider_observed_change(
                context.db,
                user_id=context.user_id,
                account_id=account["ad_account_id"],
                entity_type=str(pending.get("entity_type") or entity_type),
                entity_id=str(pending.get("entity_id") or external_id),
                before_snapshot=dict(pending.get("before_snapshot") or {}),
                after_snapshot=dict(pending.get("after_snapshot") or {}),
                observed_at=str(pending.get("observed_at") or now_iso),
                provider_updated_at=pending.get("provider_updated_at"),
                changed_fields=list(pending.get("changed_fields") or []),
            )
            if emitted is False:
                retained_pending.append(pending)
        except Exception:
            retained_pending.append(pending)
            logger.exception("Failed to retry provider-observed Snapchat change")

    last_change_fingerprint = None
    if existing and detector_ready:
        before_snapshot = _control_snapshot(entity_type, existing)
        after_snapshot = _control_snapshot(entity_type, entity)
        changed_fields = _changed_control_fields(
            entity_type, before_snapshot, after_snapshot
        )
        if changed_fields:
            last_change_fingerprint = _change_fingerprint(
                account_id=account["ad_account_id"],
                entity_type=entity_type,
                entity_id=external_id,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                changed_fields=changed_fields,
            )
            if (
                existing.get("last_provider_change_fingerprint")
                != last_change_fingerprint
            ):
                matched_proposal_id = await _matching_management_proposal(
                    context.db,
                    user_id=context.user_id,
                    account_id=account["ad_account_id"],
                    entity_type=entity_type,
                    entity_id=external_id,
                    before_snapshot=before_snapshot,
                    after_snapshot=after_snapshot,
                    changed_fields=changed_fields,
                    observed_at=now_iso,
                )
                if matched_proposal_id:
                    # A governed write may race the catalogue refresh.  Its
                    # terminal proposal is the authoritative journal source.
                    pass
                else:
                    try:
                        emitted = await _record_provider_observed_change(
                            context.db,
                            user_id=context.user_id,
                            account_id=account["ad_account_id"],
                            entity_type=entity_type,
                            entity_id=external_id,
                            before_snapshot=before_snapshot,
                            after_snapshot=after_snapshot,
                            observed_at=now_iso,
                            provider_updated_at=entity.get("updated_at"),
                            changed_fields=changed_fields,
                        )
                        if emitted is False:
                            raise RuntimeError("decision_ledger_unavailable")
                    except Exception:
                        retained_pending.append(
                            {
                                "fingerprint": last_change_fingerprint,
                                "entity_type": entity_type,
                                "entity_id": external_id,
                                "before_snapshot": before_snapshot,
                                "after_snapshot": after_snapshot,
                                "observed_at": now_iso,
                                "provider_updated_at": entity.get("updated_at"),
                                "changed_fields": changed_fields,
                            }
                        )
                        last_change_fingerprint = None
                        # The provider catalogue remains authoritative.  Keep
                        # the immutable before/after payload for a later retry.
                        logger.exception(
                            "Failed to record provider-observed Snapchat change"
                        )
    elif detect_provider_changes and detector_ready:
        before_snapshot = {
            "id": external_id,
            "name": entity.get("name"),
            "entity_created": False,
        }
        after_snapshot = {
            **_control_snapshot(entity_type, entity),
            "entity_created": True,
        }
        changed_fields = ["entity_created"]
        last_change_fingerprint = _change_fingerprint(
            account_id=account["ad_account_id"],
            entity_type=entity_type,
            entity_id=external_id,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            changed_fields=changed_fields,
        )
        matched_proposal_id = await _matching_management_proposal(
            context.db,
            user_id=context.user_id,
            account_id=account["ad_account_id"],
            entity_type=entity_type,
            entity_id=external_id,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            changed_fields=changed_fields,
            observed_at=now_iso,
        )
        if not matched_proposal_id:
            try:
                emitted = await _record_provider_observed_change(
                    context.db,
                    user_id=context.user_id,
                    account_id=account["ad_account_id"],
                    entity_type=entity_type,
                    entity_id=external_id,
                    before_snapshot=before_snapshot,
                    after_snapshot=after_snapshot,
                    observed_at=now_iso,
                    provider_updated_at=entity.get("updated_at"),
                    changed_fields=changed_fields,
                )
                if emitted is False:
                    raise RuntimeError("decision_ledger_unavailable")
            except Exception:
                retained_pending.append(
                    {
                        "fingerprint": last_change_fingerprint,
                        "entity_type": entity_type,
                        "entity_id": external_id,
                        "before_snapshot": before_snapshot,
                        "after_snapshot": after_snapshot,
                        "observed_at": now_iso,
                        "provider_updated_at": entity.get("updated_at"),
                        "changed_fields": changed_fields,
                    }
                )
                last_change_fingerprint = None
                logger.exception("Failed to record provider-created Snapchat entity")
    deduplicated_pending = list(
        {
            str(
                item.get("fingerprint")
                or _change_fingerprint(
                    account_id=account["ad_account_id"],
                    entity_type=str(item.get("entity_type") or entity_type),
                    entity_id=str(item.get("entity_id") or external_id),
                    before_snapshot=dict(item.get("before_snapshot") or {}),
                    after_snapshot=dict(item.get("after_snapshot") or {}),
                    changed_fields=list(item.get("changed_fields") or []),
                )
            ): item
            for item in retained_pending
        }.values()
    )[-25:]
    set_fields = {
        "user_id": context.user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account["ad_account_id"],
        "mezan_integration_account_id": account.get("mezan_integration_account_id"),
        "entity_type": entity_type,
        "external_id": external_id,
        "campaign_id": campaign_id,
        "ad_squad_id": ad_squad_id,
        "creative_id": str(entity.get("creative_id") or "").strip() or None,
        "deleted": entity.get("deleted") is True,
        "display_name": entity.get("name") or external_id,
        "status": entity.get("status"),
        "delivery_status": entity.get("delivery_status"),
        "review_status": entity.get("review_status"),
        "objective": entity.get("objective"),
        "objective_v2_properties": _safe_provider_value(
            entity.get("objective_v2_properties")
        ),
        "daily_budget_micro": _as_number(entity.get("daily_budget_micro")),
        "lifetime_budget_micro": _as_number(entity.get("lifetime_budget_micro")),
        "lifetime_spend_cap_micro": _as_number(entity.get("lifetime_spend_cap_micro")),
        "bid_micro": _as_number(entity.get("bid_micro")),
        "goal": _safe_provider_value(entity.get("goal")),
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
        **(
            {"last_provider_change_fingerprint": last_change_fingerprint}
            if last_change_fingerprint
            else {}
        ),
        **(
            {"pending_provider_changes": deduplicated_pending}
            if deduplicated_pending
            else {}
        ),
    }
    # Persist every monitored control at the top level as well as in the full
    # provider snapshot.  This keeps old/new comparisons symmetric and makes
    # the detector's coverage mechanically auditable against management fields.
    for field in MONITORED_FIELDS.get(entity_type, ()):
        set_fields[field] = _normalized_control_value(field, entity.get(field))
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now_iso},
    }
    if existing and pending_changes and not deduplicated_pending:
        update["$unset"] = {"pending_provider_changes": ""}
    await collection.update_one(
        identity,
        update,
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
        **extra_params,
    }
    # Snapchat forbids combining read_deleted_entities with sort. Ads use the
    # deleted-aware catalog so performance-only historical Ads retain their
    # exact Ad Squad identity; other entity catalogs keep deterministic sort.
    if "read_deleted_entities" not in params:
        params["sort"] = "updated_at-desc"
    saved = 0
    observed = 0
    errors: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    next_url: str | None = None
    marker_collection = _collection(context.db, INTEGRATION_ACCOUNTS_COLLECTION)
    marker_selector = {
        "user_id": context.user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "$or": [
            {"external_account_id": account["ad_account_id"]},
            {"ad_account_id": account["ad_account_id"]},
        ],
    }
    marker_row = await marker_collection.find_one(
        marker_selector,
        {"_id": 0, "provider_diff_baseline": 1},
    )
    marker = ((marker_row or {}).get("provider_diff_baseline") or {}).get(
        entity_type
    )
    provider_diff_baseline_ready = bool(
        isinstance(marker, dict)
        and int(marker.get("version") or 0) >= PROVIDER_DIFF_BASELINE_VERSION
        and marker.get("ready_at")
    )

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
                    detect_provider_changes=True,
                    provider_diff_baseline_ready=provider_diff_baseline_ready,
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
            if not errors:
                await marker_collection.update_one(
                    marker_selector,
                    {
                        "$set": {
                            f"provider_diff_baseline.{entity_type}": {
                                "version": PROVIDER_DIFF_BASELINE_VERSION,
                                "ready_at": context.now_iso(),
                            }
                        }
                    },
                )
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


async def sync_snapchat_ad_entities(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
) -> tuple[int, dict[str, int], list[dict[str, str]]]:
    """Refresh exact provider ad identities without touching performance facts."""
    ad_endpoint = next(
        (endpoint for endpoint in ENTITY_ENDPOINTS if endpoint[0] == "ad"),
        None,
    )
    if ad_endpoint is None:
        return 0, {"ad": 0}, [{"kind": "ad", "error": "endpoint_missing"}]

    entity_type, plural_key, singular_key, extra_params = ad_endpoint
    try:
        saved, observed, errors = await _sync_entity_type(
            context,
            client,
            access_token,
            account,
            entity_type=entity_type,
            plural_key=plural_key,
            singular_key=singular_key,
            extra_params=extra_params,
        )
        return saved, {entity_type: observed}, errors
    except SnapchatNativeSyncError as exc:
        if exc.code == "snapchat_needs_reauth":
            raise
        return 0, {entity_type: 0}, [{"kind": entity_type, "error": exc.code}]


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
    "sync_snapchat_ad_entities",
    "sync_snapchat_entities",
]
