"""Store-scope boundaries for the Salla attribution pilot.

The pilot storefront may emit browser attribution events, but it must never own
the production Salla integration or create/enrich production orders.
"""
from __future__ import annotations

import os
from typing import Any


ATTRIBUTION_PILOT_STORE_ENV = "SALLA_ATTRIBUTION_PILOT_STORE_ID"


def normalize_store_id(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def attribution_pilot_store_id() -> str:
    # There is deliberately no code default here. 748155538 is the live
    # Amasi store, so treating it as a pilot would drop legitimate orders.
    # A pilot may only be enabled by an explicit deployment setting.
    return normalize_store_id(os.environ.get(ATTRIBUTION_PILOT_STORE_ENV))


def is_attribution_pilot_store(value: Any) -> bool:
    normalized = normalize_store_id(value)
    return bool(normalized and normalized == attribution_pilot_store_id())


__all__ = [
    "ATTRIBUTION_PILOT_STORE_ENV",
    "attribution_pilot_store_id",
    "is_attribution_pilot_store",
    "normalize_store_id",
]
