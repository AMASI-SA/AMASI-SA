"""Conservative dependencies of saved-source diagnostics, never balance writers.

Only reviewed, known source failures can be isolated. An unknown failure still
blocks every balance table, so a new source cannot silently become harmless.
"""
from copy import deepcopy
import json

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
    "provider_sar_basis_conflict",
    "provider_fee_source_mode_ambiguous", "provider_fee_settings_ambiguous",
    "provider_fee_owner_mismatch",
    "provider_plan_scope_conflict", "provider_plan_scope_invalid",
    "provider_plan_scope_contract_changed", "provider_plan_scope_terms_differ",
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

# operational_balance_sources emits these only after product components have
# been extracted, tagged scope=provider_payment. The unresolved funding source
# can be a bank receipt, provider claim, customer credit or COD/cash custody;
# independent supplier invoices and salary accruals are not rewritten here.
PAYMENT_SOURCE_AMBIGUITY = frozenset({
    "provider_payment_method_unknown", "provider_payment_method_conflict",
    "provider_split_payment_review_required", "provider_payment_shape_invalid",
    "provider_payment_currency_conflict", "provider_payment_amount_invalid",
    "provider_payment_amount_conflict", "provider_payment_reference_missing",
    "provider_capture_original_basis_required", "provider_refund_execution_evidence_required",
    "provider_cancellation_execution_evidence_required",
})
VERIFIED_PROVIDER_CAPTURE = frozenset({
    "provider_capture_timestamp_invalid", "provider_capture_conflict", "provider_capture_status_conflict",
    "provider_transaction_order_conflict", "provider_transaction_history_conflict",
    "provider_transaction_binding_conflict", "provider_transaction_not_captured",
    "provider_transaction_amount_conflict",
})
MONEY_SOURCE_TABLES = frozenset({"banks_network", "cash", "providers", "couriers", "drivers",
    "employee_custody", "customer_wallets", "balance_origin"})
HISTORICAL_CAPTURE = frozenset({"provider_historical_capture_review_required",
    "provider_settled_capture_review_required"})
# Exact failures from operational_delivery_evidence, held by the shipping-only
# source phase before _attach_delivery_sources. Identity/owner/canonical-source
# failures are deliberately not covered by this allowlist.
DELIVERY_PHASE_FAILURES = frozenset({
    "operational_delivery_timestamp_missing", "operational_delivery_timestamp_invalid",
    "operational_delivery_timestamp_timezone_missing", "operational_delivery_timestamp_ambiguous",
    "operational_delivery_timestamp_before_order", "operational_delivery_timestamp_before_shipment",
    "operational_delivery_current_shipment_required", "operational_delivery_shipment_identity_missing",
    "operational_delivery_shipment_identity_conflict", "operational_delivery_shipment_not_delivered",
    "operational_delivery_shipment_status_conflict", "operational_delivery_shipment_waybill_conflict",
    "operational_delivery_order_waybill_unproven",
})
KNOWN_PROVIDERS = frozenset({"salla", "tabby", "tamara", "emkan"})


