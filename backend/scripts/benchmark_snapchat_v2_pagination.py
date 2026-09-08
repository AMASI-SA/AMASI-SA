"""Synthetic application-layer benchmark for SNAP-V2-PERF-UI-1.

This deliberately does not claim MongoDB ``explain`` results.  It measures
the Python/JSON boundary that changed: the legacy shape materializes and
serializes every campaign, while the bounded shape materializes one server
page plus a scalar report summary.  Run from the repository root with:

    python backend/scripts/benchmark_snapchat_v2_pagination.py
"""
from __future__ import annotations

import json
import statistics
import time
import tracemalloc
from typing import Any, Callable


CAMPAIGNS = 5_000
AD_SQUADS = 10_000
ADS = 20_000
PAGE_SIZE = 25
TRIALS = 7


def _campaign(index: int) -> dict[str, Any]:
    spend = round(100_000 / (index + 1), 2)
    orders = index % 11
    sales = float(orders * 185)
    return {
        "entity": {
            "id": f"campaign-{index:05d}",
            "name": f"Synthetic campaign {index:05d}",
            "level": "campaign",
            "status": "ACTIVE" if index % 3 else "PAUSED",
            "active": index % 3 != 0,
        },
        "delivery": {
            "spend": {"amount": spend, "currency": "SAR"},
            "impressions": index * 17,
            "clicks": index % 997,
        },
        "platform_outcomes": {
            "conversions": index % 13,
            "roas": round((index % 13) * 50 / spend, 4) if spend else None,
        },
        "commerce_outcomes": {
            "status": "complete",
            "orders": orders,
            "revenue": {"amount": sales, "currency": "SAR"},
        },
        "commerce_profitability": {
            "product_cost": {"amount": round(sales * 0.4, 2), "currency": "SAR"},
            "contribution_profit": {
                "amount": round(sales * 0.6 - spend, 2),
                "currency": "SAR",
            },
        },
    }


def _legacy_boundary() -> tuple[int, int]:
    rows = [_campaign(index) for index in range(CAMPAIGNS)]
    response = json.dumps(
        {"rows": rows, "page": 1, "page_size": PAGE_SIZE},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    # This slice is intentionally after full response construction, matching
    # the former client-visible pagination boundary.
    frontend_rows = rows[:PAGE_SIZE]
    return len(response), len(frontend_rows)


def _bounded_boundary() -> tuple[int, int]:
    rows = [_campaign(index) for index in range(PAGE_SIZE)]
    summary = {
        "entity_count": CAMPAIGNS,
        "spend_sar": 909_095.41,
        "snapchat_purchases": 29_994,
        "salla_orders": 24_994,
        "reconciliation": {"status": "synthetic_not_live"},
    }
    response = json.dumps(
        {
            "rows": rows,
            "page": 1,
            "page_size": PAGE_SIZE,
            "total": CAMPAIGNS,
            "filtered_total": CAMPAIGNS,
            "pages": CAMPAIGNS // PAGE_SIZE,
            "has_more": True,
            "summary": summary,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return len(response), len(rows)


def _measure(operation: Callable[[], tuple[int, int]]) -> dict[str, Any]:
    samples: list[float] = []
    peaks: list[int] = []
    response_bytes = 0
    frontend_rows = 0
    for _ in range(TRIALS):
        tracemalloc.start()
        started = time.perf_counter()
        response_bytes, frontend_rows = operation()
        samples.append((time.perf_counter() - started) * 1_000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
    return {
        "median_response_boundary_ms": round(statistics.median(samples), 3),
        "median_peak_traced_bytes": int(statistics.median(peaks)),
        "response_bytes": response_bytes,
        "frontend_rows": frontend_rows,
    }


def main() -> None:
    before = _measure(_legacy_boundary)
    after = _measure(_bounded_boundary)
    result = {
        "benchmark": "synthetic_application_boundary",
        "catalog": {
            "campaigns": CAMPAIGNS,
            "ad_squads": AD_SQUADS,
            "ads": ADS,
            "page_size": PAGE_SIZE,
            "trials": TRIALS,
        },
        "before": {
            **before,
            "python_campaign_rows_materialized": CAMPAIGNS,
            "settings_rows_loaded": 500,
            "hidden_ad_squad_settings_inputs_possible": AD_SQUADS,
            "initial_ad_squad_entity_rows": 0,
            "initial_ad_entity_rows": 0,
        },
        "after": {
            **after,
            "python_campaign_rows_materialized": PAGE_SIZE,
            "python_report_summary_rows_materialized": 1,
            "python_salla_summary_order_rows_materialized": 0,
            "settings_rows_loaded_max": PAGE_SIZE,
            "settings_child_rows_materialized": 0,
            "initial_ad_squad_entity_rows": 0,
            "initial_ad_entity_rows": 0,
        },
        "source_instrumented_command_bounds": {
            "campaign_core_mongo_commands": 2,
            "campaign_page_and_core_summary_commands": 1,
            "fact_source_coverage_commands": 1,
            "report_wide_salla_summary_commands": 1,
            "initial_ad_squad_entity_page_commands": 0,
            "initial_ad_entity_page_commands": 0,
        },
        "mongo_explain": {
            "documents_examined": "unavailable_without_live_explain",
            "documents_returned": "bounded_contract_tested_separately",
            "note": "Use the production-shaped staging dataset and explain('executionStats') before changing indexes; this task performs no production access.",
        },
    }
    result["reduction"] = {
        "python_campaign_rows_pct": round(
            (1 - result["after"]["python_campaign_rows_materialized"] / CAMPAIGNS) * 100,
            3,
        ),
        "response_bytes_pct": round(
            (1 - after["response_bytes"] / before["response_bytes"]) * 100,
            3,
        ),
        "peak_traced_memory_pct": round(
            (1 - after["median_peak_traced_bytes"] / before["median_peak_traced_bytes"]) * 100,
            3,
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
