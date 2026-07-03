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
    # rev17 was superseded by rev27 (strict live-write gate). No
    # marker required for rev17 anymore.
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
    # rev27 — Strict Live-Write Gate. If this marker is absent, the
    # deployed pipeline.py still has the old scoped-bypass logic that
    # can leak real Qoyod writes while `dry_run_mode=true`.
    "rev27_live_write_gate":
        "Iter-2026-02.rev27 — SINGLE source of truth for whether the",
    "rev27_get_api_client_strict":
        "Iter-2026-02.rev27 — STRICT Live-Write Gate (REPLACES rev17).",
    # rev28 — Atomic SAS gate persist + wording fix. If absent,
    # RULES_APPLIED could land without the gate persisted (regression
    # that caused order 270281278 observability gap).
    "rev28_atomic_gate_in_rules_applied":
        "rev28 — Include the SAS gate in the RULES_APPLIED write",
    # rev29 — Atomic CAS on ALL post-NORMALIZED transitions.
    "rev29_atomic_customer_resolved":
        "rev29 — Atomic CAS on RULES_APPLIED → CUSTOMER_RESOLVED",
    "rev29_atomic_product_resolved":
        "rev29 — Atomic CAS on CUSTOMER_RESOLVED → PRODUCT_RESOLVED",
    "rev29_atomic_invoice_created":
        "rev29 — Atomic CAS on PRODUCT_RESOLVED → INVOICE_CREATED",
    "rev29_atomic_invoice_payment":
        "rev29 — Atomic CAS INVOICE_CREATED → INVOICE_PAYMENT_CREATED",
    "rev29_atomic_completed":
        "rev29 — Atomic CAS on final COMPLETED transition",
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

    # rev26 — Control-flow invariant checker.
    # If SAS gate rejected the row (`eligible=false`) yet the row
    # advanced past SKIPPED into any pipeline stage that touches
    # customers / products / invoices / payments → hard violation.
    ADVANCED_STAGES = {
        "RULES_APPLIED", "CUSTOMER_RESOLVED", "PRODUCT_RESOLVED",
        "INVOICE_CREATED", "INVOICE_PAYMENT_CREATED",
        "PAYMENT_LINK_FAILED", "COMPLETED",
        "LOCKED_AWAITING_APPROVAL", "PARTIAL_FAILURE",
    }
    cur_stage = row.get("pipeline_stage")
    sas_rejected = (
        isinstance(sas_gate, dict) and sas_gate.get("eligible") is False)
    control_flow_violation = bool(
        sas_rejected and cur_stage in ADVANCED_STAGES)

    # rev27 — Live-Write Gate Violation invariant.
    # A REAL Qoyod id must NEVER appear on a row whose live-write
    # gates say "no writes". We fetch the current settings and
    # compare against the row's ids.
    live_write_gate_violation = False
    live_write_violation_reason = None
    # rev28 — SAS Gate Missing invariant: if SAS is enabled AND the
    # row advanced past NORMALIZED yet `selective_auto_send_gate`
    # is missing on disk, that's a critical observability violation.
    sas_gate_missing_violation = False
    sas_gate_missing_reason = None
    ADVANCED_AFTER_NORMALIZED = {
        "RULES_APPLIED", "CUSTOMER_RESOLVED", "PRODUCT_RESOLVED",
        "INVOICE_CREATED", "INVOICE_PAYMENT_CREATED",
        "PAYMENT_LINK_FAILED", "COMPLETED",
        "LOCKED_AWAITING_APPROVAL", "PARTIAL_FAILURE",
    }
    real_customer = bool(row.get("qoyod_customer_id") and
                         isinstance(row.get("qoyod_customer_id"), (str, int))
                         and not str(row.get("qoyod_customer_id"))
                             .startswith(("DRY:", "PREVIEW:")))
    real_invoice = bool(qid and isinstance(qid, str)
                        and not qid.startswith(("DRY:", "PREVIEW:")))
    real_payment = bool(row.get("qoyod_invoice_payment_id") and
                        isinstance(row.get("qoyod_invoice_payment_id"),
                                   (str, int))
                        and not str(row.get("qoyod_invoice_payment_id"))
                            .startswith(("DRY:", "PREVIEW:")))

    # Settings fetch for BOTH invariants (single round-trip).
    try:
        user_id = row.get("user_id", "main")
        settings_doc = await db.qoyod_settings.find_one(
            {"user_id": user_id}, {"_id": 0}) or {}
    except Exception:  # noqa: BLE001
        settings_doc = {}

    # ── rev28 — SAS Gate Missing check ──────────────────────────────
    if bool(settings_doc.get("selective_auto_send_enabled", False)) \
            and cur_stage in ADVANCED_AFTER_NORMALIZED \
            and sas_gate is None:
        sas_gate_missing_violation = True
        sas_gate_missing_reason = (
            "Auto-send row advanced past NORMALIZED without "
            "persisted SAS gate")

    # ── rev27 — Live-Write leak check ───────────────────────────────
    if real_customer or real_invoice or real_payment:
        try:
            violations = []
            if bool(settings_doc.get("dry_run_mode", False)):
                violations.append("dry_run_mode=true")
            if not bool(settings_doc.get(
                    "selective_live_send_enabled", False)):
                violations.append("selective_live_send_enabled=false")
            if bool(settings_doc.get("production_writes_locked", False)):
                violations.append("production_writes_locked=true")
            if violations:
                live_write_gate_violation = True
                real_kinds = []
                if real_customer: real_kinds.append("customer")
                if real_invoice:  real_kinds.append("invoice")
                if real_payment:  real_kinds.append("invoice_payment")
                live_write_violation_reason = (
                    f"Real Qoyod {'+'.join(real_kinds)} write occurred "
                    f"while: {', '.join(violations)}")
        except Exception as e:  # noqa: BLE001
            live_write_violation_reason = f"invariant_check_failed: {e!r}"

    # ── rev29 — Duplicate stage transition invariant ────────────────
    # For a single row, no (from_stage, to_stage) pair MUST repeat.
    # If it does, a stale worker / requeue advanced the row past a
    # stage it had already left. Detected here purely by inspecting
    # `stage_history`.
    duplicate_stage_transition_violation = False
    duplicate_stage_transition_reason = None
    hist = row.get("stage_history") or []
    if isinstance(hist, list):
        seen: dict = {}
        dup_key = None
        for entry in hist:
            if not isinstance(entry, dict):
                continue
            key = (entry.get("from_stage"), entry.get("to_stage"))
            if key[0] is None and key[1] is None:
                continue
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1 and dup_key is None:
                dup_key = key
        if dup_key is not None:
            duplicate_stage_transition_violation = True
            duplicate_stage_transition_reason = (
                "Pipeline stage transition repeated for same trace_id "
                f"({dup_key[0]}→{dup_key[1]} occurred "
                f"{seen[dup_key]}× in stage_history)")

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
            "qoyod_invoice_id_is_real":  real_invoice,
            # rev26 — control-flow invariant.
            "control_flow_violation":    control_flow_violation,
            "violation_reason":          (
                "SAS rejected but row advanced past SKIPPED"
                if control_flow_violation else None),
            # rev27 — live-write gate violation.
            "live_write_gate_violation": live_write_gate_violation,
            "live_write_violation_reason": live_write_violation_reason,
            # rev28 — SAS gate missing invariant.
            "sas_gate_missing_violation": sas_gate_missing_violation,
            "sas_gate_missing_reason":    sas_gate_missing_reason,
            # rev29 — Duplicate stage transition invariant.
            "duplicate_stage_transition_violation":
                duplicate_stage_transition_violation,
            "duplicate_stage_transition_reason":
                duplicate_stage_transition_reason,
        },
    }
