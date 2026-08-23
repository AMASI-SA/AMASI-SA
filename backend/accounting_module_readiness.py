"""Fail-closed readiness model for the unified accounting home."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from accounting_module_contract import EVIDENCE_SECTIONS, OPERATION_ID


def _aware_utc_iso(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _evidence_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value.get("ref") or value.get("evidence_ref") or value.get("documents"))
    if isinstance(value, (list, tuple, set)):
        return any(_evidence_present(item) for item in value)
    return bool(value)


def build_accounting_module_status(
    cutover: dict[str, Any] | None,
    *,
    provider_summary: dict[str, Any] | None = None,
    opening_posted_verified: bool = False,
    ledger_balances: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = cutover or {}
    provider = provider_summary or {}
    operation_matches = str(state.get("operation_id") or "").strip() == OPERATION_ID
    cutover_at = _aware_utc_iso(state.get("cutover_at")) if operation_matches else None
    evidence_sheet_ref = str(
        state.get("evidence_sheet_ref")
        or state.get("signed_evidence_sheet_ref")
        or ""
    ).strip()
    evidence_sections = state.get("evidence_sections") or {}

    checks: list[dict[str, Any]] = [
        {
            "id": "cutover_at",
            "label": "توقيت قطع واحد ودقيق مع المنطقة الزمنية",
            "complete": bool(cutover_at),
            "detail": cutover_at or "لم يُعتمد توقيت القطع بعد",
        },
        {
            "id": "signed_evidence_sheet",
            "label": "ورقة أدلة موقعة ومعتمدة",
            "complete": bool(evidence_sheet_ref),
            "detail": evidence_sheet_ref or "لم تُرفق ورقة الأدلة المعتمدة",
        },
    ]
    for section in EVIDENCE_SECTIONS:
        complete = _evidence_present(evidence_sections.get(section["id"]))
        checks.append({
            "id": f"evidence:{section['id']}",
            "label": section["label"],
            "complete": complete,
            "detail": "مكتمل بالأدلة" if complete else "ينقصه دليل مطابق لنفس توقيت القطع",
        })

    preview_id = str(state.get("opening_balance_preview_id") or "").strip()
    preview_balanced = state.get("opening_balance_preview_balanced") is True
    approved = bool(
        state.get("opening_balance_approved_at")
        and state.get("opening_balance_approved_by")
    )
    opening_txn_group_id = str(state.get("opening_balance_txn_group_id") or "").strip()
    checks.extend([
        {
            "id": "opening_preview",
            "label": "معاينة القيد الافتتاحي متوازنة",
            "complete": bool(preview_id and preview_balanced),
            "detail": f"المعاينة {preview_id} متوازنة" if preview_id and preview_balanced else "لم تُنشأ معاينة افتتاحية معتمدة ومتوازنة",
        },
        {
            "id": "opening_approval",
            "label": "اعتماد صريح للقيد الافتتاحي",
            "complete": approved,
            "detail": "تم تسجيل الاعتماد" if approved else "لم يعتمد المالك/المحاسب القيد الافتتاحي",
        },
        {
            "id": "opening_posted",
            "label": "القيد الافتتاحي المرحّل موثق ومتوازن",
            "complete": bool(opening_txn_group_id and opening_posted_verified),
            "detail": f"تم التحقق من مجموعة القيد {opening_txn_group_id}" if opening_txn_group_id and opening_posted_verified else "لا يوجد قيد افتتاحي مرحّل ومتحقق منه لهذه العملية",
        },
    ])

    ready = all(item["complete"] for item in checks)
    configured_status = str(state.get("status") or "not_configured").strip().lower()
    active = configured_status == "active" and operation_matches and bool(cutover_at)
    safe_active = active and ready
    incomplete = [item for item in checks if not item["complete"]]
    unverified_docs = int(provider.get("unverified_tax_invoices") or 0)
    ledger = ledger_balances if safe_active else None
    unclassified_count = int((ledger or {}).get("unclassified_count") or 0)

    tasks = [
        {"id": item["id"], "title": item["label"], "detail": item["detail"], "page": "opening-balances"}
        for item in incomplete
    ]
    if unverified_docs:
        tasks.append({
            "id": "provider-evidence-review",
            "title": "مستندات مزودين تحتاج تحققًا",
            "detail": f"{unverified_docs} مستند لم يصل إلى حالة متحقق منه",
            "page": "settlements",
        })
    if unclassified_count:
        tasks.append({
            "id": "ledger-unclassified-accounts",
            "title": "حسابات قيود غير مصنفة في الرئيسية المحاسبية",
            "detail": f"{unclassified_count} حساب يحتاج ربطًا بنوع مالي معتمد",
            "page": "journals-reports",
        })

    balances_available = safe_active and ledger is not None
    return {
        "operation_id": OPERATION_ID,
        "legacy_financial_data_included": False,
        "cutover": {
            "configured_status": configured_status,
            "operation_matches": operation_matches,
            "cutover_at": cutover_at,
            "ready_for_activation": ready,
            "active": active,
            "safe_active": safe_active,
            "unsafe_activation_detected": bool(active and not ready),
        },
        "balance_visibility": {
            "status": "available" if balances_available else "blocked",
            "reason": "cutover_active_opening_verified_ledger_only" if balances_available else "cutover_and_opening_evidence_not_fully_approved",
            "source": "general_ledger_operation_scoped" if ledger is not None else None,
            "banks": (ledger or {}).get("banks"),
            "providers": (ledger or {}).get("providers"),
            "couriers_cod": (ledger or {}).get("couriers_cod"),
            "couriers_cod_receivable": (ledger or {}).get("couriers_cod_receivable"),
            "couriers_payable": (ledger or {}).get("couriers_payable"),
        },
        "review_count": len(incomplete) + unverified_docs + unclassified_count,
        "readiness": checks,
        "tasks": tasks,
        "provider_evidence": {
            "providers": int(provider.get("providers") or 0),
            "tax_invoices": int(provider.get("tax_invoices") or 0),
            "verified_tax_invoices": int(provider.get("verified_tax_invoices") or 0),
            "unverified_tax_invoices": unverified_docs,
        },
        "implementation_audit": [
            {"page": "home", "status": "implemented"},
            {"page": "settlements", "status": "partial_existing_workflows"},
            {"page": "shipping-cod", "status": "partial_existing_workflows"},
            {"page": "inventory-purchases", "status": "partial_existing_workflows"},
            {"page": "financial-movements", "status": "partial_existing_workflows"},
            {"page": "payroll-obligations", "status": "partial_existing_workflows"},
            {"page": "opening-balances", "status": "blocked_not_implemented"},
            {"page": "journals-reports", "status": "partial_existing_workflows"},
        ],
    }
