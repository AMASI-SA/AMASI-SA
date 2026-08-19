#!/usr/bin/env python3
"""Lightweight launcher for the Decision Intelligence V3 live eval.

The eval needs the V3 reasoning prompts and structured schemas, but it does not
need FastAPI, Mongo, provider integrations, or the application runtime. The
historical eval script imports prompt constants from ``campaign_ai_decision_v3``;
importing that runtime module pulls the whole app stack. This launcher injects a
prompt-only compatibility module before loading the eval.

The historical eval also calls ``dotenv.load_dotenv`` before reading environment
variables. The lightweight launcher deliberately does not depend on
python-dotenv: the eval API key is supplied explicitly through ``OPENAI_API_KEY``.
When python-dotenv is absent we install a no-op compatibility module before the
historical script is imported. This keeps the focused eval independent of the
application dependency set and never reads/sources ``backend/.env``.

Optional environment:
  MEZAN_V3_EVAL_IDS          comma-separated scenario ids to run, preserving corpus order
  MEZAN_V3_EVAL_CHECKPOINT   persistent JSON checkpoint path; completed cases resume safely
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
from typing import Any

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

if importlib.util.find_spec("dotenv") is None:
    dotenv_stub = types.ModuleType("dotenv")

    def _load_dotenv_noop(*_args, **_kwargs):
        return False

    dotenv_stub.load_dotenv = _load_dotenv_noop
    sys.modules["dotenv"] = dotenv_stub

from scripts import campaign_ai_v3_live_eval as live_eval  # noqa: E402


_original_evaluate_case = live_eval.evaluate_case

ROOT_CAUSE_FAMILY_EXTENSIONS = {
    "good_ctr_low_atc": {"ADD_TO_CART"},
    "ad_old_price_product_new_price": {"CREATIVE"},
}

REQUIRED_ACTIONS_BY_CASE = {
    "low_stock_scale_candidate": {"RESTOCK_PRODUCT"},
}

TRANSIENT_ERROR_NAMES = {
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
}
TRANSIENT_BACKOFF_SECONDS = (20, 45, 90)
SAFE_RATE_LIMIT_HEADERS = (
    "retry-after",
    "x-request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
)


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


def _selected_cases() -> tuple[int, list[dict[str, Any]]]:
    start = max(0, int(os.environ.get("MEZAN_V3_EVAL_START", "0")))
    requested_limit = int(os.environ.get("MEZAN_V3_EVAL_LIMIT", str(len(live_eval.SCENARIOS))))
    cases = live_eval.SCENARIOS[start:start + max(1, requested_limit)]
    return start, cases


def _checkpoint_path() -> Path | None:
    raw = (os.environ.get("MEZAN_V3_EVAL_CHECKPOINT") or "").strip()
    return Path(raw).expanduser() if raw else None


def _contract_fingerprint(cases: list[dict[str, Any]]) -> str:
    payload = {
        "model": live_eval.MODEL,
        "first_pass": FIRST_PASS_INSTRUCTIONS,
        "second_pass": SECOND_PASS_INSTRUCTIONS,
        "cases": cases,
        "decision_schema": live_eval.v3_json_schema(),
        "review_schema": live_eval.review_json_schema(),
        # Keep v2 so checkpoints from the exact same model/prompt/schema corpus
        # remain valid. Transport retry/diagnostic changes do not alter the
        # marketing-evaluation contract.
        "runner_contract": 2,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _transient_failure_name(row: dict[str, Any]) -> str | None:
    failures = row.get("failures")
    if not isinstance(failures, list):
        return None
    for failure in failures:
        text = str(failure or "")
        if not text.startswith("eval_runtime_error:"):
            continue
        name = text.split(":", 1)[1].strip()
        if name in TRANSIENT_ERROR_NAMES:
            return name
    return None


def _load_checkpoint(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    path = _checkpoint_path()
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"V3_LIVE_EVAL_REFUSED_CHECKPOINT_UNREADABLE:{type(exc).__name__}") from exc
    expected_fingerprint = _contract_fingerprint(cases)
    expected_ids = [str(row["id"]) for row in cases]
    if payload.get("fingerprint") != expected_fingerprint:
        raise SystemExit("V3_LIVE_EVAL_REFUSED_CHECKPOINT_FINGERPRINT_MISMATCH")
    if payload.get("case_ids") != expected_ids:
        raise SystemExit("V3_LIVE_EVAL_REFUSED_CHECKPOINT_CASESET_MISMATCH")
    results = payload.get("results")
    if not isinstance(results, list):
        raise SystemExit("V3_LIVE_EVAL_REFUSED_CHECKPOINT_RESULTS_INVALID")
    by_id: dict[str, dict[str, Any]] = {}
    transient = 0
    for row in results:
        if not isinstance(row, dict):
            continue
        case_id = str(row.get("id") or "")
        if case_id not in expected_ids:
            continue
        if _transient_failure_name(row):
            transient += 1
            continue
        by_id[case_id] = row
    if by_id:
        print(f"V3_RESUME_LOADED={len(by_id)}", flush=True)
    if transient:
        print(f"V3_RESUME_RETRY_TRANSIENT={transient}", flush=True)
    return by_id


def _write_checkpoint(cases: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> None:
    path = _checkpoint_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [by_id[str(case["id"])] for case in cases if str(case["id"]) in by_id]
    payload = {
        "schema_version": "campaign_ai_v3_live_eval_checkpoint_v2",
        "model": live_eval.MODEL,
        "fingerprint": _contract_fingerprint(cases),
        "case_ids": [str(row["id"]) for row in cases],
        "completed": len(ordered),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "results": ordered,
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _safe_error_diagnostic(exc: Exception) -> dict[str, Any]:
    """Return safe transport diagnostics without credentials or request payloads."""
    diagnostic: dict[str, Any] = {
        "error": type(exc).__name__,
    }
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        diagnostic["status_code"] = status_code
    request_id = getattr(exc, "request_id", None)
    if request_id:
        diagnostic["request_id"] = str(request_id)[:200]

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error_body = body.get("error") if isinstance(body.get("error"), dict) else body
        for source_key, target_key in (("type", "api_error_type"), ("code", "api_error_code")):
            value = error_body.get(source_key)
            if value not in (None, ""):
                diagnostic[target_key] = str(value)[:200]
        message = error_body.get("message")
        if message not in (None, ""):
            # OpenAI error text contains limit/current/reset context, not the API key.
            diagnostic["message"] = " ".join(str(message).split())[:800]

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        safe_headers = {}
        for name in SAFE_RATE_LIMIT_HEADERS:
            try:
                value = headers.get(name)
            except Exception:
                value = None
            if value not in (None, ""):
                safe_headers[name] = str(value)[:200]
        if safe_headers:
            diagnostic["headers"] = safe_headers
    return diagnostic


def _print_safe_error_diagnostic(case_id: str, exc: Exception, *, attempt: int) -> None:
    payload = _safe_error_diagnostic(exc)
    payload["case"] = case_id
    payload["attempt"] = attempt
    print("V3_OPENAI_ERROR_DIAGNOSTIC " + json.dumps(payload, ensure_ascii=False), flush=True)


async def _run_case_with_transient_backoff(client: Any, case: dict[str, Any]) -> dict[str, Any]:
    attempts = len(TRANSIENT_BACKOFF_SECONDS) + 1
    for attempt in range(attempts):
        try:
            return await live_eval.run_case(client, case)
        except Exception as exc:
            name = type(exc).__name__
            _print_safe_error_diagnostic(str(case["id"]), exc, attempt=attempt + 1)
            if name not in TRANSIENT_ERROR_NAMES or attempt >= attempts - 1:
                return {
                    "id": str(case["id"]),
                    "description": case["description"],
                    "actions": [],
                    "roots": [],
                    "failures": [f"eval_runtime_error:{name}"],
                    "summary": "",
                }
            delay = TRANSIENT_BACKOFF_SECONDS[attempt]
            print(
                f"V3_TRANSIENT_RETRY case={case['id']} error={name} "
                f"attempt={attempt + 1}/{attempts} sleep={delay}s",
                flush=True,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


async def _main_async_with_checkpoint() -> int:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        print("V3_LIVE_EVAL_REFUSED: OPENAI_API_KEY missing")
        return 2

    start, cases = _selected_cases()
    by_id = _load_checkpoint(cases)
    client = live_eval.AsyncOpenAI(api_key=api_key, max_retries=1, timeout=180.0)
    try:
        for offset, case in enumerate(cases):
            index = start + offset + 1
            case_id = str(case["id"])
            if case_id in by_id:
                print(f"[{index}/{len(live_eval.SCENARIOS)}] {case_id} ... RESUME_SKIP", flush=True)
                continue
            print(f"[{index}/{len(live_eval.SCENARIOS)}] {case_id} ...", flush=True)
            result = await _run_case_with_transient_backoff(client, case)
            by_id[case_id] = result
            _write_checkpoint(cases, by_id)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await client.close()

    results = [by_id[str(case["id"])] for case in cases if str(case["id"]) in by_id]
    failed = [row for row in results if row.get("failures")]
    report = {
        "model": live_eval.MODEL,
        "start": start,
        "evaluated": len(results),
        "total_corpus": len(cases),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failed_ids": [row["id"] for row in failed],
        "all_requested_cases_passed": len(results) == len(cases) and not failed,
        "checkpoint_enabled": True,
    }
    print("\nV3_LIVE_EVAL_SUMMARY")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    _write_checkpoint(cases, by_id)
    return 1 if failed or len(results) != len(cases) else 0


def main() -> int:
    _apply_requested_ids()
    if _checkpoint_path() is None:
        return live_eval.main()
    return asyncio.run(_main_async_with_checkpoint())


if __name__ == "__main__":
    raise SystemExit(main())
