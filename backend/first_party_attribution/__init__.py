"""Mezan first-party commerce attribution.

The package deliberately keeps browser observations separate from Salla's
authoritative order records.  A verified match is projected onto
``unified_orders`` only by :func:`link_order_attribution`.
"""

from .core import (
    build_tracking_url,
    ensure_first_party_attribution_indexes,
    link_order_attribution,
    persist_storefront_event,
)
from .routes import make_first_party_attribution_router

__all__ = [
    "build_tracking_url",
    "ensure_first_party_attribution_indexes",
    "link_order_attribution",
    "make_first_party_attribution_router",
    "persist_storefront_event",
]
