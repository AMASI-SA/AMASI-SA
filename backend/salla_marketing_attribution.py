"""Safe Salla marketing-attribution extraction.

Fresh Salla orders are stored with their authoritative payload under
``raw_by_source.salla_direct``.  Operational order fields intentionally keep
``source=salla_direct`` to describe the ingestion channel, so advertising
reports must read the separate marketing evidence instead of treating the
ingestion source as the ad platform.

Only explicit attribution containers and fields are inspected here.  Customer,
address, payment and product data are never traversed or projected.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


_CONTAINER_KEYS = (
    "utm",
    "marketing",
    "attribution",
    "tracking",
    "traffic",
    "metadata",
    "meta",
)

_SOURCE_FIELDS = (
    "utm_source",
    "source_native",
    "order_source",
    "traffic_source",
    "marketing_source",
    "source_name",
    "ad_platform_source",
    "platform",
    "channel",
    "created_via",
    "created_by_type",
)

_SOURCE_OBJECT_FIELDS = (
    "source",
    "channel",
    "platform",
    "name",
    "label",
    "value",
    "slug",
)

_INTERNAL_INGESTION_SOURCES = {
    "salla direct",
    "salla_direct",
    "make",
    "excel",
    "custom app",
    "custom_app",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _append_unique(result: list[str], values: Iterable[Any]) -> None:
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)


def _base_containers(order: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate in (
        order,
        _dict(_dict(order.get("raw_by_source")).get("salla_direct")),
    ):
        if candidate and all(candidate is not item for item in result):
            result.append(candidate)
    return result


def attribution_containers(order: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only whitelisted marketing containers, with bounded nesting."""
    result: list[dict[str, Any]] = []
    queue: list[tuple[dict[str, Any], int]] = [
        (container, 0) for container in _base_containers(order)
    ]
    while queue:
        container, depth = queue.pop(0)
        if any(container is item for item in result):
            continue
        result.append(container)
        if depth >= 3:
            continue
        for key in _CONTAINER_KEYS:
            child = _dict(container.get(key))
            if child:
                queue.append((child, depth + 1))
        source = _dict(container.get("source"))
        if source:
            queue.append((source, depth + 1))
    return result


def field_values(order: dict[str, Any], *fields: str) -> list[str]:
    result: list[str] = []
    for container in attribution_containers(order):
        _append_unique(result, (container.get(field) for field in fields))
    return result


def nested_field_values(
    order: dict[str, Any],
    container_key: str,
    *fields: str,
) -> list[str]:
    result: list[str] = []
    for container in attribution_containers(order):
        child = _dict(container.get(container_key))
        _append_unique(result, (child.get(field) for field in fields))
    return result


