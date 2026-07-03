"""Iter-2026-02.rev25 — Worker-context SAS observability.

After Salla order 270131907 (mada, trace 8037f6cb08004142abb77b82f79c0030)
somehow reached INVOICE_CREATED in DRY-RUN — even though `mada` was NOT
in the tenant's allow-list — we need PROOF of what the running worker
saw at the moment of processing:

  • Did `_load_settings` actually return `selective_auto_send_enabled=True`?
  • What user_id did it read for?
  • Which allow-list came back?
  • Did the SAS gate function get called at all?
  • What sha of pipeline.py is this worker running?

This module writes ONE compact trace document into
`row.sas_worker_trace` on every worker call, and appends a step to
`row.stage_history`. It NEVER mutates settings, invoice ids, or the
pipeline stage. Read-only from a business-rules perspective.

Invariants
──────────
  1. NEVER leaks secrets (only presence flags for env, only settings
     keys the operator already sees in the UI).
  2. NEVER mutates settings / pipeline_stage / qoyod_* ids.
  3. Idempotent — repeated calls overwrite the same field; no growth.
  4. Fault-tolerant — swallows all errors internally so a trace-write
     bug can never abort a live pipeline run.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional


logger = logging.getLogger("qoyod.sas.worker_trace")


# Cached module-level sha of pipeline.py — computed once at import.
_PIPELINE_SHA: Optional[str] = None


def _compute_pipeline_sha() -> Optional[str]:
    global _PIPELINE_SHA
    if _PIPELINE_SHA is not None:
        return _PIPELINE_SHA
    try:
        from integrations.qoyod import pipeline as pmod
        path = getattr(pmod, "__file__", None)
        if not path:
            return None
        with open(path, "rb") as fh:
            _PIPELINE_SHA = hashlib.sha256(fh.read()).hexdigest()[:16]
        return _PIPELINE_SHA
    except (OSError, ImportError):
        return None


def _pick_settings_snapshot(settings: dict) -> dict:
    """Extract ONLY the SAS-relevant, operator-visible fields.

    NO secret keys (api tokens, webhook secrets) are ever included.
    """
    if not isinstance(settings, dict):
        return {"_error": "settings_not_dict"}
    return {
        "selective_auto_send_enabled":
            bool(settings.get("selective_auto_send_enabled", False)),
        "selective_auto_send_cutover_at":
            settings.get("selective_auto_send_cutover_at"),
        "selective_auto_send_allowed_payment_methods":
            list(settings.get(
                "selective_auto_send_allowed_payment_methods") or []),
        "selective_live_send_enabled":
            bool(settings.get("selective_live_send_enabled", False)),
        "production_writes_locked":
            bool(settings.get("production_writes_locked", False)),
        "dry_run_mode":
            bool(settings.get("dry_run_mode", False)),
        "invoice_trigger_statuses":
            list(settings.get("invoice_trigger_statuses") or []),
        # Payment mapping is signal-heavy (mapping absent → gate rejects
        # 8th invariant); we only emit KEYS, never account_id values.
        "payment_method_mapping_keys": [
            (m.get("salla_method") if isinstance(m, dict) else None)
            for m in (settings.get("payment_method_mapping") or [])
            if isinstance(m, dict)
        ],
        # Note: the row's user_id may fall back to defaults if the DB
        # has no doc — that's the diagnostic we need to detect.
        "_settings_doc_present":
            "invoice_trigger_statuses" in settings
            and "selective_auto_send_enabled" in settings,
    }


async def write_worker_trace(
    db,
    row: dict,
    *,
    stage: str,
    settings: dict,
    user_id_used: str,
    gate_ran: bool,
    gate_eligible: Optional[bool] = None,
    gate_reason: Optional[str]  = None,
    gate_detail: Optional[str]  = None,
    extras: Optional[dict]      = None,
) -> None:
    """Persist a compact `sas_worker_trace` snapshot on the row.

    Called from the top of both `process_normalized_row` and
    `process_customer_resolved_row`. Safe to call even when the row was
    just created in this same tick.

    Never raises. On error, logs at WARNING level and returns.
    """
    try:
        row_id = row.get("id")
        if not row_id:
            return

        trace = {
            "stage":                    stage,
            "checked_at":               datetime.now(timezone.utc).isoformat(),
            "worker_pipeline_sha":      _compute_pipeline_sha(),
            "worker_pid":               os.getpid(),
            "row_user_id":              row.get("user_id"),
            "user_id_used_for_settings": user_id_used,
            "settings_seen":            _pick_settings_snapshot(settings),
            "gate_ran":                 bool(gate_ran),
            "gate_eligible":            gate_eligible,
            "gate_reason":              gate_reason,
            "gate_detail":              gate_detail,
        }
        if extras:
            # Only allow scalar/dict extras — reject anything opaque.
            trace["extras"] = {
                k: v for k, v in extras.items()
                if isinstance(v, (str, int, float, bool, list, dict, type(None)))
            }

        # Idempotent: overwrites the field. history appended chronologically.
        await db.integration_inbox.update_one(
            {"id": row_id},
            {
                "$set": {"sas_worker_trace": trace},
                "$push": {
                    "sas_worker_trace_history": {
                        "$each":  [trace],
                        "$slice": -20,  # cap at last 20 traces
                    }
                },
            },
        )
    except Exception as e:  # noqa: BLE001 — never break the pipeline
        logger.warning(
            "sas_worker_trace_write_failed row_id=%s stage=%s err=%s",
            row.get("id"), stage, e,
        )
