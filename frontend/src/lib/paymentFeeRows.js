const optionalNumber = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

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
            });
        });
    });
    return output;
}
