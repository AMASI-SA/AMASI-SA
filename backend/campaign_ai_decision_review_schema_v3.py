"""Auditable second-pass review contract for Decision Intelligence V3."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from campaign_ai_decision_schema_v3 import DecisionOutputV3


class DecisionReviewOutputV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_decision: DecisionOutputV3
    reviewed_budget_owner_keys: list[str]
    counterfactual_reviewed_recommendation_ids: list[str]
    review_limitations: list[str]


def review_json_schema() -> dict[str, Any]:
    return DecisionReviewOutputV3.model_json_schema()


__all__ = ["DecisionReviewOutputV3", "review_json_schema"]
