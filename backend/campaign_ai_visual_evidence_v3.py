"""Bounded multimodal visual evidence for Decision Intelligence V3.

Actual provider creative visuals are preferred, then Salla/Product V2 hero and
gallery evidence fills the remaining bounded image budget. Provider-media URLs
are validated before entering the evidence pack; this module only performs a
final HTTPS shape check before sending them as Responses API input_image items.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


MAX_VISUALS_PER_MODEL_CALL = 8
MAX_AD_CREATIVE_VISUALS_PER_CALL = 5


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
    return raw[:4000]


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


def ad_creative_visuals(evidence_pack: dict[str, Any]) -> list[dict[str, Any]]:
    entities = ((evidence_pack.get("actual_creative_media") or {}).get("entities") or {})
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entity_key, block in entities.items():
        if not isinstance(block, dict):
            continue
        provider = str(block.get("provider") or "unknown")
        for visual in (block.get("visuals") or []):
            if not isinstance(visual, dict):
                continue
            url = _image_url(visual.get("image_url"))
            if not url or url in seen:
                continue
            seen.add(url)
            output.append({
                "kind": "actual_ad_creative",
                "entity_key": entity_key,
                "provider": provider,
                "ad_id": str(visual.get("ad_id") or ""),
                "creative_id": str(visual.get("creative_id") or ""),
                "media_id": str(visual.get("media_id") or visual.get("video_id") or ""),
                "role": str(visual.get("visual_role") or "actual_ad_media"),
                "source": str(visual.get("source") or "provider_actual_media"),
                "image_url": url,
            })
            if len(output) >= MAX_AD_CREATIVE_VISUALS_PER_CALL:
                return output
    return output


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
                    "kind": "product_page_visual",
                    "entity_key": entity_key,
                    "product_id": product_id,
                    "product_name": product_name,
                    "role": role,
                    "image_url": url,
                })
                if len(output) >= MAX_VISUALS_PER_MODEL_CALL:
                    return output
    return output


def combined_visuals(evidence_pack: dict[str, Any]) -> list[dict[str, Any]]:
    actual = ad_creative_visuals(evidence_pack)
    seen = {row["image_url"] for row in actual}
    output = list(actual)
    for row in product_visuals(evidence_pack):
        if row["image_url"] in seen:
            continue
        seen.add(row["image_url"])
        output.append(row)
        if len(output) >= MAX_VISUALS_PER_MODEL_CALL:
            break
    return output[:MAX_VISUALS_PER_MODEL_CALL]


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
    visuals = combined_visuals(evidence_pack) if include_images else []
    for visual in visuals:
        if visual.get("kind") == "actual_ad_creative":
            label = (
                "ACTUAL provider ad creative visual. "
                f"provider={visual['provider']}, role={visual['role']}, "
                f"ad_id={visual['ad_id'] or 'unknown'}, creative_id={visual['creative_id'] or 'unknown'}, "
                f"entity={visual['entity_key']}, source={visual['source']}. "
                "Analyze only what is actually visible in this image/frame. Do not claim unseen video moments."
            )
        else:
            label = (
                "Product-page visual evidence for product "
                f"{visual['product_id']} ({visual['product_name']}), role={visual['role']}, "
                f"entity={visual['entity_key']}. Analyze only what is actually visible."
            )
        content.append({"type": "input_text", "text": label})
        content.append({
            "type": "input_image",
            "image_url": visual["image_url"],
            "detail": "low",
        })
    return [{"role": "user", "content": content}], len(visuals)


__all__ = [
    "MAX_AD_CREATIVE_VISUALS_PER_CALL",
    "MAX_VISUALS_PER_MODEL_CALL",
    "ad_creative_visuals",
    "combined_visuals",
    "product_visuals",
    "responses_input",
]
