"""Source-authority rules for Product V2.

* ``source_created_at`` is the original Salla product creation time.
* Product media is unique and ordered exactly once.

These patches are installed at application bootstrap so the existing sync and
full-details services keep their public API while using corrected semantics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, dict):
        for key in ("date", "datetime", "created_at", "value"):
            parsed = _parse_datetime(value.get(key))
            if parsed is not None:
                return parsed
        return None
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def salla_created_at(raw: dict[str, Any]) -> datetime | None:
    date_node = raw.get("date") if isinstance(raw.get("date"), dict) else {}
    for candidate in (
        raw.get("created_at"),
        raw.get("date_created"),
        raw.get("created"),
        date_node.get("created_at"),
        date_node.get("date"),
    ):
        parsed = _parse_datetime(candidate)
        if parsed is not None:
            return parsed
    return None


def _canonical_url(url: Any) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        # Salla/CDN image transformation query strings can differ while the
        # underlying image is identical. Identity is path-based.
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    except Exception:
        return text.split("?", 1)[0].rstrip("/")


def unique_images(rows: Any, *, main_url: str | None = None) -> list[dict[str, Any]]:
    source = rows if isinstance(rows, list) else []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    result: list[dict[str, Any]] = []

    def add(row: dict[str, Any]) -> None:
        image_id = str(row.get("id") or "").strip()
        canonical = _canonical_url(row.get("url"))
        if not canonical:
            return
        if image_id and image_id in seen_ids:
            return
        if canonical in seen_urls:
            return
        if image_id:
            seen_ids.add(image_id)
        seen_urls.add(canonical)
        result.append(dict(row))

    main_canonical = _canonical_url(main_url)
    if main_canonical:
        for row in source:
            if isinstance(row, dict) and _canonical_url(row.get("url")) == main_canonical:
                promoted = dict(row)
                promoted["is_main"] = True
                add(promoted)
                break

    for row in sorted(
        (row for row in source if isinstance(row, dict)),
        key=lambda item: (0 if item.get("is_main") else 1, item.get("sort", 10**9)),
    ):
        add(row)

    for index, row in enumerate(result):
        row["sort"] = index
        row["is_main"] = index == 0
    return result


def install_product_source_authority() -> None:
    import product_v2_sync_hotfix as sync_module
    import product_v2_details_routes as details_module

    original_normalize: Callable[..., dict[str, Any]] = sync_module.normalize_salla_product
    if not getattr(original_normalize, "_mezan_source_authority", False):
        def normalize_with_created(raw: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            doc = original_normalize(raw, *args, **kwargs)
            created = salla_created_at(raw)
            if created is not None:
                doc["source_created_at"] = created
            return doc
        normalize_with_created._mezan_source_authority = True  # type: ignore[attr-defined]
        sync_module.normalize_salla_product = normalize_with_created

    original_details: Callable[..., dict[str, Any]] = details_module._details_patch
    if not getattr(original_details, "_mezan_source_authority", False):
        def details_with_unique_media(raw: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            patch = original_details(raw, *args, **kwargs)
            created = salla_created_at(raw)
            if created is not None:
                patch["source_created_at"] = created
            patch["images"] = unique_images(
                patch.get("images"),
                main_url=patch.get("main_image"),
            )
            return patch
        details_with_unique_media._mezan_source_authority = True  # type: ignore[attr-defined]
        details_module._details_patch = details_with_unique_media
