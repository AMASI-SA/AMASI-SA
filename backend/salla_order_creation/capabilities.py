"""Read-only Salla OAuth scope checklist for assisted WhatsApp orders.

Only the merchant's OWN stored Mezan OAuth scope claims are considered.
A separately connected merchant Salla tool/app cannot confer any Mezan scope.
This function performs no Salla/API/token access and NEVER certifies live
permissions. Real endpoint GET authorization remains a separate owner step.
"""
from __future__ import annotations

import re
from typing import Any

_SCOPE = re.compile(r"[a-z][a-z0-9_.]{1,70}\Z", re.ASCII)

REQUIRED = {
    "orders_create_scope": ("orders.read_write",),
    "catalog_read_scope": ("products.read", "products.read_write"),
    "shipping_read_scope": ("shipping.read", "shipping.read_write"),
    "payment_bank_read_scope": ("payments.read",),
}


def scope_claims(value: Any) -> frozenset[str]:
    """Parse only validated Salla OAuth claims; malformed values fail closed."""
    if isinstance(value, str):
        parts = value.strip().split()
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return frozenset()
    if (not 0 < len(parts) <= 40
            or any(type(p) is not str or _SCOPE.fullmatch(p) is None
                   for p in parts)):
        return frozenset()
    return frozenset(parts)


def assisted_salla_scope_report(
    integration: Any, *, creation_enabled: bool = False,
    bank_read_enabled: bool = False,
) -> dict:
    """Return an evidence-limited, secret-free owner-only capability summary."""
    if not isinstance(integration, dict):
        integration = {}
    connected = integration.get("status") == "connected"
    claims = scope_claims(integration.get("scope")) if connected else frozenset()
    found = {
        name: any(scope in claims for scope in allowed)
        for name, allowed in REQUIRED.items()
    }
    return {
        "connected": bool(connected),
        **found,
        "missing_required_scope_groups": sorted(
            name for name, okay in found.items() if not okay
        ),
        "creation_feature_enabled": creation_enabled is True,
        "bank_review_read_feature_enabled": bank_read_enabled is True,
        "stored_scope_claims_only": True,
        "live_provider_permission_verified": False,
        "active_bank_beneficiary_iban_verified": False,
        "proof_writers_integrated": False,
        "note": "Stored Mezan OAuth scope claims only; live provider authorization and receiving bank data still require a protected readback.",
    }