def order_source_candidates(order: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for container in attribution_containers(order):
        _append_unique(result, (container.get(field) for field in _SOURCE_FIELDS))
        source = container.get("source")
        if isinstance(source, dict):
            _append_unique(result, (source.get(field) for field in _SOURCE_OBJECT_FIELDS))
        else:
            _append_unique(result, (source,))
    return result


def meaningful_source_label(order: dict[str, Any]) -> str:
    """Return source evidence suitable for the audit UI, not ingestion labels."""
    for candidate in order_source_candidates(order):
        normalized = " ".join(candidate.casefold().replace("_", " ").split())
        if normalized not in _INTERNAL_INGESTION_SOURCES:
            return candidate
    return ""


def canonical_ad_platform(order: dict[str, Any]) -> str | None:
    """Resolve only proven advertising platforms; never infer from purchases."""
    for candidate in order_source_candidates(order):
        normalized = re.sub(r"[_\-./]+", " ", candidate.casefold())
        normalized = " ".join(normalized.split())
        compact = normalized.replace(" ", "")
        words = set(normalized.split())
        if (
            "snapchat" in compact
            or "سنابشات" in compact
            or "snap" in words
            or "سناب" in words
        ):
            return "snapchat"
        if "tiktok" in compact or "تيكتوك" in compact or "تيك توك" in normalized:
            return "tiktok"
        if (
            "instagram" in compact
            or "انستقرام" in compact
            or "انستغرام" in compact
            or "facebook" in compact
            or "فيسبوك" in compact
            or normalized == "fb"
            or "meta" in words
            or "ميتا" in words
        ):
            return "meta"
        if "google" in compact or "adwords" in compact or "جوجل" in compact or "قوقل" in compact:
            return "google"
    return None


def campaign_id_candidates(order: dict[str, Any]) -> list[str]:
    result = field_values(
        order,
        "campaign_id",
        "source_campaign_id",
        "utm_campaign_id",
        "ad_campaign_id",
    )
    for base in attribution_containers(order):
        campaign = _dict(base.get("campaign"))
        _append_unique(result, (campaign.get("id"), campaign.get("external_id")))
    # Salla integrations commonly place either the campaign ID or name in
    # utm_campaign.  The matcher checks identities, so trying it as an ID first
    # cannot manufacture a match.
    _append_unique(result, field_values(order, "utm_campaign", "campaign"))
    return result


def campaign_name_candidates(order: dict[str, Any]) -> list[str]:
    result = field_values(
        order,
        "campaign_name",
        "source_campaign_name",
        "ad_campaign_name",
    )
    for base in attribution_containers(order):
        campaign = _dict(base.get("campaign"))
        _append_unique(
            result,
            (
                campaign.get("name"),
                campaign.get("display_name"),
                campaign.get("title"),
                campaign.get("label"),
            ),
        )
    _append_unique(result, field_values(order, "utm_campaign", "campaign"))
    return result


def promoted_salla_attribution(order: dict[str, Any]) -> dict[str, str]:
    """Promote stable marketing fields for future order reads."""
    result: dict[str, str] = {}
    mappings = {
        "utm_source": ("utm_source",),
        "utm_medium": ("utm_medium", "medium"),
        "utm_campaign": ("utm_campaign",),
        "campaign_id": (
            "campaign_id",
            "source_campaign_id",
            "utm_campaign_id",
            "ad_campaign_id",
        ),
        "campaign_name": (
            "campaign_name",
            "source_campaign_name",
            "ad_campaign_name",
        ),
        "traffic_source": ("traffic_source",),
        "marketing_source": ("marketing_source",),
    }
    for target, aliases in mappings.items():
        values = field_values(order, *aliases)
        if not values and target == "utm_source":
            values = nested_field_values(order, "utm", "source", "utm_source")
        elif not values and target == "utm_medium":
            values = nested_field_values(order, "utm", "medium", "utm_medium")
        elif not values and target == "utm_campaign":
            values = nested_field_values(order, "utm", "campaign", "utm_campaign")
        if values:
            result[target] = values[0]

    if not result.get("campaign_name"):
        values = campaign_name_candidates(order)
        if values:
            result["campaign_name"] = values[0]

    source_label = meaningful_source_label(order)
    if source_label:
        result.setdefault("source_native", source_label)
        result.setdefault("marketing_source", source_label)
    platform = canonical_ad_platform(order)
    if platform:
        result["ad_platform_source"] = platform
    return result


# Safe second-query projection used to enrich existing orders.  Every included
# leaf is marketing metadata; no whole metadata object and no customer,
# address, payment or product field can leave MongoDB through this projection.
_PROJECTED_ATTRIBUTION_FIELDS = (
    *_SOURCE_FIELDS,
    "source",
    "utm_medium",
    "utm_campaign",
    "campaign",
    "medium",
    "campaign_id",
    "campaign_name",
    "source_campaign_id",
    "source_campaign_name",
    "utm_campaign_id",
    "ad_campaign_id",
    "ad_campaign_name",
)
_PROJECTED_OBJECT_FIELDS = (
    *_SOURCE_OBJECT_FIELDS,
    "id",
    "external_id",
    "display_name",
    "title",
    "campaign_id",
    "campaign_name",
)
_RAW_PREFIX = "raw_by_source.salla_direct"
SALLA_RAW_ATTRIBUTION_PROJECTION: dict[str, int] = {
    "_id": 0,
    "order_number": 1,
}
for _field in _PROJECTED_ATTRIBUTION_FIELDS:
    SALLA_RAW_ATTRIBUTION_PROJECTION[f"{_RAW_PREFIX}.{_field}"] = 1
for _container in _CONTAINER_KEYS:
    for _field in _PROJECTED_ATTRIBUTION_FIELDS:
        SALLA_RAW_ATTRIBUTION_PROJECTION[
            f"{_RAW_PREFIX}.{_container}.{_field}"
        ] = 1
    for _object_key in ("source", "campaign"):
        for _field in _PROJECTED_OBJECT_FIELDS:
            SALLA_RAW_ATTRIBUTION_PROJECTION[
                f"{_RAW_PREFIX}.{_container}.{_object_key}.{_field}"
            ] = 1
for _object_key in ("source", "campaign"):
    for _field in _PROJECTED_OBJECT_FIELDS:
        SALLA_RAW_ATTRIBUTION_PROJECTION[
            f"{_RAW_PREFIX}.{_object_key}.{_field}"
        ] = 1


__all__ = [
    "SALLA_RAW_ATTRIBUTION_PROJECTION",
    "attribution_containers",
    "campaign_id_candidates",
    "campaign_name_candidates",
    "canonical_ad_platform",
    "field_values",
    "meaningful_source_label",
    "order_source_candidates",
    "promoted_salla_attribution",
]
