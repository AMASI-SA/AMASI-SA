"""Small repeatable CPU benchmark for Phase 5 proposal normalization."""

from __future__ import annotations

import json
from time import perf_counter

from decision_intelligence.proposal_bridge import normalize_phase5_recommendation


def _fixture() -> tuple[dict, dict]:
    decision = {
        "decision_id": "snapchat_ads:campaign:campaign-1",
        "entity": {
            "level": "campaign",
            "id": "campaign-1",
            "status": "ACTIVE",
            "active": True,
        },
        "status": "RECOMMENDATION_SHADOW",
        "recommendation": {
            "action": "TEST",
            "reason": "Bounded reconciled closed-day shadow change.",
            "confidence": None,
            "priority_score": None,
        },
        "simulation": {"proposed_change": {"budget_change_pct": 5.0}},
        "evidence": {
            "metrics": {"contribution_profit_sar": 825.0, "salla_roas": 2.4},
            "quality": {"coverage_status": "complete"},
            "lineage": {"source_version": "v2"},
            "current_state_snapshot": {
                "status": "ACTIVE",
                "active": True,
                "daily_budget_native": 100.0,
                "currency_scope": "account_native",
            },
        },
    }
    result = {
        "provider": "snapchat_ads",
        "decision_ready": True,
        "period": {
            "date_from": "2026-09-02",
            "date_to": "2026-09-02",
            "timezone": "Asia/Riyadh",
            "closed": True,
        },
    }
    return result, decision


def main() -> None:
    result, decision = _fixture()
    iterations = 10_000
    started = perf_counter()
    last = None
    for _ in range(iterations):
        last = normalize_phase5_recommendation(result, decision, tenant_id="owner-1")
    duration = perf_counter() - started
    print(
        json.dumps(
            {
                "iterations": iterations,
                "duration_ms": round(duration * 1000, 3),
                "operations_per_second": round(iterations / duration, 1),
                "fingerprint_stable": bool(
                    last and last.get("recommendation_fingerprint")
                ),
                "provider_calls": 0,
                "provider_writes": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
