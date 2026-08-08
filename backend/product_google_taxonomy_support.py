"""Deterministic Google Product Category verification for Mezan -> Salla writes.

The Salla product response exposes Google taxonomy under ``google_taxonomy`` in
our connected store, while the currently documented Update Product contract does
not advertise a Google taxonomy request field.  Until a writer is proven against
Salla, this module treats every attempted taxonomy write as unverified until a
fresh Product Details read returns the requested value.

This module intentionally contains no AI.  AI may propose a taxonomy value, but
the provider write/verify boundary remains deterministic.
"""
from __future__ import annotations

from typing import Any


GOOGLE_TAXONOMY_RESPONSE_FIELDS = (
    "google_taxonomy",
    "google_product_category",
    "google_category",
)
GOOGLE_TAXONOMY_OBJECT_FIELDS = (
    "id",
    "category_id",
    "google_category_id",
    "google_product_category",
    "path",
    "full_path",
    "full_name",
    "name",
    "value",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def taxonomy_candidates(value: Any) -> set[str]:
    """Return comparable non-empty representations of a taxonomy value.

    Salla has returned ``google_taxonomy`` as a nullable provider field.  This
    helper also tolerates future object-shaped responses without pretending an
    ID and a path are interchangeable unless Salla returns both in the object.
    """
    if isinstance(value, dict):
        result: set[str] = set()
        for key in GOOGLE_TAXONOMY_OBJECT_FIELDS:
            text = _text(value.get(key))
            if text:
                result.add(text.casefold())
        return result
    if isinstance(value, (list, tuple, set)):
        result: set[str] = set()
        for item in value:
            result.update(taxonomy_candidates(item))
        return result
    text = _text(value)
    return {text.casefold()} if text else set()


def _product_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def extract_google_taxonomy(payload: Any) -> Any:
    """Extract the raw taxonomy value from a Salla product response."""
    product = _product_data(payload)
    for field in GOOGLE_TAXONOMY_RESPONSE_FIELDS:
        if field in product and product.get(field) not in (None, "", [], {}):
            return product.get(field)
    return None


def google_taxonomy_matches(expected: Any, salla_product_payload: Any) -> bool:
    """Fail closed unless a fresh Salla read contains the requested value."""
    expected_values = taxonomy_candidates(expected)
    actual_values = taxonomy_candidates(extract_google_taxonomy(salla_product_payload))
    return bool(expected_values and actual_values and expected_values.intersection(actual_values))


def taxonomy_sync_state(
    *,
    expected: Any,
    salla_product_payload: Any,
    attempted_write: bool,
) -> dict[str, Any]:
    actual = extract_google_taxonomy(salla_product_payload)
    synced = google_taxonomy_matches(expected, salla_product_payload)
    return {
        "salla_sync_status": "synced" if synced else "failed",
        "expected_google_taxonomy": expected,
        "actual_google_taxonomy": actual,
        "attempted_write": bool(attempted_write),
        "verified": synced,
    }
