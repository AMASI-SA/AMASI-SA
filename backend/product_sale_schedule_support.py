"""Install Salla sale price/date support into Product V2 and Control Center."""
from __future__ import annotations

from typing import Any, Callable

from product_sale_schedule import normalize_product_prices


def install_product_sale_schedule_support() -> None:
    import product_v2_routes as product_module
    import product_v2_details_routes as details_module
    import product_control_center_routes as control_module

    original_normalize: Callable[..., dict[str, Any]] = product_module.normalize_salla_product
    if not getattr(original_normalize, "_mezan_sale_schedule_support", False):
        def normalize_with_sale(raw: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            doc = original_normalize(raw, *args, **kwargs)
            pricing = normalize_product_prices(raw)
            doc.update({
                "price": pricing.get("price"),
                "sale_price": pricing.get("sale_price"),
                "sale_starts_at": pricing.get("sale_starts_at"),
                "sale_ends_at": pricing.get("sale_ends_at"),
            })
            if pricing.get("currency"):
                doc["currency"] = pricing["currency"]
            return doc
        normalize_with_sale._mezan_sale_schedule_support = True  # type: ignore[attr-defined]
        product_module.normalize_salla_product = normalize_with_sale

    original_details: Callable[..., dict[str, Any]] = details_module._details_patch
    if not getattr(original_details, "_mezan_sale_schedule_support", False):
        def details_with_sale(raw: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            patch = original_details(raw, *args, **kwargs)
            pricing = normalize_product_prices(raw)
            patch.update({
                "price": pricing.get("price"),
                "sale_price": pricing.get("sale_price"),
                "sale_starts_at": pricing.get("sale_starts_at"),
                "sale_ends_at": pricing.get("sale_ends_at"),
            })
            return patch
        details_with_sale._mezan_sale_schedule_support = True  # type: ignore[attr-defined]
        details_module._details_patch = details_with_sale

    control_module.PUBLISHABLE_FIELDS.update({"sale_starts_at", "sale_ends_at"})
    original_payload = control_module._salla_payload
    if not getattr(original_payload, "_mezan_sale_schedule_support", False):
        def payload_with_sale(patch: dict[str, Any]) -> dict[str, Any]:
            payload = original_payload(patch)
            if "sale_starts_at" in patch:
                payload["sale_start"] = patch.get("sale_starts_at") or None
            if "sale_ends_at" in patch:
                payload["sale_end"] = patch.get("sale_ends_at") or None
            return payload
        payload_with_sale._mezan_sale_schedule_support = True  # type: ignore[attr-defined]
        control_module._salla_payload = payload_with_sale
