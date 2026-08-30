"""Provider-neutral attribution projection for the canonical Order Engine."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from salla_marketing_attribution import field_values

RIYADH = ZoneInfo("Asia/Riyadh")
SNAPCHAT_ACCOUNT = ZoneInfo("America/New_York")


def _first(order: dict[str, Any], *names: str) -> str | None:
    values = field_values(order, *names)
    return values[0] if values else None


def _normalized(value: str | None) -> str | None:
    text = " ".join(str(value or "").strip().casefold().split())
    return text or None


def _click_ids(order: dict[str, Any]) -> dict[str, str]:
    aliases = {
        "sc_click_id": ("sc_click_id", "scclid", "snap_click_id"),
        "sc_cookie1": ("sc_cookie1",),
        "fbclid": ("fbclid",),
        "gclid": ("gclid",),
        "ttclid": ("ttclid",),
    }
    return {key: value for key, names in aliases.items() if (value := _first(order, *names))}


def build_attribution_projection(
    order: dict[str, Any], *, created_at: datetime, provider: str | None,
    campaign_id: str | None, campaign_name: str | None,
) -> dict[str, Any]:
    """Preserve raw evidence and derive a separate normalized read projection."""
    raw = {
        "source": _first(order, "utm_source"),
        "medium": _first(order, "utm_medium", "medium"),
        "campaign": _first(order, "utm_campaign"),
        "content": _first(order, "utm_content"),
        "term": _first(order, "utm_term"),
    }
    squad_id = _first(order, "ad_squad_id", "adsquad_id", "adset_id", "ad_group_id")
    squad_name = _first(order, "ad_squad_name", "adsquad_name", "adset_name", "ad_group_name")
    ad_id = _first(order, "ad_id", "creative_id")
    ad_name = _first(order, "ad_name", "creative_name")
    conflicted = bool(_first(order, "attribution_conflict", "campaign_conflict"))
    matched = bool(campaign_id or campaign_name) and not conflicted
    match_status = "conflicted" if conflicted else "matched" if matched else "unattributed"
    match_method = _first(order, "attribution_match_method", "match_method")
    if not match_method and matched:
        match_method = "campaign_id" if campaign_id else "unique_campaign_name"
    reason = None
    if conflicted:
        reason = _first(order, "attribution_conflict_reason", "unmatched_reason") or "conflicting_provider_evidence"
    elif not matched:
        reason = "campaign_identity_missing" if provider else "advertising_source_missing"
    params = {"provider": provider or "", "campaign_id": campaign_id or ""}
    if squad_id:
        params["ad_squad_id"] = squad_id
    if ad_id:
        params["ad_id"] = ad_id
    return {
        "utm_content": raw["content"], "utm_term": raw["term"], "utm_raw": raw,
        "utm_normalized": {key: _normalized(value) for key, value in raw.items()},
        "click_ids": _click_ids(order), "ad_squad_id": squad_id,
        "ad_squad_name": squad_name, "ad_id": ad_id, "ad_name": ad_name,
        "match_status": match_status, "match_method": match_method,
        "match_confidence": 1.0 if campaign_id and not conflicted else 0.85 if campaign_name and not conflicted else 0.0,
        "unmatched_reason": reason,
        "attribution_window": _first(order, "attribution_window") or ("28d_click/1d_view" if provider == "snapchat" else None),
        "order_created_at_riyadh": created_at.astimezone(RIYADH),
        "order_created_at_account": created_at.astimezone(SNAPCHAT_ACCOUNT) if provider == "snapchat" else None,
        "account_timezone": "America/New_York" if provider == "snapchat" else None,
        "entity_url": f"/snapchat-accounts?{urlencode(params)}" if provider == "snapchat" and matched else None,
    }
