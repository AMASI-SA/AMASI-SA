"""Bounded multimodal product-page visual evidence for Decision Intelligence V3.

Image URLs come only from the Salla-backed Product V2 evidence already attached
to the campaign-product graph.  They are passed to the OpenAI Responses API as
input_image items; this module does not fetch arbitrary image URLs itself.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


MAX_VISUALS_PER_MODEL_CALL = 6


def _https_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return raw[:3000]


def _image_url(value: Any) -> str | None:
    if isinstance(value, str):
        return _https_url(value)
    if not isinstance(value, dict):
        return None
    for key in ("url", "image_url", "original", "large", "medium", "small"):
        url = _https_url(value.get(key))
        if url:
            return url
    return None


def product_visuals(evidence_pack: dict[str, Any]) -> list[dict[str, Any]]:
    entities = ((evidence_pack.get("product_intelligence") or {}).get("entities") or {})
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entity_key, block in entities.items():
        for product in (block.get("products") or []):
            product_id = str(product.get("product_id") or "unknown")
            product_name = str(product.get("product_name") or product_id)
            candidates: list[tuple[str, Any]] = [
                ("hero", product.get("main_image")),
                ("page_og_image", ((product.get("page_probe") or {}).get("og_image"))),
            ]
            candidates.extend(
                (f"gallery_{index + 1}", image)
                for index, image in enumerate(product.get("images") or [])
            )
            for role, raw in candidates:
                url = _image_url(raw)
                if not url or url in seen:
                    continue
                seen.add(url)
                output.append({
                    "entity_key": entity_key,
                    "product_id": product_id,
                    "product_name": product_name,
                    "role": role,
                    "image_url": url,
                })
                if len(output) >= MAX_VISUALS_PER_MODEL_CALL:
                    return output
    return output


def responses_input(
    payload: dict[str, Any],
    evidence_pack: dict[str, Any],
    *,
    include_images: bool,
) -> tuple[list[dict[str, Any]], int]:
    import json

    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": json.dumps(payload, ensure_ascii=False, default=str),
    }]
    visuals = product_visuals(evidence_pack) if include_images else []
    for visual in visuals:
        content.append({
            "type": "input_text",
            "text": (
                "Visual evidence for product "
                f"{visual['product_id']} ({visual['product_name']}), role={visual['role']}, "
                f"entity={visual['entity_key']}. Analyze only what is actually visible."
            ),
        })
        content.append({
            "type": "input_image",
            "image_url": visual["image_url"],
            "detail": "low",
        })
    return [{"role": "user", "content": content}], len(visuals)


__all__ = ["MAX_VISUALS_PER_MODEL_CALL", "product_visuals", "responses_input"]
