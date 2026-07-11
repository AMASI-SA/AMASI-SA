"""Dynamic safety cap for Plan-B item-line LRM.

The exact LRM engine remains the single implementation. This installer only
widens its residual budget from the legacy fixed 0.10 SAR to a per-order cap:
0.01 SAR for each positive-value product line, capped at 0.50 SAR.

The builder is synchronous, so a process-local re-entrant lock safely scopes
the temporary engine limit for concurrent diagnostic/manual-send calls.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from threading import RLock
from typing import Any

from integrations.qoyod_manual import rounding_lrm_exact as _exact

_TWO_PLACES = Decimal("0.01")
_HARD_MAX_RESIDUAL = Decimal("0.50")
_LIMIT_LOCK = RLock()


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _dynamic_limit(items: list[dict]) -> Decimal:
    positive_lines = sum(
        1
        for item in (items or [])
        if _d(item.get("total")).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        ) > 0
    )
    return min(
        _HARD_MAX_RESIDUAL,
        Decimal(positive_lines) * _TWO_PLACES,
    )


def install(send_module) -> None:
    """Install exact LRM, then scope its residual cap per manual order."""
    _exact.install(send_module)

    if getattr(send_module, "_ITEM_LINE_LRM_DYNAMIC_CAP_INSTALLED", False):
        return

    exact_builder = send_module._build_invoice_payload
    exact_distributor = send_module._distribute_residual_over_items

    def dynamic_builder(*, canon: dict, contact_id: int,
                        line_resolutions: dict, settings: dict,
                        send_date_iso: str):
        items = canon.get("items") or []
        limit = _dynamic_limit(items)
        with _LIMIT_LOCK:
            previous = _exact._MAX_RESIDUAL
            _exact._MAX_RESIDUAL = limit
            try:
                return exact_builder(
                    canon=canon,
                    contact_id=contact_id,
                    line_resolutions=line_resolutions,
                    settings=settings,
                    send_date_iso=send_date_iso,
                )
            except send_module.ManualSendRefused as exc:
                distribution = (exc.extra or {}).get(
                    "rounding_distribution") or {}
                if distribution.get("reason") == "residual_exceeds_0_10":
                    distribution["reason"] = (
                        "residual_exceeds_dynamic_item_cap")
                    distribution["dynamic_cap"] = float(limit)
                    distribution["positive_item_lines"] = sum(
                        1 for item in items
                        if _d(item.get("total")).quantize(
                            _TWO_PLACES, rounding=ROUND_HALF_UP) > 0
                    )
                raise
            finally:
                _exact._MAX_RESIDUAL = previous

    def dynamic_distributor(items, line_resolutions, tax_factor,
                            tax_percent, residual_to_absorb):
        limit = _dynamic_limit(items)
        with _LIMIT_LOCK:
            previous = _exact._MAX_RESIDUAL
            _exact._MAX_RESIDUAL = limit
            try:
                return exact_distributor(
                    items,
                    line_resolutions,
                    tax_factor,
                    tax_percent,
                    residual_to_absorb,
                )
            finally:
                _exact._MAX_RESIDUAL = previous

    send_module._build_invoice_payload = dynamic_builder
    send_module._distribute_residual_over_items = dynamic_distributor
    send_module._ITEM_LINE_LRM_DYNAMIC_CAP_INSTALLED = True
