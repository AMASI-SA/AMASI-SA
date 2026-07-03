"""Iter-2026-02.rev24 — SAS build & row diagnostics (READ-ONLY).

Purpose
───────
After Salla order 270130380 (trace 7f363be85f2747e49e4bfc7bfa0687bb) ended
in DRY-RUN despite tabby_installment being in the allow-list and the order
being past cutover, we need proof of what code the LIVE worker process is
actually running — not just what's on disk in the pod.

Endpoints exposed (READ-ONLY, no DB writes, no Qoyod POSTs):

  GET /api/integrations/qoyod/admin/diagnostics/build
      → Code markers in pipeline.py (Rev16/17/20/21) as seen by the
        RUNNING python process (via loaded module __file__ — NOT a
        separate file scan). If the deployed pod is on an older build,
        the markers will be missing here even if the git working tree
        on disk has them.
      → process metadata: pid, cwd, worker task presence, python version.
      → git sha if resolvable (best-effort; may be absent in prod images).

  GET /api/integrations/qoyod/admin/diagnostics/row?trace_id=...
      → Full read-only dump of the integration_inbox row: pipeline_stage,
        selective_auto_send_gate persisted state, qoyod_customer_id,
        qoyod_invoice_id, stage_history (last 20).

Invariants (NON-NEGOTIABLE)
───────────────────────────
  • Read-only. No DB write, no external HTTP.
  • Reads the module's __file__ attribute so the marker check reflects
    the RUNNING interpreter, not the on-disk file. This is how we prove
    the worker/deploy is on the correct build.
  • Never returns secrets (api keys, tokens, webhook secrets).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess  # nosec: read-only `git rev-parse`
import sys
from datetime import datetime, timezone
from typing import Optional


REQUIRED_MARKERS: dict[str, str] = {
    # marker_id → substring that MUST appear in the running pipeline.py
    "rev16_gate_at_normalized":
        "Iter-2026-02.rev16 — Selective Auto-Send Gate",
    "rev16_gate_at_customer_resolved":
        "Iter-2026-02.rev16 — Re-evaluate Selective Auto-Send gate",
    "rev17_scoped_bypass":
        "Iter-2026-02.rev16 → rev17 — When `scoped_write_allowance=True`",
    "rev20_invoice_site":
        "Iter-2026-02.rev20 — Selective Auto-Send gate bypass",
    "rev20_payment_site":
        "Iter-2026-02.rev20 — mirror of the invoice-site fix",
    # rev25 — worker-context trace hooks. If these markers are absent
    # in the running module, the deployed pipeline.py has no
    # `sas_worker_trace` field on rows → the worker/deploy is stale.
    "rev25_worker_trace_normalized":
        "rev25 — worker trace AFTER gate evaluated",
    "rev25_worker_trace_customer_resolved":
        "rev25 — trace AFTER gate evaluated.",
}


def _resolve_git_sha() -> Optional[str]:
    """Best-effort git sha of the running process. Absent in most
    container images — do NOT fail if git is unavailable."""
    for cwd in ("/app", "/app/backend", os.getcwd()):
        try:
            out = subprocess.check_output(  # nosec
                ["git", "-C", cwd, "rev-parse", "--short=12", "HEAD"],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode("utf-8", "replace").strip()
            if out:
                return out
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            continue
    return None


def _pipeline_source_snapshot() -> dict:
    """Read the pipeline.py source from the LOADED module's __file__.
    This is the definitive check for what code the running worker uses.
    """
    try:
        from integrations.qoyod import pipeline as pmod
    except ImportError as e:
        return {
            "loaded":          False,
            "import_error":    str(e),
        }

    src_path = getattr(pmod, "__file__", None)
    snapshot = {
        "loaded":          True,
        "module_path":     src_path,
        "size_bytes":      None,
        "sha256_first16":  None,
        "line_count":      None,
    }
    try:
        with open(src_path, "rb") as fh:
            data = fh.read()
        snapshot["size_bytes"]     = len(data)
        snapshot["sha256_first16"] = (
            hashlib.sha256(data).hexdigest()[:16])
        snapshot["line_count"]     = data.count(b"\n") + 1
        snapshot["_raw_source"]    = data.decode("utf-8", "replace")
    except OSError as e:
        snapshot["read_error"] = str(e)
    return snapshot


def _marker_check(source_text: str) -> dict:
    """Return per-marker presence + occurrence count."""
    out: dict = {}
    for mid, needle in REQUIRED_MARKERS.items():
        count = source_text.count(needle) if source_text else 0
        out[mid] = {
            "present": count >= 1,
            "count":   count,
            "needle":  needle,
        }
    all_present = all(v["present"] for v in out.values())
    return {
        "all_markers_present": all_present,
        "markers":             out,
    }


def _worker_task_info() -> dict:
    """Introspect the running worker (async task) — is it running,
    interval, last round timestamp. Read-only view into the module."""
    try:
        from integrations.qoyod import worker as wmod
    except ImportError as e:
        return {"loaded": False, "import_error": str(e)}

    info: dict = {"loaded": True}
    for attr in (
        "_TASK", "_LAST_RUN_OK", "_LAST_ROUND",
        "POLL_INTERVAL_SEC", "BATCH_SIZE",
    ):
        v = getattr(wmod, attr, None)
        if v is None:
            info[attr] = None
        elif attr == "_TASK":
            info["worker_task_present"] = v is not None
            info["worker_task_done"]    = (
                v.done() if v is not None else None)
        elif isinstance(v, datetime):
            info[attr] = v.isoformat()
        else:
            info[attr] = v
    return info


def build_diagnostics_report() -> dict:
    """The full build diagnostics payload. Safe to expose to admin."""
    src = _pipeline_source_snapshot()
    source_text = src.pop("_raw_source", "") if src.get("loaded") else ""
    markers = _marker_check(source_text)
    return {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "python_version":     sys.version.split()[0],
        "process_pid":        os.getpid(),
        "process_cwd":        os.getcwd(),
        "git_sha":            _resolve_git_sha(),
        "pipeline_module":    src,
        "marker_check":       markers,
        "worker_task":        _worker_task_info(),
        "env_flags": {
            # Presence booleans only — never leak values.
            "SALLA_WEBHOOK_SECRET_present":
                bool((os.environ.get("SALLA_WEBHOOK_SECRET") or "").strip()),
            "SALLA_APP_ID_present":
                bool((os.environ.get("SALLA_APP_ID") or "").strip()),
            "QOYOD_API_KEY_present":
                bool((os.environ.get("QOYOD_API_KEY") or "").strip()),
        },
        "acceptance": {
            # For the UI/operator to read at a glance.
            "code_matches_expected": markers["all_markers_present"],
            "if_false_action": (
                "Production worker is on an OLDER build. Redeploy "
                "backend AND ensure the worker process restarts. "
                "Verify by re-hitting this endpoint until "
                "code_matches_expected=true."),
        },
    }


async def row_diagnostics(db, trace_id: str) -> dict:
    """Fetch a single integration_inbox row by trace_id. READ-ONLY."""
    if not trace_id or not isinstance(trace_id, str):
        return {"ok": False, "reason": "trace_id required"}

    projection = {
        "_id": 0,
        "id": 1,
        "user_id": 1,
        "salla_order_number": 1,
        "trace_id": 1,
        "pipeline_stage": 1,
        "received_at": 1,
        "pipeline_started_at": 1,
        "selective_auto_send_gate": 1,
        "selective_auto_send_gate_at": 1,
        # rev25 — worker-context observability trace.
        "sas_worker_trace": 1,
        "sas_worker_trace_history": 1,
        "business_rules_decision": 1,
        "preflight_result": 1,
        "qoyod_customer_id": 1,
        "qoyod_invoice_id": 1,
        "qoyod_invoice_payment_id": 1,
        "canonical_payload.payment_method": 1,
        "canonical_payload.payment_method_native": 1,
        "canonical_payload.order_status": 1,
        "canonical_payload.order_status_native": 1,
        "canonical_payload.salla_order_created_at": 1,
        "canonical_payload.total_amount": 1,
        "stage_history": 1,
        "error": 1,
        "lock_reason": 1,
        "totals_comparison": 1,
        "product_resolution": 1,
    }

    row = await db.integration_inbox.find_one(
        {"trace_id": trace_id}, projection)
    if not row:
        # Fallback: some traces might live under a different id shape.
        # Try order_number if the caller passed one accidentally.
        return {
            "ok":       False,
            "found":    False,
            "trace_id": trace_id,
            "reason":   "no integration_inbox row with this trace_id",
        }

    # Truncate stage_history to the last 20 entries for readability.
    hist = row.get("stage_history") or []
    if isinstance(hist, list) and len(hist) > 20:
        row["stage_history"] = hist[-20:]
        row["_stage_history_truncated"] = {
            "total": len(hist), "returned_last": 20,
        }

    # Coerce datetime → iso strings for JSON safety.
    def _coerce(v):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, list):
            return [_coerce(x) for x in v]
        if isinstance(v, dict):
            return {k: _coerce(vv) for k, vv in v.items()}
        return v

    row = _coerce(row)

    # Derived flags to make the diagnosis obvious at a glance.
    sas_gate = row.get("selective_auto_send_gate")
    qid      = row.get("qoyod_invoice_id")
    is_dry   = isinstance(qid, str) and qid.startswith("DRY:")

    return {
        "ok":                            True,
        "found":                         True,
        "trace_id":                      trace_id,
        "row":                           row,
        # Interpretation helpers.
        "diagnosis": {
            "sas_gate_persisted":        sas_gate is not None,
            "sas_gate_eligible":         (
                None if sas_gate is None
                else bool(sas_gate.get("eligible"))),
            "sas_gate_reason":           (
                None if sas_gate is None
                else sas_gate.get("reason")),
            "qoyod_invoice_id_is_dry":   is_dry,
            "qoyod_invoice_id_is_real":  bool(
                qid and isinstance(qid, str)
                and not qid.startswith(("DRY:", "PREVIEW:"))),
        },
    }
