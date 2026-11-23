const optionalNumber = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

export const WEEKLY_PAYMENT_FEE_ESTIMATE_LABEL = "تقديري · متوسط أسبوعي";
export const OPERATIONAL_PAYMENT_FEE_ESTIMATE_LABEL = "تقديري · نسبة الأرصدة التشغيلية";

export function paymentFeeEstimateLabel(sources = []) {
    const bases = new Set((Array.isArray(sources) ? sources : []).map((source) => source?.basis));
    const weekly = bases.has("weekly_statement_average");
    const operational = bases.has("operational_balance_average");
    if (weekly && operational) return "تقديري · متوسط أسبوعي ونسبة الأرصدة التشغيلية";
    if (weekly) return WEEKLY_PAYMENT_FEE_ESTIMATE_LABEL;
    if (operational) return OPERATIONAL_PAYMENT_FEE_ESTIMATE_LABEL;
    return "تقديري";
}

export function paymentFeeSourceNotes(sources = []) {
    return (Array.isArray(sources) ? sources : [])
        .filter((source) => source && typeof source === "object")
        .map((source) => {
            const notes = [];
            if (source.provider === "tabby") notes.push("تابي");
            const weekly = source.basis === "weekly_statement_average";
            const operational = source.basis === "operational_balance_average";
            if (weekly) {
                notes.push("متوسط أسبوعي");
                if (source.window_start && source.window_end) notes.push(`أسبوع المرجع: ${source.window_start} إلى ${source.window_end}`);
            } else if (operational) {
                notes.push("نسبة الأرصدة التشغيلية");
                if (source.period_start && source.period_end) notes.push(`فترة الأرصدة التشغيلية: ${source.period_start} إلى ${source.period_end}`);
                const dateLabel = { statement_date: "تاريخ الكشف", sale_event_date: "تاريخ بيع المصدر", accepted_at: "وقت اعتماد المصدر" }[source.source_date_basis] || "تاريخ المصدر";
                if (source.source_as_of) notes.push(`${dateLabel}: ${source.source_as_of}`);
                if (source.accepted_at && source.source_date_basis !== "accepted_at") notes.push(`وقت اعتماد المصدر: ${source.accepted_at}`);
            }
            const sample = optionalNumber(source.eligible_sale_rows);
            const rate = optionalNumber(source.rate_percent);
            const fixed = optionalNumber(source.fixed_amount_per_sale);
            const vat = optionalNumber(source.vat_rate_percent);
            if (sample != null) notes.push(`عمليات البيع المرجعية: ${sample}`);
            if (rate != null) notes.push(`نسبة العمولة: ${rate.toLocaleString("en-US", { maximumFractionDigits: 8 })}%`);
            if (fixed != null) notes.push(`ثابت لكل عملية: ${fixed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 8 })}${source.native_currency ? ` ${source.native_currency}` : ""}`);
            if (vat != null) notes.push(`ضريبة العمولة: ${vat.toLocaleString("en-US", { maximumFractionDigits: 8 })}%`);
            if (source.stale_source === true) notes.push(weekly ? "المرجع الأسبوعي قديم" : "مصدر النسبة قديم");
            return notes.join(" · ");
        }).filter(Boolean);
}

export function buildPaymentFeeRows(paymentBreakdown = [], { preserveUnknown = false } = {}) {
    const output = [];
    (Array.isArray(paymentBreakdown) ? paymentBreakdown : []).forEach((group, groupIndex) => {
        const children = Array.isArray(group?.sub_methods) && group.sub_methods.length
            ? group.sub_methods
            : [group];
        children.forEach((child, childIndex) => {
            const kind = child?.kind || (group?.key === "ad_bank_commissions" ? "ad_bank_commission" : "payment_method");
            output.push({
                key: child?.key || `${group?.key || groupIndex}-${childIndex}`,
                name: child?.display || child?.name || group?.name || "طريقة دفع غير معروفة",
                parentName: child !== group ? (child?.parent_name || group?.name || null) : null,
                kind,
                ordersCount: optionalNumber(child?.orders_count ?? group?.orders_count),
                baseAmount: optionalNumber(child?.total_sales ?? group?.total_sales) ?? (preserveUnknown ? null : 0),
                commissionPercent: optionalNumber(child?.commission_percent ?? group?.commission_percent),
                fixedFee: optionalNumber(child?.fixed_fee ?? group?.fixed_fee),
                vatPercent: optionalNumber(child?.vat_percent ?? group?.vat_percent),
                vatAmount: optionalNumber(child?.vat_amount),
                feeAmount: optionalNumber(child?.fee_amount ?? group?.fee_amount) ?? (preserveUnknown ? null : 0),
                nativeCurrency: child?.native_currency || null,
                exchangeRateToSar: optionalNumber(child?.exchange_rate_to_sar),
                spendNative: optionalNumber(child?.spend_native),
                applyBankCommission: child?.apply_bank_commission !== false,
                configured: child?.configured === true,
                // A group may mix actual and estimated children. Do not spread
                // its aggregate estimate flag or count onto every sub-method.
                feesEstimated: child?.fees_estimated === true,
                estimatedOrdersCount: optionalNumber(child?.estimated_orders_count),
                feeEstimateSources: Array.isArray(child?.fee_estimate_sources) ? child.fee_estimate_sources : [],
            });
        });
    });
    return output;
}
