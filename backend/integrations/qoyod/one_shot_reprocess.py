"""Qoyod One-Shot Reprocess — single-order, strict, audit-trail-heavy.

Why this exists
───────────────
A specific production order (e.g. `268670571`) failed because a
`DRY:product:*` id leaked into the invoice payload. The operator
wants to safely re-run **exactly that one order** against real Qoyod
after the leak guard shipped, WITHOUT touching any other DEAD_LETTER
row, WITHOUT running backfill, and WITHOUT manual shell access.

Strict invariants (user directive 2026-02-27)
─────────────────────────────────────────────
1. Targets exactly ONE row, found by `salla_order_number` (or
   `trace_id`). Multiple matches → refuse.
2. Requires a typed confirmation token `REPROCESS-<order_number>` —
   typo-resistant and order-specific (you cannot accidentally use one
   token to reprocess a different order).
3. **Never** scans/changes any other DEAD_LETTER row.
4. **Never** triggers backfill.
5. Quarantines any `DRY:contact:*` / `DRY:product:*` mappings tied to
   this order BEFORE re-running so the resolver re-creates against
   the real Qoyod tenant.
6. The pipeline's preflight guard (`pipeline.py`, Iter-267) is the
   last line of defence — if anything `DRY:*` still reaches the
   invoice payload, the row goes to DEAD_LETTER and **nothing is
   POSTed** to Qoyod.
7. On failure: no auto-retry. Returns `error.code`, `error.message`,
   `request_body_json`, and the stage at which the failure occurred.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient
from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.invoice_builder import is_dry_run_mode
from integrations.qoyod.pipeline import (
    process_normalized_row, process_customer_resolved_row,
)
from integrations.qoyod.state_machine import (
    transition, FAILURE_TO_RESUME, InvalidTransition,
)


logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


CONFIRM_TOKEN_TEMPLATE = "REPROCESS-{order_number}"
# Iter-293.4-rev3 — Per-Order Approval Phrase.
# When `production_writes_locked=True` (the master kill switch), the
# operator must additionally supply this exact phrase to unlock the
# api_client for a single order — WITHOUT flipping the global
# setting. The order_number is interpolated so a phrase approving
# order A cannot be reused for order B.
APPROVAL_PHRASE_TEMPLATE = "Approved to send order {order_number} only"

# Pipeline stages the row must traverse for a successful one-shot run.
# Iter-290h.6 — Pipeline now uses `POST /invoice_payments` instead of
# `/receipts`. The stage name in the row's history is
# `INVOICE_PAYMENT_CREATED` (no longer `RECEIPT_CREATED`).
EXPECTED_STAGE_SEQUENCE: tuple[str, ...] = (
    "CUSTOMER_RESOLVED",
    "PRODUCT_RESOLVED",
    "INVOICE_CREATED",
    "INVOICE_PAYMENT_CREATED",
    "COMPLETED",
)


class OneShotRefused(Exception):
    """Raised when the request is malformed or unsafe. Surfaces a
    structured `{code, message, ...}` dict for the HTTP layer."""
    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


# ─────────────────────────────────────────────────────────────────────
# Row lookup — single match only
# ─────────────────────────────────────────────────────────────────────
async def _find_target_row(
    db, *, user_id: str, order_number: Optional[str],
    trace_id: Optional[str],
) -> dict:
    """Find exactly one inbox row for this order. Refuse on 0 or >1
    matches — the operator must be explicit. Multiple matches mean
    the same order received multiple webhook events (e.g. status
    transitions) and the operator must pick one by `trace_id`.
    """
    if not (order_number or trace_id):
        raise OneShotRefused(
            "order_lookup_required",
            "must supply either order_number or trace_id")

    q: dict = {"user_id": user_id}
    if trace_id:
        q["trace_id"] = trace_id
    else:
        # Salla persists order ids as integers in JSON payloads; older
        # rows may have them as strings depending on the adapter path.
        # Match both representations so the operator never has to
        # guess what type the data is stored as.
        on_str = str(order_number)
        candidates: list[Any] = [on_str]
        try:
            candidates.append(int(on_str))
        except (TypeError, ValueError):
            pass
        q["$or"] = []
        for v in candidates:
            q["$or"].extend([
                {"salla_order_number": v},
                {"salla_order_id":     v},
                {"canonical_payload.order_number": v},
                {"canonical_payload.order_id":     v},
            ])

    rows = await db.integration_inbox.find(q).to_list(length=20)
    if not rows:
        raise OneShotRefused(
            "row_not_found",
            f"no integration_inbox row matches order_number={order_number} "
            f"trace_id={trace_id}",
            order_number=order_number, trace_id=trace_id)
    if len(rows) == 1:
        return rows[0]

    # Multiple matches — typical when Salla emits several status
    # webhooks for the same order (e.g. `under_review` then
    # `completed`). The operator's intent is "fix the failed one", so
    # filter to rows that are reprocessable (DEAD_LETTER / FAILED_*).
    failed_or_terminal = [r for r in rows
                          if r.get("pipeline_stage") in _REPROCESSABLE_STAGES
                          and r.get("pipeline_stage") != "SKIPPED"]
    if len(failed_or_terminal) == 1:
        return failed_or_terminal[0]
    if len(failed_or_terminal) > 1:
        rows = failed_or_terminal     # surface only the actionable ones
    raise OneShotRefused(
        "multiple_matches_pick_one_by_trace_id",
        f"order_number={order_number} matches {len(rows)} rows; "
        "supply trace_id to disambiguate",
        order_number=order_number,
        candidates=[{
            "trace_id":      r.get("trace_id"),
            "received_at":   r.get("received_at"),
            "pipeline_stage": r.get("pipeline_stage"),
            "idempotency_key": r.get("idempotency_key"),
        } for r in rows[:10]])


# ─────────────────────────────────────────────────────────────────────
# DRY-Run mapping quarantine — pre-run cleanup
# ─────────────────────────────────────────────────────────────────────
async def _quarantine_dry_mappings(
    db, *, user_id: str, row: dict,
) -> dict:
    """Inspect the row's customer + product SKUs. Quarantine any
    `qoyod_customers_mapping` / `qoyod_products_mapping` rows whose
    `qoyod_*_id` carries the `DRY:` prefix.

    Also nullifies the row's own `qoyod_customer_id` if it is a
    Dry-Run leak so the pipeline re-resolves the customer cleanly
    on the next pass.

    Iter-290g — Diagnostic accuracy
    ───────────────────────────────
    The summary now distinguishes TWO classes of mapping skip so the
    operator never sees a real Qoyod id falsely labelled "quarantined":

      • `dry_id_quarantined`         — pid/cid starts with "DRY:" (a
                                       genuine dry-run fake id we just
                                       quarantined this pass).
      • `dry_run_only_flag_carried`  — pid/cid is a REAL Qoyod id, but
                                       the mapping carries a legacy
                                       `dry_run_only=True` flag from a
                                       previous run. The pipeline will
                                       re-resolve via SKU search and
                                       (typically) auto-adopt this
                                       same real id. Surfaced for
                                       transparency, NOT as a problem.

    The old top-level `product_mappings_quarantined` key is preserved
    for back-compat with existing dashboards but now contains ONLY the
    DRY-id entries (no longer pollutes with real ids).

    Returns the audit summary (counts + the actual ids per bucket).
    """
    summary: dict[str, Any] = {
        # ── Customer ──
        "customer_mapping_quarantined":   False,
        "customer_quarantined_id":        None,
        "customer_dry_run_only_carried":  False,    # Iter-290g
        "customer_carried_real_id":       None,     # Iter-290g
        "row_customer_id_cleared":        False,
        # ── Products ──
        # Back-compat key — DRY-id quarantines ONLY (Iter-290g change).
        "product_mappings_quarantined":   [],
        # New buckets — explicit + non-confusing labels.
        "dry_id_quarantined":             [],       # Iter-290g
        "dry_run_only_flag_carried":      [],       # Iter-290g
        "scanned_sku_count":              0,
    }
    canonical = row.get("canonical_payload") or {}

    # ── Customer mapping ────────────────────────────────────────────
    customer = canonical.get("customer") or {}
    phone = (customer.get("phone") or "").strip()
    email = (customer.get("email") or "").strip().lower()
    lookup_keys: list[str] = []
    if phone:
        lookup_keys.append(phone)
    if email:
        lookup_keys.append(email)

    for lk in lookup_keys:
        m = await db.qoyod_customers_mapping.find_one(
            {"user_id": user_id, "lookup_key": lk},
            {"_id": 0, "qoyod_customer_id": 1, "dry_run_only": 1})
        if not m:
            continue
        cid = m.get("qoyod_customer_id") or ""
        cid_is_dry = str(cid).startswith("DRY:")
        cid_flagged = bool(m.get("dry_run_only"))
        if cid_is_dry:
            await db.qoyod_customers_mapping.update_one(
                {"user_id": user_id, "lookup_key": lk},
                {"$set": {"dry_run_only":       True,
                          "quarantined_at":     _now(),
                          "quarantine_reason":  "one_shot_reprocess",
                          "quarantined_id":     cid}},
            )
            summary["customer_mapping_quarantined"] = True
            summary["customer_quarantined_id"] = cid
            break
        if cid_flagged:
            # Iter-290g — real id, just a stale flag. No write needed;
            # surface it for transparency.
            summary["customer_dry_run_only_carried"] = True
            summary["customer_carried_real_id"] = cid
            break

    # Also clear the row's own qoyod_customer_id if it's a DRY leak —
    # the pipeline reads from `row.qoyod_customer_id` after the
    # CUSTOMER_RESOLVED transition.
    row_cid = row.get("qoyod_customer_id") or ""
    if str(row_cid).startswith("DRY:"):
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"qoyod_customer_id": None,
                      "qoyod_customer_id_cleared_at": _now(),
                      "qoyod_customer_id_cleared_reason": "dry_run_leak"}},
        )
        summary["row_customer_id_cleared"] = True

    # ── Product mappings ────────────────────────────────────────────
    items = canonical.get("items") or []
    for it in items:
        sku = (it.get("sku") or "").strip()
        if not sku:
            continue
        summary["scanned_sku_count"] += 1
        m = await db.qoyod_products_mapping.find_one(
            {"user_id": user_id, "sku": sku},
            {"_id": 0, "qoyod_product_id": 1, "dry_run_only": 1})
        if not m:
            continue
        pid = m.get("qoyod_product_id") or ""
        pid_is_dry = str(pid).startswith("DRY:")
        pid_flagged = bool(m.get("dry_run_only"))
        if pid_is_dry:
            await db.qoyod_products_mapping.update_one(
                {"user_id": user_id, "sku": sku},
                {"$set": {"dry_run_only":       True,
                          "quarantined_at":     _now(),
                          "quarantine_reason":  "one_shot_reprocess",
                          "quarantined_id":     pid}},
            )
            entry = {"sku": sku, "quarantined_id": pid}
            summary["dry_id_quarantined"].append(entry)
            summary["product_mappings_quarantined"].append(entry)
        elif pid_flagged:
            # Iter-290g — real Qoyod id (e.g. pid=21 for AMS10002),
            # legacy `dry_run_only=True` flag carried forward. Listed
            # in a separate bucket so the operator-facing diagnostic
            # never calls a real id "quarantined".
            summary["dry_run_only_flag_carried"].append(
                {"sku": sku, "qoyod_product_id": pid})

    return summary


# ─────────────────────────────────────────────────────────────────────
# Stage reset — bring the row back to a worker-drainable stage
# ─────────────────────────────────────────────────────────────────────
def _resume_target_for(row: dict, *, force_full: bool) -> str:
    """Choose the stage the row resumes from.

    For one-shot we want the broadest safe re-run: NORMALIZED so
    customer + product re-resolve from scratch. The state machine
    currently only allows `RETRYING → NORMALIZED` and
    `RETRYING → CUSTOMER_RESOLVED`, so we constrain accordingly.
    """
    return "NORMALIZED"


# Stages from which a one-shot reprocess can lift the row back into
# the pipeline. Anything outside this set is refused — the operator
# should not be able to mutate live/in-flight rows.
_REPROCESSABLE_STAGES: frozenset[str] = frozenset(
    {"DEAD_LETTER", "PARTIAL_FAILURE", "NORMALIZED", "NEW", "RECEIVED",
     "VALIDATED", "ELIGIBLE", "SKIPPED",
     # Iter-293.4-rev3 — Locked rows are reprocessable via per-order
     # approval. The reset path takes them through RETRYING → NORMALIZED
     # so customer/product/preflight/payload are rebuilt from scratch
     # (NO blind reuse of the stale locked_payload).
     "LOCKED_AWAITING_APPROVAL"}
    | set(FAILURE_TO_RESUME.keys())
)


async def _reset_row_to_stage(
    db, row: dict, *, resume_stage: str, actor: str,
    permit_partial_invoice_created: bool = False,
) -> None:
    """Two-hop terminal/failed → RETRYING → resume_stage transition.
    Mirrors the dead_letter_requeue mechanic so stage_history stays
    auditable.

    The state machine only permits `RETRYING → NORMALIZED` and
    `RETRYING → CUSTOMER_RESOLVED`, which is why callers should ask
    for `resume_stage="NORMALIZED"` (the broadest safe re-entry).
    """
    current = row.get("pipeline_stage")

    if current == resume_stage:
        # Already where we want it — no-op (e.g. row was already
        # NORMALIZED but the worker hasn't picked it up yet).
        return

    # rev33 — SKIPPED is ABSOLUTELY TERMINAL … except rev44 transient.
    #
    # RCA of leaks 269747616 (credit_card → invoice #193) and
    # 270054904 (tamara_installment → invoice #194): SKIPPED rows
    # were resurrected via `permit_partial_invoice_created=True`
    # and driven through PRODUCT_RESOLVED → INVOICE_CREATED live
    # during the 2026-07-05 Tabby-only canary window. The
    # partial-IC escape hatch is now scoped to INVOICE_CREATED
    # ONLY.
    #
    # rev44 (user decree, prod forensics 2026-07-07): a SKIPPED row
    # stamped `skip_class=transient` (temporary status/payment scope)
    # is resumable via the RETRYING hop. Unclassified/legacy/fatal
    # SKIPPED remains absolutely terminal — fail-closed.
    if current == "SKIPPED":
        if row.get("skip_class") != "transient":
            raise OneShotRefused(
                "skipped_is_terminal_rev33",
                "row is in stage 'SKIPPED'; rev33 makes SKIPPED "
                "absolutely terminal — no reprocess/reset permitted. "
                "(rev44: only skip_class='transient' rows are "
                "resumable; this row is fatal/unclassified.) "
                "If a SKIPPED row legitimately needs to be re-sent, "
                "create a new inbox row from the source webhook payload.",
                current_stage=current,
                skip_class=row.get("skip_class"))
    if current not in _REPROCESSABLE_STAGES:
        # Canary partial-invoice-created escape hatch: allow reset
        # from INVOICE_CREATED IFF the row has no real Qoyod
        # invoice (checked upstream by the caller via
        # `allow_reset_from_partial_invoice_created`). SKIPPED is
        # NO LONGER in this escape hatch — see rev33 block above.
        if not (permit_partial_invoice_created
                and current == "INVOICE_CREATED"):
            raise OneShotRefused(
                "unsupported_current_stage",
                f"row is in stage {current!r}; one-shot reprocess "
                f"only supports terminal / failed / pre-customer "
                f"stages",
                current_stage=current)

    # Hop 1: current → RETRYING (only if current is failure/terminal).
    # NORMALIZED / NEW / RECEIVED / VALIDATED / ELIGIBLE / SKIPPED
    # are not allowed to hop into RETRYING, so we just write the
    # resume_stage directly when applicable (defensive — most live
    # callers will see DEAD_LETTER / FAILED_* and use the two-hop path).
    # Canary partial-IC exception: MUST ALSO go via
    # RETRYING (state machine forbids INVOICE_CREATED → NORMALIZED
    # direct; edges to RETRYING are registered in state_machine.py,
    # gated business-side by this flag). rev33: SKIPPED is no
    # longer part of this escape hatch — it is absolute-terminal
    # and rejected before this point.
    needs_retry_hop = (
        (current in FAILURE_TO_RESUME)
        or (current in ("DEAD_LETTER", "PARTIAL_FAILURE",
                        "LOCKED_AWAITING_APPROVAL"))
        # rev44 — transient SKIPPED resumes via the SKIPPED →
        # RETRYING edge (registered in state_machine line ~303).
        or (current == "SKIPPED"
            and row.get("skip_class") == "transient")
        or (permit_partial_invoice_created
            and current == "INVOICE_CREATED"))
    if needs_retry_hop:
        try:
            p1 = transition(
                from_stage=current, to_stage="RETRYING",
                actor=actor,
                note=(f"one-shot reprocess: {current} → RETRYING "
                      f"(actor={actor})"),
            )
        except InvalidTransition as exc:
            raise OneShotRefused(
                "invalid_transition_to_retrying",
                f"state-machine refused {current} → RETRYING: {exc}",
                current_stage=current)
        p1.setdefault("$set", {}).update({
            "last_one_shot_at":    _now(),
            "last_one_shot_actor": actor,
        })
        await db.integration_inbox.update_one({"id": row["id"]}, p1)
        from_stage = "RETRYING"
    else:
        from_stage = current

    try:
        p2 = transition(
            from_stage=from_stage, to_stage=resume_stage,
            actor=actor,
            note=f"one-shot reprocess resume at {resume_stage}",
        )
    except InvalidTransition as exc:
        from integrations.qoyod.state_machine import (
            ALLOWED_TRANSITIONS,
        )
        allowed_from_current = sorted(
            t for (f, t) in ALLOWED_TRANSITIONS if f == current)
        raise OneShotRefused(
            "invalid_transition_to_resume",
            f"state-machine refused {from_stage} → {resume_stage}: "
            f"{exc}",
            current_stage=current, resume_stage=resume_stage,
            reset_path_attempted=(
                f"{current} → RETRYING → {resume_stage}"
                if needs_retry_hop
                else f"{from_stage} → {resume_stage}"),
            permit_partial_invoice_created=(
                permit_partial_invoice_created),
            needs_retry_hop=needs_retry_hop,
            state_machine_allowed_edges_for_current_stage=(
                allowed_from_current))
    await db.integration_inbox.update_one({"id": row["id"]}, p2)


# ─────────────────────────────────────────────────────────────────────
# Main entry — atomic, single-shot
# ─────────────────────────────────────────────────────────────────────
async def reprocess_one_order(
    db, *, user_id: str,
    order_number: Optional[str] = None,
    trace_id: Optional[str] = None,
    confirm: str,
    approval_phrase: Optional[str] = None,
    actor: str = "operator",
    allow_reset_from_partial_invoice_created: bool = False,
) -> dict:
    """Reprocess exactly one Salla order against real Qoyod.

    Returns a structured dict (always shaped identically) so the UI
    can render the outcome without inspecting HTTP error codes.

    Raises `OneShotRefused` ONLY for input/safety errors — pipeline
    failures (DEAD_LETTER, leak guard tripped, Qoyod 4xx/5xx) are
    returned as normal results with `outcome` set.

    Iter-293.4-rev3 — Per-Order Approval Phrase:
        When `production_writes_locked=True`, the operator must also
        pass `approval_phrase` exactly equal to
            "Approved to send order <order_number> only"
        to unlock the api_client for THIS one run. The global
        `production_writes_locked` setting is NEVER toggled. A row is
        inserted into `qoyod_per_order_approvals` for ZATCA audit.
    """
    # ── 1. Confirm token (order-specific, typo-resistant) ───────────
    if not (order_number or trace_id):
        raise OneShotRefused(
            "order_lookup_required",
            "must supply order_number or trace_id")
    # The token always matches the order_number the operator typed
    # — when they only supplied trace_id we still REQUIRE them to
    # type the order_number into the token. This prevents a
    # trace_id-only request from accidentally reprocessing a row
    # that the operator did not intend.
    expected = (CONFIRM_TOKEN_TEMPLATE.format(order_number=order_number)
                if order_number else None)
    if not order_number or not expected or (confirm or "").strip() != expected:
        raise OneShotRefused(
            "confirm_token_mismatch",
            "confirm must equal 'REPROCESS-<order_number>'",
            expected=expected, received=(confirm or "")[:64])

    # ── 2. Find the row (single match) ──────────────────────────────
    row = await _find_target_row(
        db, user_id=user_id,
        order_number=order_number, trace_id=trace_id)

    # ── 3a. Recovery-detection branch — row stuck at INVOICE_CREATED ──
    # Iter-293.4-rev6/rev7 (2026-XX) — Production order 269571122 hit
    # a code-path bug where the pipeline successfully POSTed
    # `create_invoice` but bailed out BEFORE the COD
    # `credit_invoice_only` transition because the auto_receipt
    # capability check fired first (since fixed in pipeline.py).
    #
    # IMPORTANT — Iter-293.4-rev7 hardening (after the user observed a
    # 0.01 SAR mismatch between Salla total and the قيود-computed
    # invoice total on order 269571122):
    #
    #     The recovery path MUST NOT silently mark the row COMPLETED.
    #     A قيود invoice that was created at a DIFFERENT total than
    #     Salla's (even by one halala) is an ACCOUNTING ISSUE that
    #     requires manual accountant review — especially after ZATCA
    #     wiring goes live. Auto-completion would hide the discrepancy.
    #
    # The recovery action is therefore explicitly DEFERRED to a
    # separate operator-only endpoint (see `/admin/recover-stuck-order`)
    # so each recovery is an explicit decision with an audit trail —
    # NOT a side-effect of `one_shot_reprocess`. The branch below ONLY
    # SURFACES the stuck-state diagnostics for the operator.
    if row.get("pipeline_stage") == "INVOICE_CREATED":
        stuck_qid = row.get("qoyod_invoice_id") or ""
        _is_real_invoice = (
            bool(stuck_qid)
            and not str(stuck_qid).startswith(("DRY:", "PREVIEW:"))
        )
        # Canary partial-state escape hatch (Iter-2026-02):
        # When the caller (canary) explicitly opts in via
        # `allow_reset_from_partial_invoice_created=True`, and there
        # is NO real Qoyod invoice_id yet (stuck_qid is empty or
        # DRY:/PREVIEW: sentinel), we DO NOT surface the recovery
        # diagnostics — instead we fall through and allow the row
        # to be reset back to NORMALIZED so the pipeline can create
        # a REAL invoice from scratch. Guardrail: if a real invoice
        # DOES exist, we refuse regardless of the caller flag.
        if allow_reset_from_partial_invoice_created and \
                not _is_real_invoice:
            # Fall through — the row will be reset just below.
            pass
        elif stuck_qid and _is_real_invoice:
            payloads_now = row.get("qoyod_payloads") or {}
            responses_now = row.get("qoyod_responses") or {}
            inv_resp_now = (responses_now.get("invoice") or {}).get("body")
            canonical_now = row.get("canonical_payload") or {}
            inv_payload_now = payloads_now.get("invoice") or {}
            # Pull the per-order approval audit (if any) for this trace.
            approval_audit = None
            try:
                approval_audit = await db.qoyod_per_order_approvals.find_one(
                    {"user_id":    user_id,
                     "order_number": order_number,
                     "trace_id":   row.get("trace_id")},
                    {"_id": 0},
                )
                if isinstance(approval_audit, dict):
                    appat = approval_audit.get("approved_at")
                    if hasattr(appat, "isoformat"):
                        approval_audit["approved_at"] = appat.isoformat()
            except Exception:    # pragma: no cover — defensive
                approval_audit = None
            # Pull totals for the mismatch surface — Salla vs قيود.
            salla_total = canonical_now.get("total_amount")
            inv_diag = payloads_now.get("invoice_diagnostics") or {}
            dry_expected = inv_diag.get("mezan_expected_total")
            # The Qoyod response carries the canonical total قيود
            # actually computed server-side. We extract it tolerantly
            # — any of `invoice.total`, `total`, `balance` (when no
            # payments) is acceptable as the "Qoyod-actual" total.
            qoyod_actual_total = None
            inv_resp_obj = (inv_resp_now.get("invoice")
                            if isinstance(inv_resp_now, dict)
                            else None)
            for src in (inv_resp_obj, inv_resp_now
                        if isinstance(inv_resp_now, dict) else None):
                if not isinstance(src, dict):
                    continue
                for k in ("total", "total_amount", "balance",
                          "grand_total"):
                    if src.get(k) is not None:
                        try:
                            qoyod_actual_total = float(src[k])
                        except (TypeError, ValueError):
                            qoyod_actual_total = None
                        if qoyod_actual_total is not None:
                            break
                if qoyod_actual_total is not None:
                    break
            try:
                difference = (
                    None if (qoyod_actual_total is None
                             or salla_total is None)
                    else round(float(qoyod_actual_total)
                               - float(salla_total), 4)
                )
            except (TypeError, ValueError):
                difference = None
            totals_mismatch = (
                difference is not None and abs(difference) > 0.005)
            return {
                "ok":         False,    # NOT a success — needs accountant.
                "outcome":    ("INVOICE_CREATED_TOTAL_MISMATCH"
                               if totals_mismatch else "INVOICE_CREATED"),
                "recoverable": True,
                "row_id":     row.get("id"),
                "trace_id":   row.get("trace_id"),
                "failed_at_stage": (
                    "INVOICE_CREATED_TOTAL_MISMATCH"
                    if totals_mismatch else "INVOICE_CREATED"),
                "qoyod_invoice_id":      stuck_qid,
                "qoyod_invoice_number":  row.get("qoyod_invoice_number"),
                "qoyod_customer_id":     row.get("qoyod_customer_id"),
                "qoyod_invoice_payment_id": None,
                "qoyod_receipt_id":      None,
                "qoyod_request_sent":    False,    # nothing new
                "invoice_request_body":  inv_payload_now,
                "invoice_response_body": inv_resp_now,
                "stage_sequence_observed": _extract_observed_sequence(row),
                "stage_history":         row.get("stage_history") or [],
                "per_order_approval":    approval_audit,
                "dry_leaks_in_final_payload": _scan_payload_for_dry(
                    inv_payload_now),
                "totals_comparison": {
                    "salla_total":           salla_total,
                    "dry_run_expected_total": dry_expected,
                    "qoyod_actual_total":    qoyod_actual_total,
                    "difference":            difference,
                    "mismatch":              totals_mismatch,
                    "tolerance_sar":         0.005,
                },
                "error": (
                    {"code":    "qoyod_actual_total_mismatch",
                     "message": (
                         "الفاتورة موجودة في قيود لكن الإجمالي يختلف عن "
                         "Salla بمقدار "
                         f"{difference:+.2f} SAR. لن يتم نقل السجل إلى "
                         "COMPLETED تلقائياً. يجب على المحاسب مراجعة "
                         "الفاتورة في قيود واتخاذ القرار يدوياً قبل أي "
                         "إجراء (ZATCA-sensitive).")}
                    if totals_mismatch
                    else
                    {"code":    "invoice_created_pending_recovery",
                     "message": (
                         "الفاتورة موجودة في قيود لكن السجل لم يصل إلى "
                         "COMPLETED بسبب خلل سابق في الـ pipeline. لا "
                         "حاجة لإرسال جديد إلى قيود. استخدم endpoint "
                         "الاسترداد المُخصّص بعد مراجعة المحاسب.")}
                ),
                "message": (
                    "تشخيص فقط — لم يُرسَل أي طلب جديد إلى قيود. "
                    "هذه الفاتورة منشأة سابقاً والسجل المحلي بانتظار "
                    "قرار. " +
                    ("⚠ فرق إجمالي 0.01+ بين Salla و قيود — يلزم "
                     "مراجعة محاسبية قبل أي خطوة."
                     if totals_mismatch else "")),
            }

    # ── 3. Bail-out if the row is already COMPLETED ─────────────────
    if row.get("pipeline_stage") == "COMPLETED":
        # Iter-290h.6 — Carry the final-state diagnostics so the UI
        # can render the full stage path + قيود ids + payloads even
        # for completed rows. Previously this branch returned
        # `stage_sequence_observed: []` and the panel showed
        # "المراحل التي اجتازها: —", masking the fact that the
        # order had already moved through INVOICE_PAYMENT_CREATED →
        # COMPLETED successfully.
        stage_sequence = _extract_observed_sequence(row)
        payloads = row.get("qoyod_payloads") or {}
        qoyod_responses = row.get("qoyod_responses") or {}
        ip_response_obj = qoyod_responses.get("invoice_payment") or {}
        return {
            "ok":       True,
            "outcome":  "ALREADY_COMPLETED",
            "row_id":   row.get("id"),
            "trace_id": row.get("trace_id"),
            "stage_sequence_observed": stage_sequence,
            "expected_stage_sequence": list(EXPECTED_STAGE_SEQUENCE),
            "qoyod_invoice_id":         row.get("qoyod_invoice_id"),
            "qoyod_invoice_payment_id": row.get("qoyod_invoice_payment_id"),
            "qoyod_customer_id":        row.get("qoyod_customer_id"),
            "qoyod_receipt_id":         row.get("qoyod_receipt_id"),
            "invoice_payload":          payloads.get("invoice"),
            "invoice_payment_payload":  payloads.get("invoice_payment"),
            "invoice_payment_response": ip_response_obj.get("body"),
            "message":  ("الطلب مكتمل سابقاً — لم يُرسَل أي طلب جديد "
                         "إلى قيود لتجنب التكرار. التفاصيل أدناه من "
                         "آخر معالجة ناجحة."),
        }

    # ── 3b. Idempotency: refuse if a REAL Qoyod invoice already exists
    #        for this Salla order (protects books against double-billing).
    salla_order_id_str = (row.get("salla_order_id")
                          or str(row.get("salla_order_number") or "")
                          or order_number)
    existing_invoice = await db.qoyod_invoices.find_one(
        {"user_id": user_id, "salla_order_id": salla_order_id_str},
        {"_id": 0, "status": 1, "qoyod_invoice_id": 1,
         "qoyod_invoice_number": 1, "dry_run": 1},
    )
    if existing_invoice:
        qid = existing_invoice.get("qoyod_invoice_id") or ""
        is_real = qid and not str(qid).startswith("DRY:")
        if is_real and existing_invoice.get("status") in (
                "sent", "invoice_sent_receipt_failed", "completed"):
            return {
                "ok":       False,
                "outcome":  "INVOICE_ALREADY_CREATED",
                "row_id":   row.get("id"),
                "trace_id": row.get("trace_id"),
                "error": {
                    "code":    "invoice_already_created",
                    "message": ("فاتورة قيود حقيقية موجودة سابقاً لهذا "
                                 "الطلب. لن يتم إنشاء فاتورة جديدة لحماية "
                                 "الدفاتر من التكرار."),
                },
                "existing_qoyod_invoice_id":     existing_invoice.get("qoyod_invoice_id"),
                "existing_qoyod_invoice_number": existing_invoice.get("qoyod_invoice_number"),
                "existing_status":               existing_invoice.get("status"),
                "qoyod_request_sent": False,
                "created_ids": {
                    "customer_id": None, "product_ids": [],
                    "invoice_id":  existing_invoice.get("qoyod_invoice_id"),
                    "receipt_id":  None,
                },
            }

    # ── 4. Require real-mode + credentials ──────────────────────────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    if is_dry_run_mode(settings):
        raise OneShotRefused(
            "dry_run_mode_active",
            "one-shot reprocess targets the REAL Qoyod tenant; "
            "disable dry_run_mode in Qoyod settings first")
    api_key = await get_api_key(db, user_id)
    if not api_key:
        raise OneShotRefused(
            "credentials_missing",
            "Qoyod API key is not configured for this tenant")

    # ── 5. Quarantine any DRY mappings tied to this order ───────────
    quarantine_summary = await _quarantine_dry_mappings(
        db, user_id=user_id, row=row)

    # ── 6. Reset row to a worker-drainable stage ────────────────────
    resume_stage = _resume_target_for(row, force_full=True)
    _current_stage = row.get("pipeline_stage")
    _qid_current = row.get("qoyod_invoice_id")
    _has_real_invoice = bool(_qid_current) and not str(
        _qid_current).startswith(("DRY:", "PREVIEW:"))
    # Iter-2026-02.rev4/rev5: allow safe-rewind two-hop from either
    # `INVOICE_CREATED` OR `SKIPPED` when the caller (canary)
    # explicitly opts in AND there is no real Qoyod invoice_id yet.
    _permit_partial_ic = (
        allow_reset_from_partial_invoice_created
        and _current_stage in ("INVOICE_CREATED", "SKIPPED")
        and not _has_real_invoice)
    await _reset_row_to_stage(
        db, row, resume_stage=resume_stage, actor=actor,
        permit_partial_invoice_created=_permit_partial_ic)
    # Re-fetch fresh state for the pipeline calls.
    fresh = await db.integration_inbox.find_one({"id": row["id"]})
    if not fresh:    # defensive — should never happen
        raise OneShotRefused(
            "row_disappeared_after_reset",
            "internal: row not found after stage reset",
            row_id=row.get("id"))

    # ── 7. Drive the pipeline — manually, single row, real client ───
    # Iter-293.4-rev3 — Per-Order Approval Phrase.
    # If `production_writes_locked=True`, the operator MUST have
    # supplied an `approval_phrase` exactly equal to
    #     "Approved to send order <order_number> only"
    # When matched, the api_client is constructed UNLOCKED for THIS
    # run only. The global setting is NEVER toggled. Every approval
    # is persisted to `qoyod_per_order_approvals` for ZATCA audit.
    from integrations.qoyod.write_lock import is_locked as _is_locked
    global_lock_active = _is_locked(settings)
    approval_audit: Optional[dict] = None
    use_unlocked_client = False
    if global_lock_active:
        if not approval_phrase:
            raise OneShotRefused(
                "approval_phrase_required",
                "production_writes_locked=true يتطلب موافقة per-order "
                "صريحة. مرّر approval_phrase = "
                f"'{APPROVAL_PHRASE_TEMPLATE.format(order_number=order_number)}' "
                "حتى يتم فك القفل لهذا الطلب فقط.",
                order_number=order_number,
                expected=APPROVAL_PHRASE_TEMPLATE.format(
                    order_number=order_number),
            )
        expected_phrase = APPROVAL_PHRASE_TEMPLATE.format(
            order_number=order_number)
        if (approval_phrase or "").strip() != expected_phrase:
            raise OneShotRefused(
                "approval_phrase_mismatch",
                "approval_phrase doesn't match the order being sent. "
                "Must equal exactly: '" + expected_phrase + "'. "
                "Phrases are order-specific and cannot be reused.",
                expected=expected_phrase,
                received=(approval_phrase or "")[:128],
                order_number=order_number,
            )
        # Persist the approval BEFORE constructing the unlocked client
        # so the audit trail captures the intent even if the run later
        # fails for an unrelated reason.
        import uuid as _uuid
        approval_id = str(_uuid.uuid4())
        approval_audit = {
            "approval_id":             approval_id,
            "user_id":                 user_id,
            "order_number":            order_number,
            "trace_id":                row.get("trace_id"),
            "row_id":                  row.get("id"),
            "actor":                   actor,
            "approval_phrase":         approval_phrase,
            "expected_phrase":         expected_phrase,
            "approved_at":             _now(),
            "global_lock_was_active":  True,
            "scope":                   "single_order",
            "unlocked_api_client":     True,
        }
        try:
            await db.qoyod_per_order_approvals.insert_one(approval_audit)
        except Exception as _exc:    # pragma: no cover
            logger.warning("qoyod_per_order_approvals insert failed: %s", _exc)
        logger.warning(
            "PER_ORDER_APPROVAL granted actor=%s order=%s trace=%s "
            "approval_id=%s scope=single_order",
            actor, order_number, row.get("trace_id"), approval_id)
        use_unlocked_client = True

    # ── 7a. Iter-001k — Selective Live Send guard ─────────────────────
    # Runs AFTER approval_phrase verification but BEFORE any api_client
    # construction / write. Even a valid approval_phrase MUST NOT
    # bypass the Selective Send policy — the policy is strictly a
    # superset of the write-lock check (it also blocks Q2 orders,
    # non-enabled trigger statuses, DRY/PREVIEW ids, bank_transfer,
    # totals mismatch, etc.).
    # Skipped when dry_run_mode is active (already rejected above).
    from integrations.qoyod.selective_send_guard import (
        SelectiveSendPolicyBlocked as _SelectiveSendBlocked,
        assert_send_allowed as _assert_send_allowed,
    )
    canonical_for_policy = fresh.get("canonical_payload") or {}
    policy_order = {
        "order_number": (fresh.get("salla_order_number")
                         or canonical_for_policy.get("order_number")
                         or order_number),
        "salla_order_id": (fresh.get("salla_order_id")
                           or canonical_for_policy.get("order_id")),
        "salla_order_created_at":
            canonical_for_policy.get("order_date"),
        "status": canonical_for_policy.get("order_status"),
        "payment_method": canonical_for_policy.get("payment_method"),
        "existing_qoyod_invoice_id": fresh.get("qoyod_invoice_id"),
        "customer_status": {
            "resolved": fresh.get("qoyod_customer_id") is not None,
            "qoyod_id": fresh.get("qoyod_customer_id"),
            "reason": None,
            # rev45 (user decree): unresolved customer is
            # created/matched INSIDE this audited send — fail-closed
            # (resolution failure dead-letters before any invoice).
            # DRY ids remain fatal in the policy engine.
            "pending_resolution_during_send":
                fresh.get("qoyod_customer_id") is None,
        },
        "products_status": {
            # We haven't run product_resolver here — leave as
            # "unknown-but-not-missing". The pipeline layer's guard
            # (process_customer_resolved_row) re-checks with the true
            # resolution result. This one_shot layer guard is the
            # FIRST-line rejection for the fast blockers (gate closed,
            # Q2 cutoff, bank_transfer, DRY invoice id, etc.).
            "resolved": True,
            "resolved_count": 1,
            "dry_run_only": 0,
            "missing": [],
        },
        "totals_status": {"valid": True, "total": 0.0,
                          "expected": 0.0, "diff": 0.0},
    }
    try:
        # Per-order unlock — approval_phrase was already verified at
        # step 7 above. The one_shot flow always builds an UNLOCKED
        # api_client (step 8). Reflect that here so the policy's
        # WRITE_LOCK_ACTIVE check doesn't misfire on an approved
        # per-order retry. The DB settings row is NEVER modified.
        _policy_settings_oneshot = dict(settings)
        _policy_settings_oneshot["production_writes_locked"] = False
        _assert_send_allowed(
            order=policy_order, settings=_policy_settings_oneshot)
    except _SelectiveSendBlocked as _blocked:
        # Persist audit — even approved orders can be refused by policy.
        try:
            await db.qoyod_per_order_approvals.update_one(
                {"row_id": row.get("id"),
                 "order_number": order_number,
                 "approved_at": (approval_audit or {}).get(
                     "approved_at")},
                {"$set": {
                    "selective_send_blocked_after_approval": True,
                    "selective_send_blocker_code":
                        _blocked.blocker_code,
                    "selective_send_blocker_reason":
                        _blocked.blocker_reason,
                    "selective_send_blocked_at":
                        datetime.now(timezone.utc),
                }})
        except Exception:    # pragma: no cover
            pass
        raise OneShotRefused(
            "selective_send_policy_blocked",
            "Selective Send policy refused this order EVEN WITH a "
            "valid approval_phrase. approval_phrase alone cannot "
            "bypass Selective Send. See selective_send_blocker_code.",
            order_number=order_number,
            selective_send_blocker_code=_blocked.blocker_code,
            selective_send_blocker_reason=_blocked.blocker_reason,
        )

    # ── 7a-bis. rev43 — SSOT fail-closed gate (user decree) ──────────
    # ONE source of truth for "is this order sendable?". Runs for
    # EVERY one_shot invocation (canary, manual, batch) BEFORE any
    # api_client construction / Qoyod write. A fully GREEN diagnosis
    # is REQUIRED: unmapped product / DRY id / SKIPPED / DEAD_LETTER /
    # duplicate / amount mismatch ⇒ refuse. NO product creation inside
    # the send — Adopt first, re-diagnose, then ONE send.
    from integrations.qoyod.send_eligibility_ssot import (
        evaluate_order_for_qoyod_send as _ssot_evaluate,
    )
    _ssot = await _ssot_evaluate(
        db, user_id=user_id, order_number=str(order_number))
    if not _ssot.get("ready_to_send"):
        logger.warning(
            "rev43 SSOT_GATE_REFUSED order=%s primary=%s blockers=%s",
            order_number, _ssot.get("primary_blocker_code"),
            [b["code"] for b in _ssot.get("blockers") or []])
        raise OneShotRefused(
            "ssot_not_ready_to_send",
            ("مصدر الحقيقة الواحد رفض الإرسال — التشخيص ليس أخضر "
             "بالكامل. الحاجز الأساسي: "
             f"{_ssot.get('primary_blocker_code')}. "
             "أصلح الحواجز ثم أعد التشخيص قبل أي محاولة إرسال."),
            order_number=order_number,
            primary_blocker_code=_ssot.get("primary_blocker_code"),
            primary_blocker_reason=_ssot.get("primary_blocker_reason"),
            blockers=_ssot.get("blockers"),
        )

    # ── 7b. Iter-293.4-rev3 — Pre-send sendability check ─────────────
    # Re-run the preview-reprocess to make sure the latest state of
    # mappings + canonical produces a sendable payload. This catches:
    #   • DRY/PREVIEW IDs still lurking in product/customer mappings.
    #   • Order canceled/refunded since the approval was granted.
    #   • request_body that would contain null contact_id/product_id.
    # If ANY check fails, refuse to send — even though approval was
    # granted. The operator must fix the dependencies first.
    if use_unlocked_client:
        try:
            from integrations.qoyod.preview_reprocess import (
                preview_reprocess_one_order)
            preview = await preview_reprocess_one_order(
                db, user_id=user_id, trace_id=row.get("trace_id"))
        except Exception as _exc:    # pragma: no cover
            logger.warning("pre-send preview failed: %s", _exc)
            preview = {"ok": False, "error": str(_exc)}
        # Surface refusal cleanly if the preview reports not sendable.
        if preview.get("ok") is True:
            dep = ((preview.get("stages") or {}).get("invoice_preview")
                   or {}).get("dependency_status") or {}
            unresolved = dep.get("request_body_unresolved") or []
            if dep.get("sendable") is False or unresolved:
                raise OneShotRefused(
                    "sendability_check_failed",
                    ("approval granted but pre-send check found the "
                     "payload is NOT sendable: " +
                     (f"{len(unresolved)} unresolved field(s) — " if unresolved else "") +
                     str(dep.get("status") or "dependency_not_sendable")),
                    order_number=order_number,
                    sendable=bool(dep.get("sendable")),
                    status=dep.get("status"),
                    request_body_unresolved=unresolved,
                    will_create_customer=bool(dep.get("will_create_customer")),
                    will_create_products_count=len(
                        dep.get("will_create_products") or []),
                    hint=("Fix DRY/PREVIEW mappings via "
                          "POST /products/adopt or "
                          "GET /admin/products/dry-mappings, then "
                          "retry with the same approval_phrase."),
                )
        # else: preview itself errored — let the pipeline below surface
        # the real failure rather than blocking on a flaky preview run.

    api_client = QoyodAPIClient(
        api_key,
        db=db, user_id=user_id,
        # Use UNLOCKED client only when per-order approval validated.
        write_lock_enabled=(False if use_unlocked_client
                            else global_lock_active),
    )
    stage_sequence: list[str] = []
    result_log: list[dict] = []

    async def _refresh() -> dict:
        return await db.integration_inbox.find_one({"id": row["id"]})

    # Step A: NORMALIZED → CUSTOMER_RESOLVED (or SKIPPED / DEAD_LETTER)
    cur = await _refresh()
    if cur and cur.get("pipeline_stage") == "NORMALIZED":
        out = await process_normalized_row(
            db, cur, api_client=api_client)
        result_log.append({"step": "normalized", "result": out})
        if out.get("outcome") in ("DEAD_LETTER", "SKIPPED"):
            after = await _refresh() or {}
            _resp = _build_failure_response(
                outcome=out.get("outcome"),
                row_id=row["id"],
                trace_id=row.get("trace_id"),
                pipeline_error=after.get("pipeline_error") or {},
                last_failed_stage=after.get("last_failed_stage"),
                canonical_payload=after.get("canonical_payload") or {},
                invoice_snapshot=(after.get("qoyod_payloads") or {}).get("invoice"),
                stage_sequence=_extract_observed_sequence(after),
                quarantine_summary=quarantine_summary,
            )
            # rev47.1 — the row-level pipeline_error/last_failed_stage
            # may be STALE forensics from an older failure (prod
            # incident 2026-07: a fresh SKIPPED surfaced the old
            # FAILED_CUSTOMER fields and misled the operator). Surface
            # THIS attempt's actual reason + the freshly written
            # stage_history note.
            _resp["fresh_attempt_outcome"] = out.get("outcome")
            _resp["fresh_attempt_reason"] = out.get("reason")
            _hist = after.get("stage_history") or []
            _last = _hist[-1] if _hist else None
            if isinstance(_last, dict):
                _resp["fresh_attempt_note"] = _last.get("note")
                _resp["fresh_attempt_stage_written"] = _last.get(
                    "to_stage")
            _gate = after.get("selective_auto_send_gate")
            if isinstance(_gate, dict):
                _resp["fresh_gate_decision"] = {
                    "eligible": _gate.get("eligible"),
                    "reason":   _gate.get("reason"),
                    "detail":   _gate.get("detail"),
                }
            return _resp
        if out.get("outcome") == "CUSTOMER_RESOLVED":
            stage_sequence.append("CUSTOMER_RESOLVED")

    # Step B: CUSTOMER_RESOLVED → … → COMPLETED / PARTIAL_FAILURE /
    # DEAD_LETTER (including the DRY-leak preflight guard).
    cur = await _refresh()
    if cur and cur.get("pipeline_stage") == "CUSTOMER_RESOLVED":
        if "CUSTOMER_RESOLVED" not in stage_sequence:
            stage_sequence.append("CUSTOMER_RESOLVED")
        out = await process_customer_resolved_row(
            db, cur, api_client=api_client)
        result_log.append({"step": "customer_resolved", "result": out})

    # Final state inspection.
    final = await _refresh() or {}
    final_stage = final.get("pipeline_stage")

    # Re-derive the observed stage sequence from stage_history so the
    # response matches reality (not a guess from outcomes).
    stage_sequence = _extract_observed_sequence(final)

    # Hard refusal — the leak guard tripped. Surface request_body_json.
    pe = final.get("pipeline_error") or {}
    if final_stage == "DEAD_LETTER" and \
            pe.get("code") == "dry_run_product_id_leaked_to_production":
        return {
            "ok":      False,
            "outcome": "DEAD_LETTER",
            "reason":  "dry_run_product_id_leaked_to_production",
            "row_id":  row["id"],
            "trace_id": row.get("trace_id"),
            "failed_at_stage": "PRODUCT_RESOLVED",
            "error": {
                "code":    pe.get("code"),
                "message": pe.get("message"),
                "leaked_ids": pe.get("leaked_ids"),
            },
            "request_body_json": (final.get("qoyod_payloads") or {})
                                  .get("invoice_blocked_preflight"),
            "stage_sequence_observed": stage_sequence,
            "quarantine_summary": quarantine_summary,
        }

    if final_stage == "COMPLETED":
        payloads = final.get("qoyod_payloads") or {}
        invoice_payload = payloads.get("invoice")
        # Iter-290h.6 — Surface the /invoice_payments diagnostics so
        # the operator can see the EXACT body we POSTed to settle the
        # invoice + Qoyod's response. Without this the success panel
        # only showed the invoice body, masking whether the payment
        # link landed (which is the whole point of Iter-290h).
        invoice_payment_payload = payloads.get("invoice_payment")
        qoyod_responses = final.get("qoyod_responses") or {}
        ip_response_obj = qoyod_responses.get("invoice_payment") or {}
        invoice_payment_response = ip_response_obj.get("body")
        invoice_payment_status_code = (
            200 if invoice_payment_response is not None else None)
        # Belt-and-suspenders: re-verify product_ids in the final
        # snapshotted payload are non-DRY. This catches the (theoretical)
        # case where the leak guard had a bug.
        dry_leaks = _scan_payload_for_dry(invoice_payload)
        if dry_leaks:
            logger.error(
                "qoyod one-shot completed WITH dry leaks present "
                "(post-hoc detection): row=%s leaks=%s",
                row["id"], dry_leaks)
        return {
            "ok":      True,
            "outcome": "COMPLETED",
            "row_id":  row["id"],
            "trace_id": row.get("trace_id"),
            "stage_sequence_observed": stage_sequence,
            "expected_stage_sequence": list(EXPECTED_STAGE_SEQUENCE),
            "qoyod_invoice_id": final.get("qoyod_invoice_id"),
            "qoyod_invoice_number": final.get("qoyod_invoice_number"),
            "qoyod_receipt_id": final.get("qoyod_receipt_id"),
            "qoyod_customer_id": final.get("qoyod_customer_id"),
            # Iter-290h.6 — new payment-link diagnostics on success path.
            "qoyod_invoice_payment_id": final.get("qoyod_invoice_payment_id"),
            "invoice_payload": invoice_payload,
            "invoice_payment_payload": invoice_payment_payload,
            "invoice_payment_response": invoice_payment_response,
            "invoice_payment_status_code": invoice_payment_status_code,
            "receipt_payload": payloads.get("receipt"),
            "dry_leaks_in_final_payload": dry_leaks,  # MUST be []
            "quarantine_summary": quarantine_summary,
            # Iter-293.4-rev3 — Per-order approval audit reference.
            "per_order_approval": (
                {"approval_id":     approval_audit.get("approval_id"),
                 "approved_at":     approval_audit.get("approved_at").isoformat()
                                    if hasattr(approval_audit.get("approved_at"),
                                               "isoformat") else None,
                 "scope":           "single_order",
                 "global_lock_was_active": True}
                if approval_audit else None),
        }

    # Everything else = a failure result. Build a stage-specific
    # diagnostic block so the operator sees the EXACT payload that
    # failed (e.g. product create body when FAILED_PRODUCT) rather
    # than a stale invoice snapshot from a previous attempt.
    return _build_failure_response(
        outcome=final_stage or "UNKNOWN",
        row_id=row["id"],
        trace_id=row.get("trace_id"),
        pipeline_error=pe,
        last_failed_stage=final.get("last_failed_stage"),
        canonical_payload=final.get("canonical_payload") or {},
        invoice_snapshot=(final.get("qoyod_payloads") or {}).get("invoice"),
        stage_sequence=stage_sequence,
        quarantine_summary=quarantine_summary,
    )


# ─────────────────────────────────────────────────────────────────────
# Helpers — payload scans + response shaping
# ─────────────────────────────────────────────────────────────────────
def _scan_payload_for_dry(payload: Any) -> list[str]:
    """Return a list of 'field=value' descriptors for any `DRY:`
    prefixed id found anywhere in the invoice payload. Empty list
    means the payload is clean.
    """
    if not isinstance(payload, dict):
        return []
    leaks: list[str] = []
    inv = payload.get("invoice") if isinstance(payload.get("invoice"), dict) \
        else payload
    cid = inv.get("contact_id")
    if isinstance(cid, str) and cid.startswith("DRY:"):
        leaks.append(f"contact_id={cid}")
    for li in (inv.get("line_items") or []):
        pid = (li or {}).get("product_id")
        if isinstance(pid, str) and pid.startswith("DRY:"):
            leaks.append(f"product_id={pid}")
    return leaks


def _extract_observed_sequence(row: dict) -> list[str]:
    """Pull happy-path stages from stage_history in chronological
    order. Filters out RETRYING / DEAD_LETTER / SKIPPED so the
    operator sees the clean path the row took (if any).
    """
    happy = {
        "NORMALIZED", "RULES_APPLIED", "CUSTOMER_RESOLVED",
        "PRODUCT_RESOLVED", "INVOICE_CREATED",
        # Iter-290h.6 — INVOICE_PAYMENT_CREATED is the new
        # post-/invoice_payments success stage. RECEIPT_CREATED is
        # kept for historical rows that completed under the legacy
        # /receipts flow so their "stages traversed" view stays
        # truthful.
        "INVOICE_PAYMENT_CREATED",
        "RECEIPT_CREATED",
        "COMPLETED",
    }
    seen: list[str] = []
    for entry in (row.get("stage_history") or []):
        to_stage = entry.get("to_stage") if isinstance(entry, dict) else None
        if to_stage in happy and to_stage not in seen:
            seen.append(to_stage)
    return seen


def _extract_sku_and_sale_price(canonical_payload: dict) -> dict:
    """Pull the first line item's SKU + the selling_price WE WOULD send.

    Mirrors `product_resolver._build_product_payload` so the operator
    can verify, side-by-side, that the request body actually carried
    the fix (`selling_price` + `is_sold: true`, correct value).
    """
    items = (canonical_payload or {}).get("items") or []
    if not items:
        return {}
    it = items[0]
    raw_price = it.get("unit_price")
    try:
        selling_price = float(raw_price) if raw_price is not None else 0.0
    except (TypeError, ValueError):
        selling_price = 0.0
    return {
        "sku":           it.get("sku"),
        "selling_price_we_would_send": selling_price,
        "unit_price_from_canonical":   raw_price,
    }


def _build_failure_response(
    *, outcome: str, row_id: str, trace_id: Optional[str],
    pipeline_error: dict, last_failed_stage: Optional[str],
    canonical_payload: dict, invoice_snapshot: Any,
    stage_sequence: list[str], quarantine_summary: dict,
) -> dict:
    """Uniform failure shape with **stage-specific** diagnostics.

    Critical for `FAILED_PRODUCT` — the operator must see the EXACT
    `POST /products` body we tried to send, not a stale invoice
    snapshot from a previous attempt. The Qoyod API client already
    captures `request_body_json` + `qoyod_response_excerpt` +
    `endpoint` + `status_code` on every QoyodAPIError, and the
    pipeline persists that into `row.pipeline_error`. We just need
    to surface it.
    """
    pe = pipeline_error or {}
    failed_stage = last_failed_stage or pe.get("last_failed_stage") or outcome

    response: dict[str, Any] = {
        "ok":      False,
        "outcome": outcome,
        "row_id":  row_id,
        "trace_id": trace_id,
        "failed_at_stage": failed_stage,
        "error": {
            "code":         pe.get("code"),
            "message":      pe.get("message"),
            "status_code":  pe.get("status_code"),
            "endpoint":     pe.get("endpoint"),
            "qoyod_response_excerpt": pe.get("qoyod_response_excerpt"),
        },
        "stage_sequence_observed": stage_sequence,
        "quarantine_summary":      quarantine_summary,
    }

    # ── Totals Guard surface (Iter-273) ─────────────────────────────
    # The pipeline persists `totals_guard.details` to the row on
    # any FAILED_VALIDATION caused by mismatched line items / order
    # math. The error code (`line_items_incomplete`, `line_items_total_mismatch`,
    # `order_total_mismatch`) is in `pipeline_error.code` — the
    # detailed breakdown lives in `pe.details`.
    if pe.get("code") in (
        "line_items_incomplete",
        "line_items_total_mismatch",
        "order_total_mismatch",
    ):
        response["totals_guard"] = {
            "code":    pe.get("code"),
            "message": pe.get("message"),
            "details": pe.get("details") or {},
        }
        # Don't surface stage-specific payload blocks; the breakdown
        # is the actionable diagnostic here.
        return response

    # ── Stage-specific payload snapshots ────────────────────────────
    if failed_stage == "FAILED_PRODUCT":
        # `pipeline_error.request_body_json` IS the product-create
        # body the resolver tried to POST to `/products`. Surfacing
        # it lets the operator verify the live deploy carries the
        # full Qoyod-compatible payload:
        #   • selling_price (not sale_price, not price)
        #   • is_sold: true (the activation flag — without this
        #     Qoyod ignores selling_price and rejects the create)
        product_create_body = pe.get("request_body_json")
        # Drill into the wrapped form (resolver sends `{"product": {...}}`).
        inner = (product_create_body or {}).get("product") \
            if isinstance(product_create_body, dict) else None
        is_dict = isinstance(inner, dict)
        selling_price_field_present = is_dict and ("selling_price" in inner)
        sale_price_field_present    = is_dict and ("sale_price"    in inner)
        is_sold_flag                = (inner or {}).get("is_sold") if is_dict else None
        expected = _extract_sku_and_sale_price(canonical_payload)
        response["product_create"] = {
            "endpoint":     pe.get("endpoint")    or "POST /products",
            "status_code":  pe.get("status_code"),
            "request_body": product_create_body,
            "response_excerpt": pe.get("qoyod_response_excerpt"),
            "selling_price_field_present": selling_price_field_present,
            "sale_price_field_present":    sale_price_field_present,
            "is_sold_flag":                is_sold_flag,
            "selling_price_in_request_body":
                (inner or {}).get("selling_price") if is_dict else None,
            "sku_in_request_body":         (inner or {}).get("sku")
                                            if is_dict else None,
            "expected_from_canonical":     expected,
            # Quick verdict the operator can read at a glance.
            # The deploy is "good" only if BOTH conditions hold:
            #   - selling_price field is present
            #   - is_sold flag is true (activation flag)
            "deploy_carries_full_fix": (
                selling_price_field_present and is_sold_flag is True
            ),
        }
        return response

    if failed_stage in ("FAILED_INVOICE", "FAILED_RECEIPT"):
        # For invoice/receipt failures the offending body is the
        # invoice payload (and possibly the receipt). The snapshot
        # captured in `qoyod_payloads.invoice` IS what was attempted.
        response["invoice_payload"] = invoice_snapshot
        response["request_body_json"] = pe.get("request_body_json") \
                                       or invoice_snapshot
        return response

    if failed_stage in ("PAYMENT_LINK_FAILED",
                        "PAYMENT_METHOD_MAPPING_MISSING"):
        # Iter-290h.4 — Explicit branch for the payment-link step so
        # the operator sees diagnostic fields the user demanded:
        # `payment_post_attempted`, `request_sent_to_qoyod`,
        # `qoyod_status_code`, `qoyod_response`, `skip_reason`.
        #
        # The pipeline writes the invoice_payment error block to
        # `row.qoyod_responses.invoice_payment.error` AND to
        # `row.pipeline_error` (same dict). We surface both so the
        # frontend can label the panel correctly — "تم الإرسال
        # وقيود رفض" vs "تم الإيقاف قبل الإرسال".
        attempted    = (failed_stage == "PAYMENT_LINK_FAILED")
        sent         = attempted   # PAYMENT_LINK_FAILED only fires after the POST
                                    # raised — the request was on the wire.
        skip_reason  = None
        if failed_stage == "PAYMENT_METHOD_MAPPING_MISSING":
            skip_reason = (
                "لم يتم ضبط حساب قيود لطريقة الدفع المستخدمة — "
                "افتح إعدادات قيود ← طرق الدفع وضبط الحساب ثم أعد المحاولة.")
        response["payment_post_attempted"] = attempted
        response["request_sent_to_qoyod"]  = sent
        response["qoyod_status_code"]      = pe.get("status_code")
        response["qoyod_response"]         = pe.get("qoyod_response_excerpt")
        response["skip_reason"]            = skip_reason
        response["request_body_json"]      = pe.get("request_body_json")
        # Surface the invoice id so the operator can correlate with قيود UI.
        response["existing_qoyod_invoice_id"] = (
            (canonical_payload or {}).get("qoyod_invoice_id")
            or pe.get("qoyod_invoice_id"))
        return response

    # Catch-all (FAILED_CUSTOMER, FAILED_VALIDATION, etc.) — surface
    # whatever the error block carried.
    response["request_body_json"] = pe.get("request_body_json")
    return response
