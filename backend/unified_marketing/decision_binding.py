"""Stable, owner-bound evidence shared by candidate creation and execution.

Observation times prove freshness independently. They are not revisions to the
economic facts a recommendation used. Every other row/management field remains
bound, including parent campaign evidence used to reason about child entities.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


def _semantic_row(row: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(row)
    if isinstance(value.get("account"), dict):
        value["account"].pop("last_sync_at", None)
    return value


def source_evidence_fingerprint(
    user_id: str, row: dict[str, Any], management: dict[str, Any],
    *, parent_campaign: dict[str, Any] | None = None,
) -> str:
    entity = row.get("entity") or {}
    parent = None
    if entity.get("level") in {"ad_group", "ad"}:
        parent = parent_campaign or {}
        if (not entity.get("campaign_id")
                or (parent.get("entity") or {}).get("id") != entity.get("campaign_id")
                or (parent.get("entity") or {}).get("level") != "campaign"
                or parent.get("provider") != row.get("provider")
                or (parent.get("account") or {}).get("id") != (row.get("account") or {}).get("id")
                or parent.get("period") != row.get("period")):
            raise ValueError("unified_parent_campaign_evidence_missing_or_mismatched")
        parent = _semantic_row(parent)
    state = deepcopy(management)
    state.pop("updated_at", None)
    payload = {"version": "unified-decision-binding-v1", "user_id": str(user_id),
               "row": _semantic_row(row), "management": state, "parent_campaign": parent}
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()
