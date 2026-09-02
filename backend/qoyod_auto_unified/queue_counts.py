"""Queue counters and per-order operator classification."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from integrations.qoyod.candidate_orders import (
    PAYMENT_ELIGIBLE, PAYMENT_NEEDS_LIVE_VERIFICATION, build_candidate_audit,
)
from integrations.qoyod.payment_methods import is_cod_family
from integrations.qoyod.unsent_orders import DUPLICATE, FAILED, SENT, UNSENT

from .common import RETRYABLE_SYNC_FAILURE_CODES
from .invoice_state import _invoice_financials


def _invoice_resolved(invoice: dict[str, Any], payment_method: Any) -> bool:
    return bool(
        _invoice_financials(invoice).get("resolved_paid")
        or is_cod_family(payment_method)
    )


async def _queue_audit(
    db: Any,
    *,
    user_id: str,
    orders_user_id: str,
    days: int,
    search: Optional[str],
    now: Optional[datetime],
    from_date: Any,
    to_date: Any,
    audit: Optional[dict[str, Any]] = None,
    failures: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, int]]:
    from integrations.qoyod import unsent_orders as unsent_module

    if audit is None:
        audit = await build_candidate_audit(
            db,
            orders_user_id=str(orders_user_id),
            markers_user_id=str(user_id),
            marker_user_ids=(str(user_id), str(orders_user_id)),
            from_date=from_date,
            to_date=to_date,
            days=days,
            now=now,
            search=search,
            lightweight=True,
            require_complete=False,
        )
    if failures is None:
        failures = await unsent_module._manual_failure_evidence(
            db,
            markers_user_id=str(user_id),
            order_numbers=audit["eligible_references"],
            scan_limit=int(audit.get("scan_limit") or 10_000),
        )
    queue = {
        "ready_to_send": 0,
        "quarantined": 0,
        "needs_payment_verification": 0,
        "in_qoyod": 0,
        "retryable_sync": 0,
    }
    for proof in audit.get("orders") or []:
        reference = str(proof.get("order_number") or "")
        invoice = proof.get("qoyod_invoice")
        if invoice is not None:
            queue["in_qoyod"] += 1
            continue
        payment_state = proof.get("payment_eligibility")
        failure = failures.get(reference) or {}
        failure_code = str(failure.get("code") or "")
        if payment_state == PAYMENT_NEEDS_LIVE_VERIFICATION:
            queue["needs_payment_verification"] += 1
            continue
        if failure_code:
            if failure_code in RETRYABLE_SYNC_FAILURE_CODES:
                queue["retryable_sync"] += 1
            else:
                queue["quarantined"] += 1
            continue
        if proof.get("worker_candidate") and payment_state == PAYMENT_ELIGIBLE:
            queue["ready_to_send"] += 1
    return audit, failures, queue


def _proof_classification(
    unsent_module: Any,
    proof: dict[str, Any],
    failure: Optional[dict[str, Any]],
) -> dict[str, Any]:
    invoice = proof.get("qoyod_invoice")
    count = int(proof.get("qoyod_invoice_count_for_reference") or 0)
    payment_method = proof.get("payment_method")
    if count > 1:
        return {
            "status": DUPLICATE,
            "reason": (
                "يوجد أكثر من فاتورة قيود تحمل reference نفسه؛ "
                "الإرسال محظور حتى المراجعة"
            ),
            "retry_allowed": False,
        }
    if invoice is not None:
        financials = _invoice_financials(invoice)
        if financials["resolved_paid"] or is_cod_family(payment_method):
            return {
                "status": SENT,
                "reason": (
                    f"فاتورة قيود #{invoice.get('qoyod_invoice_id')} — "
                    "أُرسل وتمت المصالحة"
                ),
                "retry_allowed": False,
                "failure_code": None,
                "failure_source": None,
            }
        if (
            failure
            or (financials.get("remaining") or 0) > 0.01
            or str(invoice.get("status") or "").lower()
            == "invoice_sent_receipt_failed"
        ):
            return {
                "status": FAILED,
                "reason": (
                    str((failure or {}).get("message") or "").strip()
                    or "الفاتورة موجودة في قيود لكن سند السداد لم يكتمل"
                ),
                "failure_code": (
                    (failure or {}).get("code")
                    or "invoice_sent_receipt_failed"
                ),
                "failure_source": (
                    (failure or {}).get("source") or "qoyod_invoices"
                ),
                "retry_allowed": False,
            }
        return {
            "status": SENT,
            "reason": (
                f"فاتورة قيود #{invoice.get('qoyod_invoice_id')} — "
                "مطابقة دقيقة لرقم الطلب في reference"
            ),
            "retry_allowed": False,
        }

    inbox_row = proof.get("inbox_row")
    if inbox_row is not None:
        classification = unsent_module.simplify_row(
            inbox_row, in_qoyod_by_reference=False
        )
        if classification.get("status") == SENT:
            classification = {
                "status": UNSENT,
                "reason": (
                    "توجد علامة محلية لكن لا يوجد مرجع فاتورة مطابق "
                    "في qoyod_invoices"
                ),
                "retry_allowed": True,
            }
        return unsent_module._overlay_manual_failure(
            classification, failure
        )
    return {
        "status": UNSENT,
        "reason": (
            "طلب مؤهل في unified_orders؛ سيُنشئ العامل إسقاط الإرسال "
            "تلقائياً دون الحاجة إلى سجل قديم"
        ),
        "retry_allowed": False,
    }
