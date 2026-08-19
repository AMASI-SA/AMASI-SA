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
