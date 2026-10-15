"""Render one A4 card per physical order unit.

Reviewed preparation batches historically store one snapshot row per selected
source line and keep the selected physical units in ``unit_indices``.  The
approved Amasi product file requires one card/QR per physical unit, so this
final overlay expands those snapshots immediately before rendering without
changing allocation, quantities, or workflow state.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
from typing import Any, Callable


_INSTALLED = False
_ORIGINAL_RENDERER: Callable[[dict[str, Any]], bytes] | None = None


def expand_batch_lines_to_physical_pieces(batch_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic one-unit snapshots while preserving their identity."""
    expanded: list[dict[str, Any]] = []
    for source in batch_row.get("lines") or []:
        if not isinstance(source, dict):
            continue
        indices: list[int] = []
        for raw in source.get("unit_indices") or []:
            try:
                index = int(raw)
            except (TypeError, ValueError):
                continue
            if index > 0 and index not in indices:
                indices.append(index)
        if not indices and source.get("unit_index") not in (None, ""):
            try:
                index = int(source.get("unit_index"))
            except (TypeError, ValueError):
                index = 0
            if index > 0:
                indices.append(index)
        if not indices:
            # Backward compatibility for old stored batches that predate
            # materialised unit indices. Do not invent a new identity here.
            expanded.append(deepcopy(source))
            continue

        for index in sorted(indices):
            row = deepcopy(source)
            row["unit_index"] = index
            row["unit_indices"] = [index]
            row["quantity"] = 1
            expanded.append(row)
    return expanded


def install_preparation_pdf_physical_piece_overlay() -> None:
    """Install after the Amasi A4 renderer so each visible card is one unit."""
    global _INSTALLED, _ORIGINAL_RENDERER
    if _INSTALLED:
        return

    import reviewed_preparation_batches as batch

    _ORIGINAL_RENDERER = batch.render_preparation_batch_pdf

    @wraps(_ORIGINAL_RENDERER)
    def render_one_card_per_piece(batch_row: dict[str, Any]) -> bytes:
        assert _ORIGINAL_RENDERER is not None
        render_row = dict(batch_row)
        render_row["lines"] = expand_batch_lines_to_physical_pieces(batch_row)
        return _ORIGINAL_RENDERER(render_row)

    batch.render_preparation_batch_pdf = render_one_card_per_piece
    _INSTALLED = True


__all__ = [
    "expand_batch_lines_to_physical_pieces",
    "install_preparation_pdf_physical_piece_overlay",
]
