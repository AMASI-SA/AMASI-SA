"""Product/destination evidence for Campaign AI Decision Intelligence V3.

This module reads the existing campaign-product association graph and Product V2
catalog.  Public page probing is deliberately constrained to the canonical host
already stored for that Salla product; ad-controlled arbitrary hosts are never
used as an SSRF oracle.

The output is descriptive evidence.  Product health and inventory estimates are
never converted into a marketing decision here; OpenAI receives them as facts.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from integrations_control_center.campaign_product_associations import (
    list_effective_campaign_products,
)
from product_v2_routes import PRODUCTS as PRODUCT_V2_COLLECTION
from campaign_product_catalog import current_catalog_product
from unified_marketing.gateway import load_unified_marketing_entity_metadata


SALLA_PRODUCT_CACHE = "salla_products"
PRODUCT_WATCH_HISTORY = "mezan_advertising_product_watch_history_v1"
MAX_PRODUCTS_PER_ENTITY = 8
MAX_PAGE_BYTES = 1_000_000


def _text(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _entity_key(row: dict[str, Any]) -> str:
    return "|".join((
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    ))


def _hierarchy(row: dict[str, Any]) -> dict[str, str | None]:
    level = str(row.get("entity_level") or "")
    campaign_id = (
        str(row.get("entity_id")) if level == "campaign"
        else _text(row.get("campaign_id"), 160) or None
    )
    ad_squad_id = (
        str(row.get("entity_id")) if level == "ad_group"
        else _text(row.get("ad_group_id"), 160) or None
        if level == "ad"
        else None
    )
    ad_id = str(row.get("entity_id")) if level == "ad" else None
    return {
        "campaign_id": campaign_id,
        "ad_squad_id": ad_squad_id,
        "ad_id": ad_id,
    }


def _status(value: Any) -> str:
    text = _text(value, 80).casefold()
    if text in {"active", "sale", "available", "published", "enabled"}:
        return "active"
    if text in {"out", "out_of_stock", "sold_out"}:
        return "out_of_stock"
    if text in {"hidden", "draft", "inactive", "disabled", "archived"}:
        return "hidden_or_inactive"
    return text or "unknown"


def _variant_rows(product: dict[str, Any]) -> list[dict[str, Any]]:
    rows = product.get("variants") if isinstance(product.get("variants"), list) else []
    output = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        quantity = _number(row.get("quantity") if row.get("quantity") not in (None, "") else row.get("stock_quantity"))
        output.append({
            "id": _text(row.get("id"), 160) or None,
            "name": _text(row.get("name") or row.get("title"), 240) or None,
            "sku": _text(row.get("sku"), 160) or None,
            "quantity": quantity,
            "unlimited_quantity": bool(row.get("unlimited_quantity") or row.get("is_infinite")),
            "price": _number(row.get("price")),
            "sale_price": _number(row.get("sale_price") if row.get("sale_price") not in (None, "") else row.get("discount_price")),
            "image": _text(row.get("image") or row.get("image_url"), 1000) or None,
            "selections": row.get("selections") or row.get("options") or row.get("values") or [],
        })
    return output


def _option_price_context(product: dict[str, Any]) -> dict[str, Any]:
    """Catalog fields are not a live selection/checkout observation."""
    options = product.get("options") if isinstance(product.get("options"), list) else []
    values = [value for option in options if isinstance(option, dict)
              for value in (option.get("values") if isinstance(option.get("values"), list) else [])
              if isinstance(value, dict) and value.get("price") is not None]
    return {
        "source": "product_v2_catalog_snapshot",
        "source_fields_complete": bool(values) and all(
            value.get("price_source_field") in {"price", "additional_price"} for value in values
        ),
        "selected_final_price_verified": False,
        "independent_option_addition_verified": False,
        "limitations": [
            "option_price_field_is_not_verified_final_item_price",
            "legacy_option_price_may_have_lost_source_field",
            "catalog_prices_are_not_historical_paid_prices",
            "conditional_options_and_checkout_total_not_observed",
        ],
    }


def _campaign_velocity(row: dict[str, Any], product_id: str) -> dict[str, Any]:
    profitability = (
        row.get("campaign_profitability")
        if row.get("entity_level") == "campaign"
        else row.get("parent_campaign_profitability")
    )
    profitability = profitability if isinstance(profitability, dict) else {}
    products = profitability.get("products") if isinstance(profitability.get("products"), list) else []
    match = next((item for item in products if str(item.get("salla_product_id") or "") == product_id), None)
    if not isinstance(match, dict):
        return {
            "available": False,
            "average_daily_units": None,
            "scope": "campaign_only_not_whole_store",
        }
    units = _number(match.get("units"))
    observed_days = max(1, int(_number(row.get("observed_days")) or 3))
    return {
        "available": units is not None,
        "units_in_observed_window": units,
        "observed_days": observed_days,
        "average_daily_units": round(units / observed_days, 3) if units is not None else None,
        "scope": "advertising_linked_campaign_velocity_not_whole_store",
    }


def _variant_sales(row: dict[str, Any], product_id: str) -> dict[str, Any]:
    campaign = row.get("entity_level") == "campaign"
    source = row.get("campaign_profitability" if campaign else "parent_campaign_profitability") or {}
    products = source.get("products") or []
    match = next((p for p in products if str(p.get("salla_product_id") or "") == product_id), {})
    facts = match.get("variant_sales") or {}
    rows = facts.get("rows") or []
    return {
        **facts,
        "status": facts.get("status", "unavailable"),
        "rows": rows[:16],
        "rows_truncated": len(rows) > 16,
        "scope": "campaign" if campaign else "parent_campaign_not_ad_or_ad_group",
        "conversion_rate_available": False,
        "price_cause_verified": False,
        "variant_selection_abandonment": "not_available",
    }


def _inventory(product: dict[str, Any], velocity: dict[str, Any]) -> dict[str, Any]:
    quantity = _number(product.get("quantity"))
    unlimited = bool(product.get("unlimited_quantity"))
    variants = _variant_rows(product)
    finite_variant_quantities = [
        row["quantity"] for row in variants
        if row.get("quantity") is not None and not row.get("unlimited_quantity")
    ]
    available_quantity = quantity
    if available_quantity is None and finite_variant_quantities:
        available_quantity = sum(float(value) for value in finite_variant_quantities)
    daily = _number(velocity.get("average_daily_units"))
    days_remaining = (
        round(max(available_quantity, 0) / daily, 2)
        if available_quantity is not None and daily and daily > 0 and not unlimited
        else None
    )
    stockout_at = (
        (datetime.now(timezone.utc) + timedelta(days=days_remaining)).isoformat()
        if days_remaining is not None
        else None
    )
    if unlimited:
        status = "unlimited"
    elif available_quantity is not None and available_quantity <= 0:
        status = "out_of_stock"
    elif days_remaining is not None and days_remaining < 1:
        status = "less_than_one_day_estimated"
    elif days_remaining is not None and days_remaining < 3:
        status = "low_stock_estimated"
    elif available_quantity is not None:
        status = "in_stock"
    else:
        status = "unknown"
    return {
        "status": status,
        "quantity": quantity,
        "available_quantity_estimate": available_quantity,
        "reserved_quantity": None,
        "unlimited_quantity": unlimited,
        "variants": variants,
        "velocity": velocity,
        "estimated_days_to_stockout": days_remaining,
        "estimated_stockout_at": stockout_at,
        "estimate_limitations": (
            ["velocity_is_campaign_linked_not_whole_store"]
            if days_remaining is not None
            else ["insufficient_velocity_or_inventory_data"]
        ),
    }


def _is_public_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return bool(infos)


def _canonical_host(url: str | None) -> str | None:
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname.casefold().rstrip(".")


async def probe_public_product_page(
    url: str | None,
    *,
    canonical_url: str | None,
) -> dict[str, Any]:
    """Fetch only the trusted canonical product host and report public health."""
    requested = _text(url, 2000) or None
    canonical = _text(canonical_url, 2000) or None
    requested_host = _canonical_host(requested)
    canonical_host = _canonical_host(canonical)
    if not requested or not requested_host or not canonical_host:
        return {"checked": False, "status": "PRODUCT_URL_UNKNOWN", "reason": "canonical_product_url_missing"}
    if requested_host != canonical_host:
        return {
            "checked": False,
            "status": "PRODUCT_URL_WRONG_DESTINATION",
            "requested_url": requested,
            "canonical_url": canonical,
            "reason": "destination_host_does_not_match_trusted_product_host",
        }
    if not await asyncio.to_thread(_is_public_ip, requested_host):
        return {"checked": False, "status": "PRODUCT_URL_UNSAFE", "reason": "host_not_public"}

    try:
        async with asyncio.timeout(15):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(12.0),
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": "MezanProductHealth/1.0", "Accept": "text/html", "Accept-Encoding": "identity"},
            ) as client:
                async with client.stream("GET", requested) as response:
                    base = {"checked": True, "requested_url": requested,
                            "final_url": str(response.url), "http_status": response.status_code,
                            "redirected": False, "redirect_followed": False}
                    if 300 <= response.status_code < 400:
                        return {**base, "status": "PRODUCT_URL_REDIRECTED",
                                "reason": "redirect_not_followed"}
                    if response.status_code != 200:
                        return {**base, "status": "PRODUCT_URL_BROKEN" if response.status_code == 404 else "PRODUCT_PAGE_UNAVAILABLE"}
                    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if mime != "text/html":
                        return {**base, "status": "PRODUCT_PAGE_UNAVAILABLE", "reason": "non_html_response"}
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        return {**base, "status": "PRODUCT_PAGE_UNAVAILABLE", "reason": "encoded_response_not_supported"}
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdecimal() or int(length) > MAX_PAGE_BYTES):
                        return {**base, "status": "PRODUCT_PAGE_UNAVAILABLE", "reason": "html_response_too_large"}
                    body = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=1024):
                        if len(body) + len(chunk) > MAX_PAGE_BYTES:
                            return {**base, "status": "PRODUCT_PAGE_UNAVAILABLE", "reason": "html_response_too_large"}
                        body.extend(chunk)
                    text = body.decode(response.encoding or "utf-8", errors="replace")
        final_url = str(response.url)
        status = "PRODUCT_URL_OK"
        lowered = text.casefold()
        add_to_cart_present = any(marker in lowered for marker in (
            "add-to-cart", "add_to_cart", "addtocart", "أضف للسلة", "أضف إلى السلة", "اضف للسلة",
        ))
        unavailable_markers = any(marker in lowered for marker in (
            "out of stock", "sold out", "غير متوفر", "نفدت الكمية", "غير متاح",
        ))
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        return {
            "checked": True,
            "status": status,
            "requested_url": requested,
            "final_url": final_url,
            "http_status": response.status_code,
            "redirected": bool(response.history),
            "page_title": _text(title_match.group(1), 300) if title_match else None,
            "add_to_cart_marker_present": add_to_cart_present,
            "unavailable_marker_present": unavailable_markers,
            "body_truncated": False,
            "redirect_followed": False,
        }
    except Exception as exc:
        return {
            "checked": True,
            "status": "PRODUCT_PAGE_UNAVAILABLE",
            "requested_url": requested,
            "error_type": type(exc).__name__,
        }


async def _snapchat_destination(db: Any, user_id: str, row: dict[str, Any]) -> str | None:
    if row.get("provider") != "snapchat" or row.get("entity_level") != "ad":
        return None
    metadata = await load_unified_marketing_entity_metadata(
        db,
        user_id,
        provider="snapchat_ads",
        account_id=str(row.get("account_id") or ""),
        entity_level="ad",
        entity_id=str(row.get("entity_id") or ""),
    )
    return _text(metadata.get("destination_url"), 2000) or None


async def _products_for_candidate(db: Any, user_id: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    hierarchy = _hierarchy(row)
    if not hierarchy.get("campaign_id") or not row.get("account_id"):
        return []
    try:
        links = await list_effective_campaign_products(
            db,
            user_id,
            provider=str(row.get("provider") or ""),
            account_id=str(row.get("account_id") or ""),
            campaign_id=hierarchy["campaign_id"],
            ad_squad_id=hierarchy["ad_squad_id"],
            ad_id=hierarchy["ad_id"],
            include_unverified=False,
        )
    except Exception:
        return []
    return links[:MAX_PRODUCTS_PER_ENTITY]


async def build_product_intelligence(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
    *,
    probe_pages: bool = True,
) -> dict[str, Any]:
    """Build product/page/inventory evidence keyed by advertising entity."""
    result: dict[str, Any] = {}
    probe_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidates:
        links = await _products_for_candidate(db, user_id, row)
        advertised_destination = await _snapchat_destination(db, user_id, row)
        products: list[dict[str, Any]] = []
        for link in links:
            product_id = str(link.get("product_id") or "")
            product = await db[PRODUCT_V2_COLLECTION].find_one(
                {"user_id": user_id, "salla_product_id": product_id},
                {"_id": 0, "raw_salla": 0, "raw_salla_details": 0},
            ) or {}
            cache = await db[SALLA_PRODUCT_CACHE].find_one(
                {"user_id": user_id, "product_id": product_id},
                {"_id": 0},
            ) or {}
            current_catalog = await current_catalog_product(db, user_id, product_id)
            canonical_url = _text((current_catalog or {}).get("url"), 2000) or None
            destination = advertised_destination or canonical_url
            velocity = _campaign_velocity(row, product_id)
            inventory = _inventory(product, velocity)
            page_key = (destination or "", canonical_url or "")
            if probe_pages and page_key not in probe_cache:
                probe_cache[page_key] = await probe_public_product_page(
                    destination,
                    canonical_url=canonical_url,
                )
            page_probe = probe_cache.get(page_key) if probe_pages else {
                "checked": False,
                "status": "PRODUCT_URL_NOT_PROBED",
            }
            product_status = _status(product.get("status") or cache.get("status"))
            visibility = (
                "not_public_or_inactive"
                if product_status == "hidden_or_inactive" or product.get("archived") is True
                else "out_of_stock"
                if product_status == "out_of_stock"
                else "public_status_expected"
                if product_status == "active"
                else "unknown"
            )
            products.append({
                "product_id": product_id,
                "product_name": product.get("name") or link.get("product_name") or cache.get("name"),
                "association": {
                    "scope_type": link.get("scope_type"),
                    "confirmed": link.get("confirmed"),
                    "evidence": link.get("evidence"),
                },
                "destination_url": destination,
                "destination_source": "provider_ad_metadata" if advertised_destination else "catalog_fallback",
                "canonical_product_url": canonical_url,
                "page_probe": page_probe,
                "status": product_status,
                "visibility": visibility,
                "archived": bool(product.get("archived")),
                "sku": product.get("sku"),
                "price": product.get("price") if product.get("price") is not None else cache.get("price"),
                "sale_price": product.get("sale_price"),
                "currency": product.get("currency") or "SAR",
                "description": _text(product.get("description"), 1600) or None,
                "short_description": _text(product.get("short_description"), 800) or None,
                "main_image": product.get("main_image") or cache.get("main_image"),
                "images": (product.get("images") or cache.get("images") or [])[:12],
                "options": (product.get("options") or [])[:20],
                "option_price_context": _option_price_context(product),
                "variants": _variant_rows(product)[:50],
                "inventory": inventory,
                "variant_sales": _variant_sales(row, product_id),
                "details_loaded": bool(product.get("details_loaded")),
                "last_synced_at": product.get("last_synced_at"),
                "details_synced_at": product.get("details_synced_at"),
                "source_updated_at": product.get("source_updated_at"),
                "data_limitations": [
                    *([] if product else ["product_v2_record_missing"]),
                    *([] if canonical_url else ["canonical_product_url_missing"]),
                    "reserved_quantity_not_available_in_current_product_contract",
                ],
            })
        result[_entity_key(row)] = {
            "products": products,
            "advertised_destination_url": advertised_destination,
            "product_count": len(products),
            "source_contract": {
                "product_identity": "verified_campaign_product_association_when_available",
                "product_catalog": "mezan_products_v2_from_salla",
                "canonical_page_url": "mezan_products_v2_current_salla_snapshot",
                "page_probe": "public_get_same_canonical_host_only",
                "inventory_is_evidence_not_rule": True,
            },
        }
    return {
        "schema_version": "campaign_ai_product_intelligence_v3",
        "entities": result,
    }


async def ensure_product_watch_indexes(db: Any) -> None:
    await db[PRODUCT_WATCH_HISTORY].create_index(
        [("user_id", 1), ("product_id", 1), ("observed_at", -1)],
        name="advertising_product_watch_user_product_time",
    )


__all__ = [
    "PRODUCT_WATCH_HISTORY",
    "build_product_intelligence",
    "ensure_product_watch_indexes",
    "probe_public_product_page",
]
