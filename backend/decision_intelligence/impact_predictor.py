"""Conservative impact prediction helpers for decision simulations."""
from __future__ import annotations

from .models import SimulationResult


def choose_best_simulation(results: list[SimulationResult]) -> SimulationResult | None:
    """Return the strongest positive scenario with known impact and confidence."""
    known = [
        item for item in results
        if item.expected_profit_delta_sar is not None and item.confidence >= 0.5
    ]
    if not known:
        return None
    return max(
        known,
        key=lambda item: (
            item.expected_profit_delta_sar or float("-inf"),
            item.confidence,
        ),
    )
