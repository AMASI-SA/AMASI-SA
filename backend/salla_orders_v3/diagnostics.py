"""Read-only diagnostics and cutover gates for Salla Orders V3."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

from .parity import (
    compare_attribution_parity,
    compare_fulfillment_parity,
    compare_qoyod_parity,
)


REQUIRED_SCOPE = "orders.read"
ACCEPTED_ORDER_READ_SCOPES = frozenset({"orders.read", "orders.read_write"})


def scope_diagnostic(integration: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Report effective stored scopes without returning either OAuth token."""
    scope_value = (integration or {}).get("scope")
    if isinstance(scope_value, list):
        scopes = {str(value).strip() for value in scope_value if str(value).strip()}
    else:
        scopes = {
            value
            for value in str(scope_value or "").replace(",", " ").split()
            if value
        }
    effective_read_scopes = sorted(scopes & ACCEPTED_ORDER_READ_SCOPES)
    return {
        "connected": bool(integration and integration.get("status") == "connected"),
        "stored_scopes": sorted(scopes),
        "required_scope": REQUIRED_SCOPE,
        "accepted_order_read_scopes": sorted(ACCEPTED_ORDER_READ_SCOPES),
        "effective_order_read_scopes": effective_read_scopes,
        "required_scope_present": bool(effective_read_scopes),
        "token_fields_returned": False,
    }


def build_parity_report(
    *,
    legacy_order: dict[str, Any],
    v3_order: dict[str, Any],
    legacy_qoyod_dry_run: dict[str, Any],
    v3_qoyod_dry_run: dict[str, Any],
    legacy_attribution_rows: list[dict[str, Any]],
    v3_attribution_rows: list[dict[str, Any]],
    regression_results: Optional[dict[str, bool]] = None,
) -> dict[str, Any]:
    fulfillment = compare_fulfillment_parity(legacy_order, v3_order)
    qoyod = compare_qoyod_parity(legacy_qoyod_dry_run, v3_qoyod_dry_run)
    attribution = compare_attribution_parity(
        legacy_attribution_rows,
        v3_attribution_rows,
    )
    regressions = deepcopy(regression_results or {})
    regressions_passed = bool(regressions) and all(regressions.values())
    cutover_allowed = bool(
        fulfillment["passed"]
        and qoyod["passed"]
        and attribution["passed"]
        and regressions_passed
    )
    return {
        "fulfillment_parity": fulfillment,
        "qoyod_parity": qoyod,
        "attribution_parity": attribution,
        "regressions": regressions,
        "regressions_passed": regressions_passed,
        "cutover_allowed": cutover_allowed,
        "provider_write_reached": False,
    }


async def read_fulfillment_shadow_comparison(
    db: Any,
    *,
    user_id: str,
    store_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Compare one current order and one isolated V3 snapshot without writes."""
    legacy = await db.unified_orders.find_one(
        {"user_id": str(user_id), "order_number": str(order_number)},
        {"_id": 0, "raw_by_source": 0, "raw_by_user": 0},
    )
    shadow = await db.salla_orders_v3_shadow.find_one(
        {
            "user_id": str(user_id),
            "store_id": str(store_id),
            "order_number": str(order_number),
        },
        {"_id": 0, "compatibility_order": 1},
    )
    if not legacy or not shadow:
        return {
            "available": False,
            "legacy_found": bool(legacy),
            "shadow_found": bool(shadow),
            "provider_write_reached": False,
        }
    return {
        "available": True,
        **compare_fulfillment_parity(
            legacy,
            shadow.get("compatibility_order") or {},
        ),
        "provider_write_reached": False,
    }
