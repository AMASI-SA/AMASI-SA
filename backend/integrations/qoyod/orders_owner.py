"""Resolve the Orders/Salla data owner for Qoyod operations.

Qoyod still uses the legacy singleton tenant ``main`` for accounting
markers, while Orders V2 and the Salla OAuth connection are owned by the
merchant account. Employees carry that merchant id in ``created_by``.
"""
from __future__ import annotations

from typing import Any


def orders_owner_id(user: dict[str, Any] | None) -> str:
    """Return the merchant owner id, preserving the actor id for owners."""
    value = user or {}
    role = str(value.get("role") or "").strip().lower()
    if role != "owner":
        created_by = str(value.get("created_by") or "").strip()
        if created_by:
            return created_by
    return str(value.get("id") or "").strip()

