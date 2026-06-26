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

logger = logging.getLogger(__name__)

# Module-level singletons so the worker is idempotent if startup runs
# twice (uvicorn reload, in-flight test rerun, etc.).
_WORKER_TASK: Optional[asyncio.Task] = None
_LAST_RUN_AT: Optional[datetime] = None
_LAST_RUN_OK: bool = True
_LAST_ROUND: dict = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _one_round(db, *, user_id: str, batch_limit: int) -> dict:
    """Process one batch from each pending bucket. Returns counts."""
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


async def _loop(db, *, interval_sec: float, batch_limit: int) -> None:
    """Main poll loop. Runs forever; exceptions are logged not raised."""
    global _LAST_RUN_AT, _LAST_RUN_OK, _LAST_ROUND
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
        await asyncio.sleep(interval_sec)


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
    }
