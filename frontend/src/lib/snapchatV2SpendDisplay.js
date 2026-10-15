export function snapchatV2SpendDisplay(report) {
    const headline = report?.headline_spend_native ?? report?.base_spend_native;
    const parsedHeadline = Number(headline);
    const parsedUnallocated = Number(report?.unallocated_spend_native || 0);
    return {
        spendNative: headline !== null
            && headline !== undefined
            && headline !== ""
            && Number.isFinite(parsedHeadline)
            ? parsedHeadline
            : null,
        unallocatedSpendNative: Number.isFinite(parsedUnallocated)
            ? parsedUnallocated
            : 0,
        hourlyBreakdownComplete: report?.hourly_breakdown_complete
            ?? report?.amount_complete
            ?? false,
    };
}
