"""Remove duplicate product images before storing/displaying Product V2 details."""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit


def _canonical_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    except Exception:
        return text.split("?", 1)[0].split("#", 1)[0]


def dedupe_images(rows: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        image_id = str(row.get("id") or "").strip()
        url = _canonical_url(row.get("url") or row.get("image") or row.get("src"))
        if image_id and image_id in seen_ids:
            continue
        if url and url in seen_urls:
            continue
        if image_id:
            seen_ids.add(image_id)
        if url:
            seen_urls.add(url)
        result.append(row)
    return result


def install_product_image_dedupe_support() -> None:
    import product_v2_details_routes as module

    original: Callable[..., dict[str, Any]] = module._details_patch
    if getattr(original, "_mezan_image_dedupe_support", False):
        return

    def wrapped(raw: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        patch = original(raw, *args, **kwargs)
        patch["images"] = dedupe_images(patch.get("images") or [])
        return patch

    wrapped._mezan_image_dedupe_support = True  # type: ignore[attr-defined]
    module._details_patch = wrapped
