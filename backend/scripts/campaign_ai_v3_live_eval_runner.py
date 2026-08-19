#!/usr/bin/env python3
"""Lightweight launcher for the Decision Intelligence V3 live eval.

The eval needs the V3 reasoning prompts and structured schemas, but it does not
need FastAPI, Mongo, provider integrations, or the application runtime. The
historical eval script imports prompt constants from ``campaign_ai_decision_v3``;
importing that runtime module pulls the whole app stack. This launcher injects a
prompt-only compatibility module before loading the eval.

Optional environment:
  MEZAN_V3_EVAL_IDS   comma-separated scenario ids to run, preserving corpus order
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import types

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from campaign_ai_decision_prompts_v3 import (  # noqa: E402
    FIRST_PASS_INSTRUCTIONS,
    SECOND_PASS_INSTRUCTIONS,
)

prompt_stub = types.ModuleType("campaign_ai_decision_v3")
prompt_stub.FIRST_PASS_INSTRUCTIONS = FIRST_PASS_INSTRUCTIONS
prompt_stub.SECOND_PASS_INSTRUCTIONS = SECOND_PASS_INSTRUCTIONS
sys.modules["campaign_ai_decision_v3"] = prompt_stub

from scripts import campaign_ai_v3_live_eval as live_eval  # noqa: E402


_original_evaluate_case = live_eval.evaluate_case

# The corpus describes broad acceptable diagnosis families. These extensions do
# not force an action or waive a bad outcome; they recognize equally valid,
# more-localized members of the V3 root-cause taxonomy that the original
# hand-written corpus omitted.
ROOT_CAUSE_FAMILY_EXTENSIONS = {
    # Healthy clicks/page visits followed by collapsed ATC can localize directly
    # to the Add-To-Cart step instead of the broader landing/product buckets.
    "good_ctr_low_atc": {"ADD_TO_CART"},
    # A stale price rendered in the ad itself is legitimately a Creative root
    # cause as well as Offer/Landing/Product mismatch.
    "ad_old_price_product_new_price": {"CREATIVE"},
}

# Business requirements that must be present, not merely one action among an
# acceptable family. A commercially strong campaign constrained by <1 day of
# stock must explicitly surface replenishment while any scale write stays
# blocked until capacity is restored.
REQUIRED_ACTIONS_BY_CASE = {
    "low_stock_scale_candidate": {"RESTOCK_PRODUCT"},
}


def _evaluate_case_with_execution_contract(case, output):
    eval_case = dict(case)
    extensions = ROOT_CAUSE_FAMILY_EXTENSIONS.get(str(case.get("id") or ""), set())
    if extensions:
        current = list(eval_case.get("acceptable_root_causes") or [])
        eval_case["acceptable_root_causes"] = list(dict.fromkeys([*current, *sorted(extensions)]))

    failures = list(_original_evaluate_case(eval_case, output))

    required_actions = REQUIRED_ACTIONS_BY_CASE.get(str(case.get("id") or ""), set())
    if required_actions:
        observed_actions = {item.recommended_action for item in output.recommendations}
        missing = sorted(required_actions - observed_actions)
        if missing:
            failures.append(f"required_actions_missing:{missing}")

    forbidden_executable = set(case.get("must_not_executable_actions") or [])
    if forbidden_executable:
        for item in output.recommendations:
            if item.recommended_action in forbidden_executable and bool(item.executable):
                failures.append(
                    f"forbidden_executable_action:{item.recommended_action}:{item.recommendation_id}"
                )
    return failures


live_eval.evaluate_case = _evaluate_case_with_execution_contract


def _apply_requested_ids() -> None:
    raw = (os.environ.get("MEZAN_V3_EVAL_IDS") or "").strip()
    if not raw:
        return
    requested = [item.strip() for item in raw.split(",") if item.strip()]
    requested_set = set(requested)
    available = {row["id"] for row in live_eval.SCENARIOS}
    unknown = [item for item in requested if item not in available]
    if unknown:
        raise SystemExit(f"V3_LIVE_EVAL_REFUSED_UNKNOWN_IDS:{','.join(unknown)}")
    selected = [row for row in live_eval.SCENARIOS if row["id"] in requested_set]
    if len(selected) != len(requested_set):
        raise SystemExit("V3_LIVE_EVAL_REFUSED_DUPLICATE_OR_MISSING_IDS")
    live_eval.SCENARIOS = selected
    os.environ["MEZAN_V3_EVAL_START"] = "0"
    os.environ["MEZAN_V3_EVAL_LIMIT"] = str(len(selected))
    print(f"V3_TARGETED_SCENARIOS={len(selected)}", flush=True)


def main() -> int:
    _apply_requested_ids()
    return live_eval.main()


if __name__ == "__main__":
    raise SystemExit(main())
