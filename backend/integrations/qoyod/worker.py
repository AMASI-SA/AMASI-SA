"""Qoyod Pipeline Worker — auto-advances NORMALIZED → COMPLETED.

Why this exists
───────────────
The webhook handler (sync) only drives a row up to `NORMALIZED`. The
subsequent stages (RULES_APPLIED → CUSTOMER_RESOLVED → INVOICE_CREATED
→ RECEIPT_CREATED) were originally designed to be triggered by a
background worker that polls the inbox.

Before this module was wired in, rows sat at `NORMALIZED` forever
unless an operator manually POSTed to:
    /api/integrations/qoyod/pipeline/process-normalized
    /api/integrations/qoyod/pipeline/process-customer-resolved

That UX is unacceptable for First Production Dry Run. This module
fills the gap with an `asyncio` loop that runs every `interval_sec`
and progresses pending rows in batches.

Operational notes
─────────────────
• Each iteration is wrapped in `try/except` so a single bad row never
  kills the worker.
• `batch_limit` keeps each tick bounded — prevents stampedes.
• `is_running()` exposes liveness for `/healthz`-style checks.
• `run_now()` synchronously drains the queue once — used by the UI's
  "Advance Now" emergency button.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from integrations.qoyod.pipeline import (
    process_pending_normalized,
    process_pending_customer_resolved,
)
from integrations.qoyod.dead_letter_requeue import (
    auto_requeue_known_fixed,
)
from integrations.qoyod.backfill_gate import (
    skip_pre_activation_rows,
)

logger = logging.getLogger(__name__)

# Module-level singletons so the worker is idempotent if startup runs
# twice (uvicorn reload, in-flight test rerun, etc.).
_WORKER_TASK: Optional[asyncio.Task] = None
_LAST_RUN_AT: Optional[datetime] = None
_LAST_RUN_OK: bool = True
_LAST_ROUND: dict = {}
_NEXT_POLL_DELAY_SEC: Optional[float] = None
FROZEN_CONTROL_POLL_INTERVAL_SEC = 300.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _one_round(db, *, user_id: str, batch_limit: int) -> dict:
    """Process one batch from each pending bucket. Returns counts."""
    # The production Plan-B sender is isolated from this historical path.
    # When the legacy pipeline is frozen this worker must remain a hard no-op,
    # even though the connector master switch is enabled for Plan B.
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id},
        {"_id": 0, "legacy_pipeline_frozen": 1},
    ) or {}
    if settings.get("legacy_pipeline_frozen"):
        return {
            "status": "legacy_pipeline_frozen",
            "processed": 0,
            "at": _now().isoformat(),
        }

    # ── Step 0a: Backfill Gate (user directive 2026-02-27) ──────────
    # Default `backfill_mode="now_forward_only"`: pre-activation rows
    # in NORMALIZED / CUSTOMER_RESOLVED / PRODUCT_RESOLVED are SKIPPED
    # so they never reach Qoyod. Operator must explicitly set
    # `backfill_mode="backfill_unsent"` to opt in to backfill.
    try:
        backfill_result = await skip_pre_activation_rows(
            db, user_id=user_id, limit=batch_limit)
    except Exception:
        logger.exception("qoyod backfill gate failed (worker tick)")
        backfill_result = {"ok": False, "scanned": 0, "skipped": 0}

    # ── Step 0b: Auto-Requeue (self-healing for KNOWN_FIXED_PATTERNS) ─
    # Runs BEFORE the drain so any rows it flips back into NORMALIZED
    # /CUSTOMER_RESOLVED get picked up in this same tick. Strictly
    # bounded by the pattern registry — generic DEAD_LETTER rows are
    # untouched. Bounded retries (`requeue_attempts ≤ MAX_REQUEUE_ATTEMPTS`)
    # prevent infinite loops.
    try:
        requeue_result = await auto_requeue_known_fixed(
            db, user_id=user_id, actor="worker", limit=batch_limit)
    except Exception:
        logger.exception("qoyod auto-requeue failed (worker tick)")
        requeue_result = {"ok": False, "scanned": 0, "requeued": 0}

    n_results = await process_pending_normalized(
        db, user_id, limit=batch_limit)
    cr_results = await process_pending_customer_resolved(
        db, user_id, limit=batch_limit)

    def _summary(results):
        if not isinstance(results, dict):
            return {"processed": 0}
        rows = results.get("rows") or []
        outcomes: dict[str, int] = {}
        for r in rows:
            oc = r.get("outcome") or "unknown"
            outcomes[oc] = outcomes.get(oc, 0) + 1
        return {"processed": len(rows), "outcomes": outcomes}

    return {
        "backfill_gate": {
            "scanned": int(backfill_result.get("scanned") or 0),
            "skipped": int(backfill_result.get("skipped") or 0),
            "mode":    backfill_result.get("mode"),
        },
        "auto_requeue": {
            "scanned":  int(requeue_result.get("scanned") or 0),
            "requeued": int(requeue_result.get("requeued") or 0),
            "skipped_no_pattern":   int(requeue_result.get("skipped_no_pattern") or 0),
            "skipped_max_attempts": int(requeue_result.get("skipped_max_attempts") or 0),
            "failures": int(requeue_result.get("failures") or 0),
        },
        "normalized":         _summary(n_results),
        "customer_resolved":  _summary(cr_results),
        "at":                 _now().isoformat(),
    }


async def run_now(db, *, user_id: str = "main",
                  batch_limit: int = 25) -> dict:
    """Drain one round synchronously. Used by the UI's manual button.
    Returns the per-bucket summary."""
    global _LAST_RUN_AT, _LAST_RUN_OK, _LAST_ROUND
    try:
        result = await _one_round(db, user_id=user_id,
                                  batch_limit=batch_limit)
        _LAST_RUN_OK = True
        _LAST_ROUND = result
        return result
    except Exception as exc:
        _LAST_RUN_OK = False
        logger.exception("qoyod pipeline worker run_now failed")
        return {"error": f"{exc.__class__.__name__}: {exc}",
                "at": _now().isoformat()}
    finally:
        _LAST_RUN_AT = _now()


def _next_poll_delay(last_round: dict, *, interval_sec: float) -> float:
    active_delay = max(0.1, float(interval_sec))
    if last_round.get("status") == "legacy_pipeline_frozen":
        return max(active_delay, FROZEN_CONTROL_POLL_INTERVAL_SEC)
    return active_delay


async def _loop(db, *, interval_sec: float, batch_limit: int) -> None:
    """Main poll loop. Runs forever; exceptions are logged not raised."""
    global _LAST_RUN_AT, _LAST_RUN_OK, _LAST_ROUND, _NEXT_POLL_DELAY_SEC
    logger.info("qoyod pipeline worker started (interval=%ss, batch=%s)",
                interval_sec, batch_limit)
    while True:
        try:
            _LAST_ROUND = await _one_round(
                db, user_id="main", batch_limit=batch_limit)
            _LAST_RUN_OK = True
        except Exception:
            _LAST_RUN_OK = False
            logger.exception("qoyod pipeline worker tick failed")
        _LAST_RUN_AT = _now()
        _NEXT_POLL_DELAY_SEC = _next_poll_delay(
            _LAST_ROUND,
            interval_sec=interval_sec,
        )
        await asyncio.sleep(_NEXT_POLL_DELAY_SEC)


def start_worker(db, *, interval_sec: float = 5.0,
                 batch_limit: int = 25) -> asyncio.Task:
    """Spawn the worker on application startup. Idempotent — calling
    twice returns the existing task."""
    global _WORKER_TASK
    if _WORKER_TASK is not None and not _WORKER_TASK.done():
        return _WORKER_TASK
    _WORKER_TASK = asyncio.create_task(
        _loop(db, interval_sec=interval_sec, batch_limit=batch_limit),
        name="qoyod-pipeline-worker",
    )
    return _WORKER_TASK


def is_running() -> bool:
    return _WORKER_TASK is not None and not _WORKER_TASK.done()


def liveness() -> dict:
    """Diagnostic snapshot for `/worker/status`."""
    return {
        "running":     is_running(),
        "last_run_at": _LAST_RUN_AT.isoformat() if _LAST_RUN_AT else None,
        "last_run_ok": _LAST_RUN_OK,
        "last_round":  _LAST_ROUND,
        "next_poll_delay_sec": _NEXT_POLL_DELAY_SEC,
    }
