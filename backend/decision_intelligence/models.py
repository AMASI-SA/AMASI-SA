"""Typed contracts for Autonomous Decision Intelligence Phase 4."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DecisionAction = Literal["EXECUTE_NOW", "TEST", "WATCH", "DO_NOT_INTERVENE"]
ApprovalState = Literal["PENDING", "APPROVED", "REJECTED"]


@dataclass(frozen=True)
class DecisionSignal:
    signal_id: str
    source: str
    title: str
    evidence_count: int
    confidence: float
    expected_profit_delta_sar: float | None = None
    urgency: float = 0.0
    effort: float = 0.0
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DecisionRecommendation:
    signal_id: str
    action: DecisionAction
    priority_score: float
    confidence: float
    expected_profit_delta_sar: float | None
    reason: str
    evidence: tuple[dict[str, Any], ...]
    requires_owner_approval: bool = True
    read_only: bool = True


@dataclass(frozen=True)
class SimulationResult:
    scenario: str
    expected_profit_delta_sar: float | None
    downside_sar: float | None
    upside_sar: float | None
    confidence: float
    assumptions: tuple[str, ...]
    read_only: bool = True


@dataclass(frozen=True)
class ApprovalRequest:
    decision_id: str
    state: ApprovalState
    requested_action: DecisionAction
    rationale: str
    requires_owner_approval: bool = True
    execution_performed: bool = False
