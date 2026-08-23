"""Governed Product Discovery Engine V1.

Recommendation-only layer for Growth Intelligence.
No supplier, catalog, price, inventory, Salla, or campaign writes.
Unknown economics remain unknown.
"""
from __future__ import annotations

from datetime import date
from typing import Any

CONTRACT_VERSION = "product_discovery_engine_v1"


def build_product_discovery_candidates(
    *,
    candidates: list[dict[str, Any]],
    audience_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    results = []
    evidence = audience_evidence if isinstance(audience_evidence, list) else []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        product = str(candidate.get("product_name") or "").strip()
        if not product:
            continue

        known_cost = all(
            candidate.get(key) is not None
            for key in (
                "landed_cost_sar",
                "expected_price_sar",
            )
        )

        margin = None
        if known_cost:
            margin = round(
                float(candidate["expected_price_sar"])
                - float(candidate["landed_cost_sar"]),
                2,
            )

        results.append(
            {
                "candidate_id": str(candidate.get("candidate_id") or product),
                "product_name": product,
                "category": str(candidate.get("category") or "unknown"),
                "recommendation": "WATCH",
                "confidence": "medium" if evidence else "low",
                "unit_economics_known": known_cost,
                "expected_margin_sar": margin,
                "why_it_matches_amasi_audience": candidate.get(
                    "audience_reasons", []
                ),
                "risks": candidate.get("risks", []),
                "requires_owner_approval": True,
            }
        )

    return {
        "contract_version": CONTRACT_VERSION,
        "as_of": date.today().isoformat(),
        "read_only": True,
        "candidates": results,
        "guardrails": [
            "No autonomous supplier ordering.",
            "No autonomous product publishing.",
            "Unknown economics remain unknown.",
        ],
    }


__all__ = ["CONTRACT_VERSION", "build_product_discovery_candidates"]