def index_report_coverage(report):
    """Lossless opt-in transport: send each diagnostic once, retain every link.

    No eligibility, amount, completeness flag, note or case count changes.
    Existing API clients keep the expanded default. This only avoids sending
    the same historical source diagnostics again for every affected table.
    """
    catalog, positions = [], {}
    currencies = {}
    for code, currency in report.get('currencies', {}).items():
        tables = []
        for table in currency.get('tables', []):
            coverage = table.get('coverage')
            if coverage is not None:
                indexes = []
                for issue in coverage.get('issues', []):
                    key = json.dumps(issue, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                    if key not in positions:
                        positions[key] = len(catalog)
                        catalog.append(issue)
                    indexes.append(positions[key])
                table = {**table, 'coverage': {**{k: v for k, v in coverage.items() if k != 'issues'},
                                               'issue_indexes': indexes}}
            tables.append(table)
        currencies[code] = {**currency, 'tables': tables}
    return {**report, 'currencies': currencies, 'coverage_format': 'indexed',
            'coverage_issue_catalog': catalog}


def _historical_capture_phase(issue):
    # This exact payload is emitted by the provider-history handler in the
    # engine. A bare error code cannot establish which source phase failed.
    page = issue.get("order_page_balance_review")
    if not isinstance(page, dict) or not page.get("order_id"):
        return False
    provider = issue.get("provider")
    return (provider in KNOWN_PROVIDERS and page.get("provider") == provider
            and issue.get("source_id") in {"provider:" + str(page["order_id"]),
                "provider:" + str(page["order_id"]) + ":" + provider})


def affected_tables(issue):
    code = str(issue.get("code") or "")
    if code == "canonical_salla_source_missing":
        # The canonical-order loop stops before order costs/payment/delivery.
        # Salary contracts, advertising snapshots and external-party ledger
        # balances are independent sources. Supplier receipts still depend on
        # the skipped order identity; cash and consolidated coverage stay held.
        source = issue.get("source_id")
        # Collector emits the native Salla order_number. Placeholders or
        # arbitrary text cannot establish a specific order dependency.
        valid_reference = (type(source) is int and source > 0) or (
            isinstance(source, str) and source.isascii() and source.isdecimal()
            and any(digit != "0" for digit in source))
        if valid_reference:
            return ALL_TABLES - {"employees", "external", "ads", "ads_currency_conversion"}
        return ALL_TABLES
    if code in HISTORICAL_CAPTURE:
        # The history check includes previous bank/COD payments, customer
        # returns/exchanges and same-order movements (including custody legs).
        return MONEY_SOURCE_TABLES if _historical_capture_phase(issue) else ALL_TABLES
    if code in PAYMENT_SOURCE_AMBIGUITY:
        return MONEY_SOURCE_TABLES if issue.get("scope") == "provider_payment" else ALL_TABLES
    if code in VERIFIED_PROVIDER_CAPTURE:
        if issue.get("scope") == "provider_payment" and issue.get("provider") in KNOWN_PROVIDERS:
            return frozenset({"providers", "balance_origin"})
        return ALL_TABLES
    if code in DELIVERY_PHASE_FAILURES:
        if issue.get("component") == "shipping_fee":
            return frozenset({"banks_network", "cash", "couriers", "drivers", "employee_custody", "balance_origin"})
        return ALL_TABLES
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
    ids = set()
    for row in relevant:
        if not row.get("source_id") or row.get("code") == "provider_net_balance_incomplete":
            continue
        source = str(row["source_id"])
        if (row.get("scope") == "provider_payment" or source.startswith("provider:")):
            source = source.removeprefix("provider:")
            provider = str(row.get("provider") or "")
            if provider:
                source = source.removesuffix(":" + provider)
            ids.add(("provider_order", source))
        else:
            ids.add(("source", source))
    unknown = sum(1 for row in relevant if not row.get("source_id"))
    return {"complete": bool(as_of) and not relevant, "as_of": as_of, "started_at": started_at,
            "currency": currency, "issues": relevant, "affected_source_count": len(ids),
            "unidentified_issue_count": unknown, "financial_impact": None}


def _reason(issue):
    if issue.get("message"):
        return str(issue["message"])
    code = str(issue.get("code") or "")
    if code in PAYMENT_SOURCE_AMBIGUITY or code in HISTORICAL_CAPTURE:
        return "يلزم مطابقة مصدر التحصيل أو الاسترداد مع البنك والمنصة والعهد قبل اكتمال الأرصدة المتأثرة"
    if code in DELIVERY_PHASE_FAILURES:
        return "دليل تسليم الشحنة أو توقيته غير مكتمل؛ يلزم مطابقة أجرتها وتحصلاتها والعهد المرتبطة بها"
    if code in PROVIDER_CALCULATION or code in VERIFIED_PROVIDER_CAPTURE:
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
