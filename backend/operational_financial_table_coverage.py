"""Conservative dependencies of saved-source diagnostics, never balance writers.

Only reviewed, known source failures can be isolated. An unknown failure still
blocks every balance table, so a new source cannot silently become harmless.
"""
from copy import deepcopy

ALL_TABLES = frozenset({"banks_network", "cash", "suppliers", "employees", "external",
    "ads_currency_conversion", "ads", "employee_custody", "other_balances",
    "couriers", "drivers", "providers", "customer_wallets", "balance_origin"})

# These failures occur while pricing/recognizing a provider obligation. They do
# not write bank money; the separate settlement writer/evidence owns that effect.
PROVIDER_CALCULATION = frozenset({
    "provider_fee_estimate_unavailable", "provider_net_balance_incomplete",
    "provider_fee_policy_incomplete_or_ambiguous", "provider_payment_plan_required",
    "provider_payment_method_required_for_fee", "provider_payment_plan_conflict",
    "provider_fee_tax_rounding_invalid", "provider_fee_tax_basis_invalid",
    "provider_fee_tax_rate_invalid", "fee_tax_policy_incomplete",
    "provider_fee_component_shape_invalid", "provider_fee_component_identity_invalid",
    "provider_fee_rate_invalid", "provider_fee_policy_evidence_required",
    "provider_capture_evidence_missing", "provider_capture_evidence_incomplete",
    "provider_capture_timestamp_conflict", "provider_balance_sar_unavailable",
    "provider_sar_basis_conflict", "provider_historical_capture_review_required",
    "provider_fee_source_mode_ambiguous", "provider_fee_settings_ambiguous",
    "provider_fee_owner_mismatch",
})
SALARY_CALCULATION = frozenset({"salary_contract_missing", "salary_source_changed",
    "employee_salary_contract_missing_or_ambiguous", "employee_salary_contract_incomplete",
    "operational_salary_start_policy_invalid"})
AD_CALCULATION = frozenset({"ad_daily_close_evidence_incomplete", "ad_daily_source_incomplete",
    "ad_start_day_partial_baseline_required", "ad_start_day_baseline_evidence_invalid",
    "ad_fx_snapshot_incomplete", "advertising_start_day_coverage_required",
    "advertising_close_not_due_or_evidence_incomplete", "advertising_prepaid_wallet_negative"})
PREPAID = frozenset({"operational_prepaid_shipping_" + suffix for suffix in (
    "evidence_incomplete", "native_overlap", "amount_conflict", "cash_account_unavailable",
    "cash_binding_required", "completion_time_invalid", "event_invalid", "existing_charge_review_required",
    "owner_mismatch", "recorded_evidence_changed", "shipment_shared_by_orders", "tariff_ambiguous",
    "completion_ambiguous", "completion_timestamp_missing", "order_identity_conflict",
    "order_status_conflict", "source_stale")})
SHIPPING = frozenset({"courier_fee_historical_review_required", "driver_fee_evidence_conflict",
    "driver_fee_history_review_required", "shipping_identity_or_rate_incomplete", "driver_fee_saved_conflict",
    "courier_fee_native_overlap", "driver_fee_amount_conflict", "driver_fee_native_overlap",
    "driver_fee_payout_review_required", "driver_fee_policy_review_required"})


def affected_tables(issue):
    code = str(issue.get("code") or "")
    if code in PROVIDER_CALCULATION:
        return frozenset({"providers", "balance_origin"})
    if code in SALARY_CALCULATION:
        return frozenset({"employees"})
    if code in AD_CALCULATION:
        return frozenset({"ads", "ads_currency_conversion"})
    # Prepaid evidence can hide a cashbox debit, even when it also appears in
    # the shipping company table. Never isolate this warning to shipping only.
    if code in PREPAID:
        return frozenset({"cash", "couriers", "balance_origin"})
    if code in SHIPPING:
        return frozenset({"cash", "couriers", "drivers", "balance_origin"})
    if code in {"operational_driver_review_effect_conflict", "operational_driver_review_order_ambiguous",
                "operational_driver_review_executor_conflict"}:
        return frozenset({"banks_network", "drivers", "balance_origin"})
    return ALL_TABLES


def table_coverage(identity, issues, *, as_of, started_at, currency):
    relevant = [deepcopy(row) for row in issues
                if row.get("currency") in {None, currency} and identity in affected_tables(row)]
    # A provider summary is a diagnostic aggregate, not another affected order.
    ids = {str(row["source_id"]) for row in relevant if row.get("source_id")
           and row.get("code") != "provider_net_balance_incomplete"}
    unknown = sum(1 for row in relevant if not row.get("source_id"))
    return {"complete": bool(as_of) and not relevant, "as_of": as_of, "started_at": started_at,
            "currency": currency, "issues": relevant, "affected_source_count": len(ids),
            "unidentified_issue_count": unknown, "financial_impact": None}


def _reason(issue):
    if issue.get("message"):
        return str(issue["message"])
    code = str(issue.get("code") or "")
    if code in PROVIDER_CALCULATION:
        return "تعذر استكمال احتساب مستحق منصة الدفع من أدلة التحصيل والتعرفة المحفوظة"
    if code in SALARY_CALCULATION:
        return "تحتاج أيام استحقاق الرواتب أو عقودها وتواريخ سريانها إلى مراجعة"
    if code in AD_CALCULATION:
        return "تغطية إنفاق الإعلان أو سعر تحويله إلى الريال غير مكتملة"
    if code in PREPAID:
        return "دليل تنفيذ الشحنة المدفوعة مقدمًا أو تعرِفتها أو خصمها من الصندوق يحتاج إلى مراجعة"
    if code in SHIPPING:
        return "تحتاج أجرة الشحن أو هوية الشحنة أو دليل استحقاقها إلى مطابقة"
    return "يوجد تعارض أو نقص في مصدر قد يؤثر على هذا الرصيد؛ يلزم التحقق من تفاصيل المصدر"


def apply_table_coverage(tables, issues, *, as_of, started_at, currency):
    for table in tables:
        if table["id"] not in ALL_TABLES:
            continue
        coverage = table_coverage(table["id"], issues, as_of=as_of, started_at=started_at, currency=currency)
        table["coverage"] = coverage
        table["complete"] = table["complete"] and coverage["complete"]
        table["notes"] = [note for note in table.get("notes", [])
                          if not note.startswith("أسباب عدم الاكتمال لهذا الجدول:")]
        if coverage["issues"]:
            reasons = sorted({_reason(row) for row in coverage["issues"]})
            table.setdefault("notes", []).append(
                "أسباب عدم الاكتمال لهذا الجدول: " + "، ".join(reasons) + ". "
                + f"مصادر محددة متأثرة دون تكرار: {coverage['affected_source_count']}. "
                + "عدد المصادر ليس عدد شحنات مستحقة، والأثر المالي غير محسوم من التحذير وحده.")
