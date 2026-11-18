import {
    dashboardAvailableAmount,
    dashboardCanonicalSpendMatches,
    dashboardKnownIncompleteAmount,
    dashboardSpendPeriodMatches,
} from "../services/dashboardAdsSpend";

const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function financialAmount(value, { nonnegative = false } = {}) {
    if (!(typeof value === "number" || typeof value === "string" && value.trim() !== "")) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && (!nonnegative || parsed >= 0) ? parsed : null;
}

/** Shared display eligibility for the server-owned final net, with no new financial calculation. */
export function dashboardExecutiveProfitState({ executiveSnapshot, spendSnapshot, fromDate, toDate } = {}) {
    const hasExecutive = record(executiveSnapshot) && record(executiveSnapshot.totals);
    const totals = hasExecutive ? executiveSnapshot.totals : {};
    const selectedPeriod = fromDate !== undefined || toDate !== undefined;
    const periodMatches = hasExecutive && (!selectedPeriod || dashboardSpendPeriodMatches({
        date_from: executiveSnapshot.range?.from_date,
        date_to: executiveSnapshot.range?.to_date,
    }, { fromDate, toDate }));
    const productCostComplete = typeof totals.product_cost_complete === "boolean"
        ? totals.product_cost_complete
        : Number(totals.incomplete_profit_orders_count || executiveSnapshot?.product_cost_v2?.incomplete_orders_count || 0) === 0;
    const salesConversionComplete = totals.sales_currency_conversion_complete !== false
        && executiveSnapshot?.currency_conversion?.conversion_complete !== false;
    const adsQuality = executiveSnapshot?.ads_v2?.spend_quality || {};
    const adsTotal = financialAmount(totals.total_ads_cost, { nonnegative: true });
    // Undefined retains the legacy executive-card contract. An explicitly
    // supplied shared snapshot is newer display evidence and must reconcile.
    const sharedSnapshotSupplied = spendSnapshot !== undefined;
    const platformSnapshot = sharedSnapshotSupplied ? spendSnapshot : executiveSnapshot?.dashboard_platform_spend;
    const sharedSpendPeriodPending = sharedSnapshotSupplied && selectedPeriod
        && !dashboardSpendPeriodMatches(platformSnapshot, { fromDate, toDate });
    const currentPlatformIncomplete = Boolean(sharedSnapshotSupplied && fromDate
        && platformSnapshot?.date_from === fromDate && platformSnapshot?.date_to === (toDate || fromDate)
        && platformSnapshot?.spend_quality?.status === "incomplete"
        && platformSnapshot?.spend_quality?.amount_available === false);
    const sharedAdsTotal = sharedSnapshotSupplied
        ? dashboardAvailableAmount(platformSnapshot, { fromDate, toDate }) : null;
    const sharedSpendAvailable = !sharedSnapshotSupplied || !selectedPeriod || sharedAdsTotal !== null;
    let sharedAdsChanged = false;
    if (sharedAdsTotal !== null) {
        try {
            sharedAdsChanged = !dashboardCanonicalSpendMatches(executiveSnapshot, platformSnapshot);
        } catch {
            sharedAdsChanged = true;
        }
    }
    const adsSpendComplete = periodMatches && sharedSpendAvailable && !sharedSpendPeriodPending && !currentPlatformIncomplete && !sharedAdsChanged && adsTotal !== null
        && totals.ads_spend_data_complete === true
        && adsQuality.status === "complete" && adsQuality.amount_complete === true;
    const adsSpendAvailable = periodMatches && sharedSpendAvailable && !sharedSpendPeriodPending && !currentPlatformIncomplete && !sharedAdsChanged
        && (adsSpendComplete || adsTotal !== null
            && totals.ads_spend_amount_available === true && adsQuality.amount_available === true);
    const adsSpendProvisional = adsSpendAvailable && !adsSpendComplete
        && totals.ads_spend_provisional === true && adsQuality.provisional === true;
    const knownAdsTotal = !adsSpendAvailable
        ? dashboardKnownIncompleteAmount(platformSnapshot, { fromDate, toDate }) : null;
    const summarySpendPending = sharedAdsTotal !== null && !adsSpendAvailable;
    const displaySpendProvisional = sharedAdsTotal !== null
        ? platformSnapshot.spend_quality.status === "provisional" : adsSpendProvisional;
    const canonicalNet = financialAmount(totals.net_profit);
    const reason = !hasExecutive ? "executive_pending"
        : !periodMatches ? "executive_period_pending"
            : !productCostComplete ? "product_cost_incomplete"
                : !salesConversionComplete ? "sales_currency_incomplete"
                    : sharedSpendPeriodPending ? "spend_period_pending"
                        : sharedAdsChanged ? "spend_basis_pending"
                            : !adsSpendAvailable ? "ads_spend_incomplete"
                                : canonicalNet === null ? "net_profit_unavailable" : "ready";
    const ready = reason === "ready";
    return {
        ready,
        netProfit: ready ? canonicalNet : null,
        operatingExpenses: periodMatches ? financialAmount(totals.operating_expenses_total, { nonnegative: true }) : null,
        provisional: ready && adsSpendProvisional,
        reason,
        periodMatches,
        productCostComplete,
        salesConversionComplete,
        adsTotal,
        platformSnapshot,
        sharedSpendPeriodPending,
        currentPlatformIncomplete,
        sharedAdsTotal,
        sharedAdsChanged,
        adsSpendComplete,
        adsSpendAvailable,
        adsSpendProvisional,
        knownAdsTotal,
        summarySpendPending,
        displaySpendProvisional,
    };
}
