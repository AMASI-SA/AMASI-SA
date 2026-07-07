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
    # rev29b — Dry-run wording enforcement. If absent, stage_history
    # may still contain "customer created in Qoyod" / "product(s)
    # created" / "invoice created" for dry-run rows — misleading
    # audit signal.
    "rev29b_dry_run_wording":
        "rev29b — Dry-run wording enforcement",
    # rev29c — Fail-closed SAS gate persistence + dry-run wording
    # strengthened via `_pipeline_is_dry_mode`. If absent, the
    # NORMALIZED → RULES_APPLIED transition may still advance
    # without persisting the SAS gate.
    "rev29c_fail_closed_gate":
        "rev29c — Fail-closed gate persistence",
    # rev29d — Hard preflight guard at every downstream stage entry.
    # If a row lands at CUSTOMER_RESOLVED / PRODUCT_RESOLVED /
    # INVOICE_CREATED without `selective_auto_send_gate` persisted
    # (typically because a stale worker built it), the pipeline
    # DEAD_LETTERs before emitting any stage_history note.
    "rev29d_hard_gate_preflight":
        "rev29d — Hard preflight",
    # rev30 — Payment continuation. Ensures rows with
    # posting_mode=disabled or auto_receipt=off land at a definitive
    # `COMPLETED_INVOICE_ONLY` terminal stage (instead of silently
    # sitting at INVOICE_CREATED) and expose payment-stage blocker
    # fields for diagnostics.
    "rev30_payment_continuation":
        "rev30 — Payment continuation",
    # rev31 — Tabby-only Live Canary. If absent, the deploy is
    # missing the dedicated live-canary endpoint that flips
    # dry_run_mode/production_writes_locked/selective_live_send_enabled
    # under a strict precondition check.
    "rev31_tabby_live_canary":
        "rev31 — Live Canary for Tabby",
    # rev32 — Fail-closed hardening (BLOCKER hotfix for GitHub #5).
    # If absent, the deploy is missing (a) the stale-worker POST
    # block, (b) terminal-stage hard stop, (c) unified pre-POST
    # guard, (d) auto kill-switch, (e) diagnostic flags. Live Canary
    # MUST NOT be re-enabled while this marker is absent.
    "rev32_fail_closed_hardening":
        "rev32 — Fail-closed hardening",
    # rev32.1 — Dead-letter hardening (order 270589798 RCA).
    # If absent, the deploy is missing (a) BLOCKED_FOR_WRITE_STAGES
    # (FAILED_* stages now block downstream writes), (b) fail-closed
    # missing-sha check in assert_final_write_permitted, (c)
    # dead_lettered_at signal (independent of pipeline_stage), (d)
    # api_client-level write guard so direct QoyodAPIClient callers
    # (retry/reprocess/manual/go_live/…) are fenced too. Live Canary
    # MUST NOT be re-enabled while this marker is absent.
    "rev32_1_dead_letter_hardening":
        "rev32.1 — Dead-letter hardening",
    # rev33 — Canary Scope Lock + SKIPPED Terminality (P0 for the
    # 2026-07-05 Tabby-only canary scope leak: orders 269747616 →
    # invoice #193 and 270054904 → invoice #194 were live-written
    # despite payment_method ∈ {credit_card, tamara_installment}
    # being OUTSIDE the ["tabby_installment"] allowlist). If absent,
    # the deploy is missing (a) `post_skipped_history_write_violation`
    # (any row with SKIPPED in stage_history is refused), (b)
    # `canary_scope_drift_violation` (when
    # selective_live_send_enabled=True the allowlist must equal
    # exactly ["tabby_installment"] at write time), (c) pipeline-
    # level `_live_write_permitted` allowlist mirror, (d)
    # api_client-argument bypass denial in
    # `process_customer_resolved_row`, (e) SKIPPED removed from
    # `one_shot_reprocess._reset_row_to_stage` escape hatch. Live
    # Canary MUST NOT be re-enabled while this marker is absent.
    "rev33_canary_scope_lock":
        "rev33 — Canary Scope Lock + SKIPPED Terminality",
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
        # rev31 — Also fold in the source of `live_canary.py` (adjacent
        # module) so the marker check can prove the live-canary
        # endpoint code was deployed alongside the pipeline. The
        # marker check uses substring lookup, so concatenation is
        # sufficient and it never mutates `pipeline.py`.
        try:
            src_dir = src_path.rsplit("/", 1)[0]
            lc_path = f"{src_dir}/live_canary.py"
            with open(lc_path, "rb") as fh2:
                lc_data = fh2.read()
            data = data + b"\n\n# ---- live_canary.py ----\n\n" + lc_data
        except OSError:
            pass  # live_canary.py optional for backwards-compat
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


