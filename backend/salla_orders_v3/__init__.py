"""Canonical Salla Orders V3 shadow engine.

The package is deliberately isolated from operational order consumers until
the parity gates in :mod:`salla_orders_v3.parity` pass on production samples.
"""

from .compatibility import build_compatibility_order
from .gateway import SallaOrdersGateway
from .normalizer import normalize_order_item, normalize_order_items
from .shadow import SallaOrdersShadowEngine

__all__ = [
    "SallaOrdersGateway",
    "SallaOrdersShadowEngine",
    "build_compatibility_order",
    "normalize_order_item",
    "normalize_order_items",
]
