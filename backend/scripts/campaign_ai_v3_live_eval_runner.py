#!/usr/bin/env python3
"""Lightweight launcher for the Decision Intelligence V3 live eval.

The eval needs the exact Production reasoning prompts and structured schemas,
but it does not need FastAPI, Mongo, provider integrations, or the application
runtime.  The historical eval script imports the prompt constants from
``campaign_ai_decision_v3``; importing that runtime module pulls the whole app
stack.  This launcher injects a prompt-only compatibility module before loading
the eval, keeping the pre-Production model test isolated from runtime deps.
"""
from __future__ import annotations

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

from scripts.campaign_ai_v3_live_eval import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
