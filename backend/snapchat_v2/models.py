"""Shared contracts for the isolated Snapchat reporting V2 data plane."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

SNAPCHAT_PROVIDER = "snapchat_ads"
DEFAULT_SWIPE_ATTRIBUTION_WINDOW = "28_DAY"
DEFAULT_VIEW_ATTRIBUTION_WINDOW = "7_DAY"

EntityType = Literal["ad_account", "campaign", "ad_squad", "ad"]
SyncState = Literal[
    "pending",
    "running",
    "complete",
    "partial",
    "failed",
    "skipped",
    "abandoned",
]
CoverageState = Literal[
    "confirmed_data",
    "confirmed_zero",
    "confirmed_no_data",
    "unknown_incomplete",
]


class SnapchatHourlyFact(TypedDict, total=False):
    user_id: str
    provider: str
    ad_account_id: str
    entity_type: EntityType
    external_id: str
    campaign_id: str | None
    ad_squad_id: str | None
    ad_id: str | None
    hour_start_utc: datetime
    hour_end_utc: datetime
    account_timezone: str
    currency: str
    action_report_time: str
    attribution_windows: dict[str, Any]
    attribution_key: str
    spend_native: float
    impressions: int
    swipes: int
    video_views: int
    purchases: int
    purchase_value_native: float
    coverage: dict[str, Any]
    source: dict[str, Any]
    provisional: bool
    sync_run_id: str
    created_at: datetime
    updated_at: datetime


class SnapchatAccountRecord(TypedDict, total=False):
    user_id: str
    provider: str
    ad_account_id: str
    display_name: str
    currency: str
    timezone: str
    permissions: list[str]
    organization_id: str | None
    organization_name: str | None
    account_status: str | None
    connection_status: str
    selected: bool
    active: bool
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def clean_text(value: Any, *, limit: int = 300) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def build_attribution_key(
    action_report_time: str,
    attribution_windows: dict[str, Any] | None,
) -> str:
    payload = {
        "action_report_time": clean_text(action_report_time, limit=32).lower()
        or "conversion",
        "attribution_windows": attribution_windows or {},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def derive_entity_identity(fact: dict[str, Any]) -> tuple[EntityType, str]:
    ad_id = clean_text(fact.get("ad_id"), limit=128)
    if ad_id:
        return "ad", ad_id
    ad_squad_id = clean_text(fact.get("ad_squad_id"), limit=128)
    if ad_squad_id:
        return "ad_squad", ad_squad_id
    campaign_id = clean_text(fact.get("campaign_id"), limit=128)
    if campaign_id:
        return "campaign", campaign_id
    account_id = clean_text(fact.get("ad_account_id"), limit=128)
    if not account_id:
        raise ValueError("ad_account_id is required")
    return "ad_account", account_id
