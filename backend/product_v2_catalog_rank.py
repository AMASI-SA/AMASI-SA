"""Salla catalogue-order authority for Product V2.

Salla's own products listing is the user-visible source of truth for the
"newest first" order.  Some list payloads omit a reliable creation timestamp,
so Mezan stores the exact catalogue position returned by Salla during a full
sync.  A lower rank means the product appeared earlier in Salla's list.
"""
from __future__ import annotations


def catalog_rank(*, page: int, index: int, per_page: int) -> int:
    page = max(1, int(page))
    index = max(0, int(index))
    per_page = max(1, int(per_page))
    return (page - 1) * per_page + index


NEWEST_CATALOG_SORT = [
    ("salla_catalog_rank", 1),
    ("salla_product_id", -1),
]

OLDEST_CATALOG_SORT = [
    ("salla_catalog_rank", -1),
    ("salla_product_id", 1),
]
