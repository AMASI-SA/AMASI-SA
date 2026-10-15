"""Read-only scenario simulation for proposed business actions."""
from __future__ import annotations

from .models import SimulationResult


def simulate_action(
    *,
    scenario: str,
    current_profit_sar: float | None,
    expected_profit_sar: float | None,
    confidence: float,
    assumptions: list[str] | None = None,
) -> SimulationResult:
    confidence = max(0.0, min(1.0, float(confidence)))
    delta = None
    downside = None
    upside = None
    if current_profit_sar is not None and expected_profit_sar is not None:
        delta = round(float(expected_profit_sar) - float(current_profit_sar), 2)
        uncertainty = abs(delta) * (1.0 - confidence)
        downside = round(delta - uncertainty, 2)
        upside = round(delta + uncertainty, 2)
    return SimulationResult(
        scenario=scenario,
        expected_profit_delta_sar=delta,
        downside_sar=downside,
        upside_sar=upside,
        confidence=confidence,
        assumptions=tuple(assumptions or []),
    )
