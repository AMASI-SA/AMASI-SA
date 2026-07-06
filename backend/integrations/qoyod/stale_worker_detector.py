"""Iter-2026-02.rev36 — Stale-Worker (Zombie) Detector — READ-ONLY.

Incident 2026-07-06 (order 270954898 → real invoice 195 / payment 166
during the open canary window): a backend process running OLD code
(pre SAS-gate-persistence era) picked the row from integration_inbox
and pushed it live WITHOUT any modern guard. Its forensic signature:

    • row advanced past NORMALIZED by actor="worker"
    • sas_worker_trace.worker_pipeline_sha  → missing        (rev32)
    • selective_auto_send_gate              → not persisted   (rev24)
    • no qoyod_canary_budget reservation                     (rev35)
    • no qoyod_live_send_audit rows                          (rev35)

The CURRENT build writes the gate + worker sha unconditionally in
`process_normalized_row` (both SAS-enabled and SAS-disabled branches)
BEFORE any transition — so any recent worker-driven advancement
missing those markers proves a foreign/old worker is alive and
polling the same production database.

This module only READS. It never mutates rows, settings, or Qoyod.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# Worker-driven stages that imply the row moved toward (or into) the
# Qoyod write path. SKIPPED is excluded: several legitimate paths
# (backfill gate, canary scope skip) write SKIPPED without a worker
# trace and would false-positive.
_WRITE_PATH_STAGES = frozenset({
    "RULES_APPLIED", "CUSTOMER_RESOLVED", "PRODUCT_RESOLVED",
    "INVOICE_CREATED", "INVOICE_PAYMENT_CREATED", "RECEIPT_CREATED",
    "COMPLETED", "COMPLETED_WITH_ROUNDING_WARNING",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _current_sha() -> str:
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    return _compute_pipeline_sha()


def _worker_write_transitions(row: dict) -> list[dict]:
    out = []
    for h in (row.get("stage_history") or []):
        if not isinstance(h, dict):
            continue
        if h.get("actor") == "worker" and \
                h.get("to_stage") in _WRITE_PATH_STAGES:
            out.append(h)
    return out


async def scan_for_stale_workers(
    db, *, user_id: str, hours: int = 24, limit: int = 2000,
) -> dict:
    """READ-ONLY sweep of recent inbox rows for zombie signatures.

    Issues reported per row:
      • worker_sha_missing   — advanced by worker, NO pipeline sha
                               → pre-rev32 code wrote it (ZOMBIE).
      • worker_sha_stale     — sha present but != current build
                               → an older (rev32+) build is running.
      • sas_gate_missing     — past NORMALIZED without a persisted
                               gate record → pre-rev24 code (ZOMBIE).
    """
    cutoff = _now() - timedelta(hours=max(1, min(hours, 24 * 14)))
    current_sha = _current_sha()

    scanned = 0
    alerts: list[dict] = []
    counts = {"worker_sha_missing": 0, "worker_sha_stale": 0,
              "sas_gate_missing": 0}

    cursor = db.integration_inbox.find(
        {"user_id": user_id, "received_at": {"$gte": cutoff}},
        {"_id": 0, "id": 1, "trace_id": 1, "salla_order_number": 1,
         "pipeline_stage": 1, "received_at": 1, "stage_history": 1,
         "sas_worker_trace": 1, "selective_auto_send_gate": 1,
         "qoyod_invoice_id": 1},
    ).sort("received_at", -1).limit(max(1, min(limit, 5000)))

    async for row in cursor:
        scanned += 1
        wt = _worker_write_transitions(row)
        if not wt:
            continue    # webhook-only / held rows — not worker-driven
        issues: list[str] = []
        row_sha = ((row.get("sas_worker_trace") or {})
                   .get("worker_pipeline_sha"))
        if not row_sha:
            issues.append("worker_sha_missing")
        elif row_sha != current_sha:
            issues.append("worker_sha_stale")
        if not row.get("selective_auto_send_gate"):
            issues.append("sas_gate_missing")
        if not issues:
            continue
        for i in issues:
            counts[i] += 1
        qid = str(row.get("qoyod_invoice_id") or "")
        alerts.append({
            "trace_id":            row.get("trace_id"),
            "salla_order_number":  row.get("salla_order_number"),
            "pipeline_stage":      row.get("pipeline_stage"),
            "issues":              issues,
            "row_worker_sha":      row_sha,
            "qoyod_invoice_id":    row.get("qoyod_invoice_id"),
            "real_write_suspected": bool(
                qid and not qid.upper().startswith(("DRY:", "PREVIEW:"))),
            "last_worker_transition": {
                "to_stage": wt[-1].get("to_stage"),
                "at":       wt[-1].get("at"),
                "note":     wt[-1].get("note"),
            },
        })

    zombie = any(("worker_sha_missing" in a["issues"]
                  or "sas_gate_missing" in a["issues"]) for a in alerts)
    stale_build = any("worker_sha_stale" in a["issues"] for a in alerts)
    return {
        "ok":                       True,
        "checked_at":               _now().isoformat(),
        "window_hours":             hours,
        "current_pipeline_sha":     current_sha,
        "scanned":                  scanned,
        "counts":                   counts,
        "alerts":                   alerts[:100],
        "alerts_total":             len(alerts),
        "zombie_activity_detected": zombie,
        "stale_build_detected":     stale_build,
        "all_clear":                not alerts,
        "human_message": (
            "نظيف — لا أثر لأي عامل قديم في النافذة المفحوصة."
            if not alerts else
            "🚨 رُصد نشاط عامل بكود قديم! صفوف تقدمت بواسطة worker بدون "
            "بصمة sha أو بدون SAS gate محفوظ. لا تفتح أي نافذة إرسال "
            "حي قبل إنهاء العمليات القديمة (دعم Emergent) وتدوير مفتاح "
            "قيود."),
    }
