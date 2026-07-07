"""rev43.1 — SKIPPED Forensics (READ-ONLY proof, user decree).

Question to prove from PRODUCTION data:
    Does a status-based SKIPPED (invoice_trigger_status_not_enabled /
    status_not_in_allow_list) permanently lock an order even when a
    LATER webhook arrives with completed / تم التنفيذ?

ZERO writes. ZERO Qoyod calls. No reprocess / reset / send.
"""
from __future__ import annotations

from datetime import datetime, timezone

_COMPLETED = ("completed", "تم التنفيذ")

# Reason classification (from stage_history note + gate records).
_STATUS_MARKERS = (
    "status_not_in_allow_list", "invoice_trigger_status_not_enabled",
    "status_hard_blocked", "not_eligible_status", "status_not_eligible",
    "under_review",
)
_PAYMENT_MARKERS = (
    "payment_method_not_in_allow_list", "payment_method_hard_blocked",
    "canary_scope_skip", "not_in_allowlist",
)
_PRE_ACTIVATION_MARKERS = ("pre_activation_skipped",)


def _classify(reason_text: str) -> str:
    t = (reason_text or "").lower()
    if any(m in t for m in _STATUS_MARKERS):
        return "status_not_enabled"
    if any(m in t for m in _PAYMENT_MARKERS):
        return "payment_method_scope"
    if any(m in t for m in _PRE_ACTIVATION_MARKERS):
        return "pre_activation"
    return "other"


def _skip_entry(row: dict) -> dict | None:
    for h in reversed(row.get("stage_history") or []):
        if h.get("to_stage") == "SKIPPED":
            return h
    return None


def _reason_of(row: dict, entry: dict | None) -> str:
    parts = []
    if entry and entry.get("note"):
        parts.append(str(entry["note"]))
    gate = row.get("selective_auto_send_gate") or {}
    if gate.get("reason"):
        parts.append(f"sas_gate={gate['reason']}")
    br = row.get("business_rules_decision") or {}
    if br.get("reason"):
        parts.append(f"business_rule={br['reason']}")
    if row.get("skipped_reason"):
        parts.append(f"skipped_reason={row['skipped_reason']}")
    css = row.get("canary_scope_skip") or {}
    if css.get("reason"):
        parts.append(f"canary_scope={css['reason']}")
    return " | ".join(parts) or "(no reason recorded)"


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row_summary(row: dict) -> dict:
    canon = row.get("canonical_payload") or {}
    raw = row.get("raw_payload") or {}
    entry = _skip_entry(row)
    reason = _reason_of(row, entry)
    return {
        "order_number": row.get("salla_order_number"),
        "trace_id": row.get("trace_id"),
        "old_stage": (entry or {}).get("from_stage"),
        "new_stage": "SKIPPED",
        "skipped_at": _iso((entry or {}).get("at")
                           or row.get("pipeline_finished_at")),
        "actor": (entry or {}).get("actor"),
        "reason": reason,
        "reason_class": _classify(reason),
        "salla_status_at_decision": (canon.get("order_status_native")
                                     or canon.get("order_status")),
        "salla_status_slug": canon.get("order_status"),
        "payment_method": canon.get("payment_method"),
        "order_date": canon.get("order_date"),
        "received_at": _iso(row.get("received_at")),
        "webhook_event_type": (raw.get("event") or raw.get("event_type")
                               or "order"),
    }


