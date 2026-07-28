"""Prevent duplicate main images in Product V2 details.

Salla can return a product's primary image both as `main_image` and as the first
entry in `images`, sometimes with a different CDN URL. If the gallery already
contains a row marked as main, Mezan must trust that gallery row and must not
insert the separate `main_image` value again.
"""
from __future__ import annotations

from typing import Any


def install_product_main_image_dedupe_support() -> None:
    import product_v2_details_routes as module

    original = module._dedupe_images
    if getattr(original, "_mezan_main_image_dedupe_support", False):
        return

    def wrapped(rows: list[dict[str, Any]], main_image: Any = None, product_name: Any = None) -> list[dict[str, Any]]:
        has_gallery_main = any(bool(row.get("is_main")) for row in rows if isinstance(row, dict))
        effective_main_image = None if has_gallery_main else main_image
        return original(rows, effective_main_image, product_name)

    wrapped._mezan_main_image_dedupe_support = True  # type: ignore[attr-defined]
    module._dedupe_images = wrapped