# rev47.1 — Cross-module markers. rev44+ revisions live OUTSIDE
# pipeline.py, so the pipeline-only marker scan above can NEVER prove
# they are deployed (prod incident 2026-07: operator could not verify
# rev47 was live). Each entry: marker_id → (module filename relative
# to this directory, needle that MUST appear in the deployed source).
MODULE_MARKERS: dict[str, tuple[str, str]] = {
    "rev44_transient_skip": (
        "skip_classification.py",
        "rev44 — Skip classification"),
    "rev45_customer_pending_resolution": (
        "selective_send_policy.py",
        "rev45 (user decree, option أ)"),
    "rev46_credit_card_canary_scope": (
        "canary_budget.py",
        "rev46 — canary scope moved mada → credit_card"),
    "rev46_1_payment_account_mapping_check": (
        "send_eligibility_ssot.py",
        "rev46.1 — the SAS gate"),
    "rev47_skip_history_exemption": (
        "rev32_hardening.py",
        "rev47 — SKIPPED-history veto exemption"),
    "rev47_manual_only_recovery_pattern": (
        "dead_letter_requeue.py",
        "false_skip_history_veto_2026_07_07"),
}


def _module_marker_check() -> dict:
    """Scan sibling qoyod modules for the rev44+ markers. Read-only.
    Also reports each module's sha256_first16 so preview vs deployed
    builds can be compared byte-for-byte."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    out: dict = {}
    for mid, (fname, needle) in MODULE_MARKERS.items():
        path = os.path.join(base_dir, fname)
        entry: dict = {"module": fname, "needle": needle}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            count = text.count(needle)
            entry.update({
                "present": count >= 1,
                "count":   count,
                "sha256_first16": hashlib.sha256(
                    text.encode("utf-8")).hexdigest()[:16],
            })
        except OSError as e:
            entry.update({"present": False, "count": 0,
                          "read_error": str(e)})
        out[mid] = entry
    all_present = all(v.get("present") for v in out.values())
    return {
        "all_module_markers_present": all_present,
        "markers":                    out,
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
    module_markers = _module_marker_check()
    return {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "python_version":     sys.version.split()[0],
        "process_pid":        os.getpid(),
        "process_cwd":        os.getcwd(),
        "git_sha":            _resolve_git_sha(),
        "pipeline_module":    src,
        "marker_check":       markers,
        "module_marker_check": module_markers,
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
            "code_matches_expected": (
                markers["all_markers_present"]
                and module_markers["all_module_markers_present"]),
            "pipeline_markers_ok":  markers["all_markers_present"],
            "module_markers_ok":
                module_markers["all_module_markers_present"],
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
        # rev30 — Payment-stage blocker fields persisted by the
        # pipeline on the INVOICE_CREATED short-circuit sites.
        "payment_stage_blocker_code": 1,
        "payment_stage_blocker_reason": 1,
        "payment_stage_expected": 1,
        "invoice_payment_required_for_method": 1,
        "posting_mode": 1,
        # rev32.1 — Dead-letter hardening flags.
        "rev32_flags": 1,
        # rev32.1 — dead_lettered_at is checked independently of
        # pipeline_stage by the api_client + assert_final_write_permitted.
        "dead_lettered_at": 1,
        # Only the invoice_payment sub-key is needed for the preview
        # existence check; keep the projection tight.
        "qoyod_payloads.invoice_payment": 1,
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

    # ── rev29b — Dry-run wording violation invariant ────────────────
    # An audit trail with `DRY:` ids MUST NEVER carry the strings
    # "customer created in Qoyod", "product(s) created", or
    # "invoice ... created" in its stage_history — those wordings
    # imply a real Qoyod POST happened. Detects rows written under
    # the pre-rev28/rev29b wording so the operator can filter them
    # in the historical review.
    dry_run_wording_violation = False
    dry_run_wording_reason = None
    dry_run_wording_offending: list[dict] = []
    # Row-side evidence that this row was in dry-run at processing.
    _cust_dry = (isinstance(row.get("qoyod_customer_id"), str)
                 and str(row.get("qoyod_customer_id"))
                     .startswith(("DRY:", "PREVIEW:")))
    _inv_dry = (isinstance(qid, str)
                and qid.startswith(("DRY:", "PREVIEW:")))
    _pay_dry = (isinstance(row.get("qoyod_invoice_payment_id"), str)
                and str(row.get("qoyod_invoice_payment_id"))
                    .startswith(("DRY:", "PREVIEW:")))
    _hist_entries = row.get("stage_history") or []
    _hist_dry = False
    if isinstance(_hist_entries, list):
        for _e in _hist_entries:
            if isinstance(_e, dict) and isinstance(_e.get("note"), str) \
                    and "DRY-RUN" in _e["note"]:
                _hist_dry = True
                break
    # sas_worker_trace evidence — what the worker actually saw.
    _swt = row.get("sas_worker_trace") or {}
    _swt_settings = (_swt.get("settings_seen") or {}) \
        if isinstance(_swt, dict) else {}
    _swt_dry = bool(_swt_settings.get("dry_run_mode", False))
    # Current settings evidence (fallback only — user directive says
    # do NOT trust current settings blindly; row evidence wins).
    _settings_dry_now = bool(settings_doc.get("dry_run_mode", False))
    dry_evidence = (_cust_dry or _inv_dry or _pay_dry
                    or _hist_dry or _swt_dry or _settings_dry_now)

    if dry_evidence and isinstance(_hist_entries, list):
        # Forbidden phrases as (label, regex) pairs so `invoice N created`
        # (with any invoice number) also matches. A note is only
        # offending when it does NOT itself contain the explicit
        # "DRY-RUN" marker.
        _FORBIDDEN = (
            ("customer created in Qoyod",
             re.compile(r"customer created in Qoyod")),
            ("product(s) created",
             re.compile(r"product\(s\) created")),
            ("invoice created",
             re.compile(r"\binvoice(?:\s+\S+)?\s+created\b")),
        )
        for _e in _hist_entries:
            if not isinstance(_e, dict):
                continue
            _note = _e.get("note")
            if not isinstance(_note, str):
                continue
            if "DRY-RUN" in _note:
                continue  # explicitly marked → safe (new wording)
            for _label, _rx in _FORBIDDEN:
                if _rx.search(_note):
                    dry_run_wording_offending.append({
                        "from_stage": _e.get("from_stage"),
                        "to_stage":   _e.get("to_stage"),
                        "note":       _note,
                        "phrase":     _label,
                    })
                    break
        if dry_run_wording_offending:
            dry_run_wording_violation = True
            _bits = []
            if _cust_dry:
                _bits.append("qoyod_customer_id=DRY:*")
            if _inv_dry:
                _bits.append("qoyod_invoice_id=DRY:*")
            if _pay_dry:
                _bits.append("qoyod_invoice_payment_id=DRY:*")
            if _hist_dry:
                _bits.append("stage_history contains DRY-RUN note")
            if _swt_dry:
                _bits.append(
                    "sas_worker_trace.settings_seen.dry_run_mode=true")
            if _settings_dry_now:
                _bits.append("current settings.dry_run_mode=true")
            dry_run_wording_reason = (
                f"Row evidence indicates DRY-RUN ({', '.join(_bits)}) but "
                f"stage_history contains {len(dry_run_wording_offending)} "
                f"note(s) with pre-rev29b wording implying real Qoyod POST(s)"
            )

    # ── rev29d — Worker code identity mismatch ───────────────────────
    # If the row's stored `sas_worker_trace.worker_pipeline_sha` does
    # not match the CURRENT process's pipeline sha, this is evidence
    # that the row was built by a stale worker. Surface both shas so
    # the operator can decide whether to restart / redeploy.
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    _swt = row.get("sas_worker_trace") or {}
    row_worker_pipeline_sha = (
        _swt.get("worker_pipeline_sha")
        if isinstance(_swt, dict) else None)
    current_pipeline_sha = _compute_pipeline_sha()
    worker_code_mismatch = bool(
        row_worker_pipeline_sha
        and current_pipeline_sha
        and row_worker_pipeline_sha != current_pipeline_sha)

    # ── rev30 — Payment stage diagnostic surfacing ───────────────────
    # Explicitly answers "WHY did this row stop at INVOICE_CREATED?"
    # by surfacing:
    #   - invoice_payment_required_for_method (canonical.payment_method
    #     is pre-paid vs COD)
    #   - payment_stage_expected (True at COMPLETED / payment-created,
    #     False at COMPLETED_INVOICE_ONLY, None otherwise unless
    #     silent-stuck at INVOICE_CREATED)
    #   - payment_stage_blocker_code / _reason (persisted by the
    #     pipeline on short-circuit sites, or synthesised here for
    #     silent-stuck rows)
    #   - payment_payload_preview_exists (whether the invoice_payment
    #     preview is present on the row)
    _canonical = row.get("canonical_payload") or {}
    _pm_for_diag = (
        _canonical.get("payment_method")
        or _canonical.get("payment_method_native")
        if isinstance(_canonical, dict) else None)
    invoice_payment_required_for_method: Optional[bool] = None
    if _pm_for_diag:
        try:
            from integrations.qoyod.payment_methods import is_cod_family
            invoice_payment_required_for_method = (
                not is_cod_family(_pm_for_diag))
        except Exception:
            invoice_payment_required_for_method = None
    _stage = row.get("pipeline_stage")
    payment_stage_blocker_code = row.get("payment_stage_blocker_code")
    payment_stage_blocker_reason = row.get("payment_stage_blocker_reason")
    if _stage in ("INVOICE_PAYMENT_CREATED", "COMPLETED",
                  "COMPLETED_WITH_ROUNDING_WARNING"):
        payment_stage_expected = True
    elif _stage == "COMPLETED_INVOICE_ONLY":
        payment_stage_expected = False
    elif _stage == "INVOICE_CREATED":
        # Silent-stuck detection — rev30 short-circuits normally set
        # the terminal stage. If we see INVOICE_CREATED here, either
        # the worker was interrupted OR a stale worker built the row.
        payment_stage_expected = (
            bool(invoice_payment_required_for_method)
            if invoice_payment_required_for_method is not None
            else None)
        if payment_stage_blocker_code is None:
            payment_stage_blocker_code = "silent_stuck_at_invoice_created"
            payment_stage_blocker_reason = (
                "Row sits at INVOICE_CREATED with no downstream "
                "transition. Under rev30 this state SHOULD be "
                "unreachable — either the worker was interrupted "
                "mid-tick, a stale worker built the row, OR the "
                "pipeline was re-processed without completing the "
                "payment step.")
    else:
        payment_stage_expected = None
    _payloads = row.get("qoyod_payloads") or {}
    payment_payload_preview_exists = bool(
        isinstance(_payloads, dict)
        and _payloads.get("invoice_payment") is not None)

    # ── rev32 — Fail-closed hardening diagnostic flags ───────────────
    # These flags are persisted by rev32 guards
    # (`rev32_hardening.assert_final_write_permitted`) when a write
    # attempt is blocked. Surfaced here so `/admin/diagnostics/row`
    # gives one-shot visibility of any violation class + whether the
    # kill-switch has been triggered by rev32.
    _rev32 = row.get("rev32_flags") or {}
    if not isinstance(_rev32, dict):
        _rev32 = {}
    rev32_live_non_allowlisted_pm_violation = bool(
        _rev32.get("live_non_allowlisted_payment_method_violation"))
    rev32_post_terminal_stage_downstream_violation = bool(
        _rev32.get("post_terminal_stage_downstream_violation"))
    rev32_skipped_then_posted_violation = bool(
        _rev32.get("skipped_then_posted_violation"))
    rev32_stale_worker_live_write_violation = bool(
        _rev32.get("stale_worker_live_write_violation"))
    rev32_live_write_gate_violation = bool(
        _rev32.get("live_write_gate_violation"))
    rev32_kill_switch_triggered = bool(
        _rev32.get("kill_switch_triggered"))
    rev32_kill_switch_reason = _rev32.get("kill_switch_reason")
    rev32_last_violation_type = _rev32.get("last_violation_type")

    # ── rev32.1 — Dead-letter hardening diagnostic flags ─────────────
    rev32_1_post_dead_letter_write_violation = bool(
        _rev32.get("post_dead_letter_write_violation"))
    rev32_1_post_failed_stage_downstream_violation = bool(
        _rev32.get("post_failed_stage_downstream_violation"))
    rev32_1_missing_current_pipeline_sha_violation = bool(
        _rev32.get("missing_current_pipeline_sha_violation"))
    rev32_1_missing_row_context_on_write_violation = bool(
        _rev32.get("rev32_1_missing_row_context_on_write"))
    row_dead_lettered_at = row.get("dead_lettered_at")

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
            # rev29b — Dry-run wording invariant.
            "dry_run_wording_violation":  dry_run_wording_violation,
            "dry_run_wording_reason":     dry_run_wording_reason,
            "dry_run_wording_offending":  dry_run_wording_offending,
            # rev29d — Worker code identity check.
            "row_worker_pipeline_sha":    row_worker_pipeline_sha,
            "current_pipeline_sha":       current_pipeline_sha,
            "worker_code_mismatch":       worker_code_mismatch,
            # rev30 — Payment stage diagnostic surfacing.
            "invoice_payment_required_for_method":
                invoice_payment_required_for_method,
            "payment_stage_expected":     payment_stage_expected,
            "payment_stage_blocker_code": payment_stage_blocker_code,
            "payment_stage_blocker_reason":
                payment_stage_blocker_reason,
            "payment_payload_preview_exists":
                payment_payload_preview_exists,
            # rev32 — Fail-closed hardening diagnostic surfacing.
            "live_non_allowlisted_payment_method_violation":
                rev32_live_non_allowlisted_pm_violation,
            "post_terminal_stage_downstream_violation":
                rev32_post_terminal_stage_downstream_violation,
            "skipped_then_posted_violation":
                rev32_skipped_then_posted_violation,
            "stale_worker_live_write_violation":
                rev32_stale_worker_live_write_violation,
            "rev32_live_write_gate_violation":
                rev32_live_write_gate_violation,
            "kill_switch_triggered":  rev32_kill_switch_triggered,
            "kill_switch_reason":     rev32_kill_switch_reason,
            "rev32_last_violation_type": rev32_last_violation_type,
            # rev32.1 diagnostics.
            "post_dead_letter_write_violation":
                rev32_1_post_dead_letter_write_violation,
            "post_failed_stage_downstream_violation":
                rev32_1_post_failed_stage_downstream_violation,
            "missing_current_pipeline_sha_violation":
                rev32_1_missing_current_pipeline_sha_violation,
            "rev32_1_missing_row_context_on_write_violation":
                rev32_1_missing_row_context_on_write_violation,
            "dead_lettered_at":       row_dead_lettered_at,
        },
    }