async def build_skipped_forensics(
    db, *, user_id: str, limit: int = 20,
) -> dict:
    limit = max(1, min(int(limit), 50))

    # ── 1. Last N transitions INTO SKIPPED (by finish time) ─────────
    proj = {"_id": 0, "salla_order_number": 1, "trace_id": 1,
            "stage_history": 1, "pipeline_stage": 1,
            "pipeline_finished_at": 1, "received_at": 1,
            "selective_auto_send_gate": 1, "business_rules_decision": 1,
            "skipped_reason": 1, "canary_scope_skip": 1,
            "canonical_payload.order_status": 1,
            "canonical_payload.order_status_native": 1,
            "canonical_payload.payment_method": 1,
            "canonical_payload.order_date": 1,
            "raw_payload.event": 1, "raw_payload.event_type": 1}
    cur = db.integration_inbox.find(
        {"user_id": user_id, "pipeline_stage": "SKIPPED"},
        proj).sort("pipeline_finished_at", -1).limit(limit)
    skipped_rows = [_row_summary(r) async for r in cur]

    reason_class_counts: dict[str, int] = {}
    for s in skipped_rows:
        reason_class_counts[s["reason_class"]] = \
            reason_class_counts.get(s["reason_class"], 0) + 1

    # ── 2+3. For status-skipped orders: did a completed webhook ─────
    # arrive LATER, and what happened to it?
    status_cases: list[dict] = []
    locked_examples: list[dict] = []
    for s in skipped_rows:
        if s["reason_class"] != "status_not_enabled":
            continue
        order = s["order_number"]
        later = db.integration_inbox.find(
            {"user_id": user_id, "salla_order_number": order,
             "$or": [
                 {"canonical_payload.order_status":
                     {"$in": list(_COMPLETED)}},
                 {"canonical_payload.order_status_native":
                     {"$in": list(_COMPLETED)}},
             ]},
            proj).sort("received_at", -1).limit(3)
        completed_rows = [r async for r in later]
        case: dict = {"skipped_row": s,
                      "completed_webhook_found": bool(completed_rows)}
        if completed_rows:
            cr = completed_rows[0]
            cr_entry = _skip_entry(cr)
            cr_reason = _reason_of(cr, cr_entry)
            case["completed_row"] = {
                "trace_id": cr.get("trace_id"),
                "received_at": _iso(cr.get("received_at")),
                "pipeline_stage": cr.get("pipeline_stage"),
                "skip_reason": (cr_reason
                                if cr.get("pipeline_stage") == "SKIPPED"
                                else None),
                "skip_reason_class": (_classify(cr_reason)
                                      if cr.get("pipeline_stage")
                                      == "SKIPPED" else None),
            }
            if cr.get("pipeline_stage") == "SKIPPED":
                case["verdict"] = "completed_webhook_also_skipped"
                locked_examples.append({
                    "order_number": order,
                    "first_skip_reason": s["reason"],
                    "first_skip_status": s["salla_status_at_decision"],
                    "completed_row_trace_id": cr.get("trace_id"),
                    "completed_row_skip_reason": cr_reason,
                    "completed_row_skip_reason_class":
                        _classify(cr_reason),
                    "proof": ("وصل webhook بحالة تم التنفيذ لاحقاً "
                              "لكن صفّه هو الآخر تحوّل SKIPPED — "
                              "الطلب مقفول نهائياً رغم اكتماله"),
                })
            elif cr.get("pipeline_stage") in (
                    "COMPLETED", "INVOICE_CREATED", "PAYMENT_RECORDED"):
                case["verdict"] = "completed_webhook_progressed"
            else:
                case["verdict"] = "completed_webhook_pending"
        else:
            case["verdict"] = "no_completed_webhook_yet"
        status_cases.append(case)

    verdict_counts: dict[str, int] = {}
    for c in status_cases:
        verdict_counts[c["verdict"]] = \
            verdict_counts.get(c["verdict"], 0) + 1

    return {
        "ok": True,
        "read_only": True,
        "no_qoyod_api_calls": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_skipped_transitions": skipped_rows,
        "reason_class_counts": reason_class_counts,
        "status_skipped_cases": status_cases,
        "status_case_verdict_counts": verdict_counts,
        "locked_despite_completed_examples": locked_examples[:5],
        "note": ("قراءة فقط — إثبات جنائي. reason_class: "
                 "status_not_enabled = تخطٍ بسبب حالة غير مفعّلة؛ "
                 "payment_method_scope = تخطٍ بسبب قائمة طرق الدفع "
                 "(SAS/كناري)؛ pre_activation = قبل تفعيل الإنتاج. "
                 "verdict=completed_webhook_also_skipped يعني الطلب "
                 "مقفول نهائياً رغم وصول حالة تم التنفيذ."),
    }
