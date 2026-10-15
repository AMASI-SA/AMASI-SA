"""Store-scope boundaries for the Salla attribution pilot.

The pilot storefront may emit browser attribution events, but it must never own
the production Salla integration or create/enrich production orders.
"""
from __future__ import annotations

import os
from typing import Any


DEFAULT_ATTRIBUTION_PILOT_STORE_ID = "748155538"
ATTRIBUTION_PILOT_STORE_ENV = "SALLA_ATTRIBUTION_PILOT_STORE_ID"


def normalize_store_id(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def attribution_pilot_store_id() -> str:
    # Return the optional deployment pilot when present for compatibility with
    # existing callers. The verified demo ID remains independently immutable in
    # ``is_attribution_pilot_store`` below.
    return normalize_store_id(
        os.environ.get(ATTRIBUTION_PILOT_STORE_ENV)
        or DEFAULT_ATTRIBUTION_PILOT_STORE_ID
    )


def is_attribution_pilot_store(value: Any) -> bool:
    normalized = normalize_store_id(value)
    if not normalized:
        return False
    # 748155538 was verified in Salla as the dedicated demo storefront. It is
    # always isolated even if a deployment variable is missing or points to an
    # additional pilot store.
    return normalized in {
        DEFAULT_ATTRIBUTION_PILOT_STORE_ID,
        attribution_pilot_store_id(),
    }


__all__ = [
    "ATTRIBUTION_PILOT_STORE_ENV",
    "DEFAULT_ATTRIBUTION_PILOT_STORE_ID",
    "attribution_pilot_store_id",
    "is_attribution_pilot_store",
    "normalize_store_id",
]
