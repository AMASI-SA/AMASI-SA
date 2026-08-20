"""Expand selected physical preparation units into individual PDF cards.

The reviewed stage remains aggregated by product so the merchant can select a
large piece quantity quickly. At PDF build time, however, every allocated
physical unit becomes its own card.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable


_INSTALLED = False
_ORIGINAL_BUILD_LINES: Callable | None = None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def split_quantity_card_note(source_quantity: Any, unit_index: Any) -> str:
    """Return the operational note shown only on cards split from one order line."""
    total = _positive_int(source_quantity) or 1
    unit = _positive_int(unit_index) or 1
    if total <= 1:
        return ""
    return f"هذا المنتج مفصول من كمية {total} — القطعة {unit} من {total}"


def _append_split_note(existing_note: Any, source_quantity: Any, unit_index: Any) -> str | None:
    split_note = split_quantity_card_note(source_quantity, unit_index)
    existing = " ".join(str(existing_note or "").split())
    if not split_note:
        return existing or None
    if split_note in existing:
        return existing
    return " | ".join(part for part in (existing, split_note) if part) or None


def expand_preparation_unit_cards(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one immutable card snapshot per selected physical unit."""
    expanded: list[dict[str, Any]] = []

    for source in rows or []:
        if not isinstance(source, dict):
            continue

        selected_quantity = _positive_int(source.get("quantity")) or 1
        source_line_quantity = (
            _positive_int(source.get("source_line_quantity"))
            or selected_quantity
        )
        unit_indices = [
            unit
            for unit in (
                _positive_int(value)
                for value in (source.get("unit_indices") or [])
            )
            if unit is not None
        ]
        if not unit_indices:
            unit_indices = list(range(1, selected_quantity + 1))

        for unit_index in unit_indices:
            card = dict(source)
            card.update({
                "line_number": len(expanded) + 1,
                "quantity": 1,
                "source_line_quantity": source_line_quantity,
                "unit_index": unit_index,
                "unit_indices": [unit_index],
                "note": _append_split_note(
                    source.get("note"),
                    source_line_quantity,
                    unit_index,
                ),
            })
            expanded.append(card)

    return expanded


def install_preparation_pdf_unit_card_expansion() -> None:
    """Wrap the active reviewed-batch snapshot builder after layout install."""
    global _INSTALLED, _ORIGINAL_BUILD_LINES
    if _INSTALLED:
        return

    import reviewed_preparation_batches as batch

    _ORIGINAL_BUILD_LINES = batch._build_batch_lines

    @wraps(_ORIGINAL_BUILD_LINES)
    async def build_one_card_per_unit(
        context: dict[str, Any],
        planned: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        assert _ORIGINAL_BUILD_LINES is not None
        rows = await _ORIGINAL_BUILD_LINES(context, planned)
        return expand_preparation_unit_cards(rows)

    batch._build_batch_lines = build_one_card_per_unit
    _INSTALLED = True


__all__ = [
    "expand_preparation_unit_cards",
    "install_preparation_pdf_unit_card_expansion",
    "split_quantity_card_note",
]
