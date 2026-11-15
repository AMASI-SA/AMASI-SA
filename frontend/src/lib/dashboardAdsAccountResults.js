import {
    DASHBOARD_ADS_PROVIDERS,
    dashboardAvailableAmount,
    dashboardCanonicalSpendMatches,
    dashboardSpendPeriodMatches,
} from "../services/dashboardAdsSpend";

const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function amount(value) {
    if (!(typeof value === "number" || typeof value === "string" && value.trim() !== "")) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 && parsed <= Number.MAX_SAFE_INTEGER ? parsed : null;
}

function count(value) {
    const parsed = amount(value);
    return Number.isSafeInteger(parsed) ? parsed : null;
}

function accountId(row) {
    const id = record(row) ? row.account_id : null;
    return typeof id === "string" && id.trim() && id.length <= 512 ? id : null;
}

function emptyStats(partial = false) {
    return { orders: null, sales: null, cpa: null, knownOrders: null, knownSales: null, partial };
}

function displayedAccounts(rows) {
    const result = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
        const id = accountId(row);
        if (id !== null && !result.has(id)) result.set(id, row);
    });
    return result;
}

// Conflicting duplicate evidence never chooses an arbitrary account total.
function accountEvidence(rows, providerRequired = false) {
    const result = new Map();
    (Array.isArray(rows) ? rows : []).forEach((row) => {
        const id = accountId(row);
        if (id === null || providerRequired && row.provider !== "snapchat") return;
        result.set(id, result.has(id) ? null : row);
    });
    return result;
}

function accountSpend(row, ready) {
    const quality = row?.quality;
    return ready && row?.projection_timezone === "Asia/Riyadh"
        && quality?.amount_available === true && quality.connected !== false
        && ["complete", "provisional"].includes(quality.status)
        && quality.data_state !== "unknown_incomplete" ? amount(row.total_sar) : null;
}

function providerSpend(snapshot, provider, ready) {
    const quality = snapshot?.providers?.[provider];
    return ready && quality?.amount_available !== false
        && quality?.data_state !== "unknown_incomplete" && quality?.status !== "incomplete"
        && quality?.data_quality !== "incomplete" ? amount(snapshot?.provider_totals_sar?.[provider]) : null;
}

function metrics(row, spend, basisMatches, { account = false, coverageComplete = true } = {}) {
    if (!record(row)) return emptyStats(true);
    const attributed = account ? coverageComplete && row.attribution_complete === true
        : row.attribution_complete !== false;
    const converted = account ? row.sales_currency_conversion_complete === true
        : row.sales_currency_conversion_complete !== false;
    const orders = attributed ? count(row.salla_orders) : null;
    const sales = attributed && converted ? amount(row.salla_sales_sar) : null;
    return {
        orders,
        sales,
        cpa: basisMatches && spend !== null && orders !== null && orders > 0 ? amount(spend / orders) : null,
        knownOrders: orders ?? count(row.known_salla_orders),
        knownSales: sales ?? amount(row.known_salla_sales_sar),
        partial: orders === null || sales === null,
    };
}

function sum(values, { complete = false, integer = false } = {}) {
    if (!values.length || complete && values.some((value) => value === null)) return null;
    const known = values.filter((value) => value !== null);
    if (!known.length) return null;
    const total = known.reduce((value, next) => value + next, 0);
    return integer ? count(total) : amount(Number(total.toFixed(2)));
}

function totalMetrics(rows, spends, basisMatches) {
    const orders = sum(rows.map((row) => row.orders), { complete: true, integer: true });
    const sales = sum(rows.map((row) => row.sales), { complete: true });
    const spend = sum(spends, { complete: true });
    const partial = orders === null || sales === null || rows.some((row) => row.partial);
    return {
        orders,
        sales,
        cpa: basisMatches && !partial && spend !== null && orders > 0 ? amount(spend / orders) : null,
        knownOrders: sum(rows.map((row) => row.knownOrders), { integer: true }),
        knownSales: sum(rows.map((row) => row.knownSales)),
        partial,
    };
}

/** Display-only Salla metrics. This reads existing snapshots and never fetches or writes. */
export function buildDashboardAdsTableMetrics({
    executiveSnapshot,
    spendSnapshot,
    fromDate,
    toDate,
    snapchatAccounts = null,
} = {}) {
    const range = { fromDate, toDate: toDate || fromDate };
    const accountMode = Array.isArray(snapchatAccounts);
    const displayed = displayedAccounts(snapchatAccounts);
    const accounts = new Map([...displayed.keys()].map((id) => [id, emptyStats()]));
    const providers = Object.fromEntries(DASHBOARD_ADS_PROVIDERS.map((provider) => [provider, emptyStats()]));
    const executiveReady = record(executiveSnapshot) && dashboardSpendPeriodMatches({
        date_from: executiveSnapshot.range?.from_date,
        date_to: executiveSnapshot.range?.to_date,
    }, range);
    if (!executiveReady) return { accounts, providers, total: emptyStats(), unassignedOrders: null, pending: true };

    const spendReady = dashboardSpendPeriodMatches(spendSnapshot, range);
    let basisMatches = false;
    try {
        basisMatches = spendReady && amount(spendSnapshot?.total_sar) !== null
            && amount(executiveSnapshot?.totals?.total_ads_cost) !== null
            && dashboardAvailableAmount(spendSnapshot, range) !== null
            && dashboardCanonicalSpendMatches(executiveSnapshot, spendSnapshot);
    } catch {
        // Malformed basis evidence must not hide independently known Salla facts.
    }

    const providerRows = executiveSnapshot.ads_v2?.executive_breakdown?.providers;
    const providerSpends = new Map();
    DASHBOARD_ADS_PROVIDERS.forEach((provider) => {
        const spend = providerSpend(spendSnapshot, provider, spendReady);
        providerSpends.set(provider, spend);
        providers[provider] = metrics(providerRows?.[provider], spend, basisMatches);
    });

    const result = executiveSnapshot.ads_v2?.account_results;
    const accountsReady = record(result) && result.source_only === true
        && result.timezone === "Asia/Riyadh" && dashboardSpendPeriodMatches(result, range)
        && Array.isArray(result.accounts);
    const unassignedOrders = accountsReady ? count(result.coverage?.unassigned_orders) : null;
    const coverageComplete = accountsReady && result.coverage?.catalog_complete === true
        && result.coverage?.status === "complete" && unassignedOrders === 0;
    const resultsById = accountsReady ? accountEvidence(result.accounts, true) : new Map();
    const spendsById = accountEvidence(spendSnapshot?.providers?.snapchat?.accounts);
    const accountSpends = [];
    displayed.forEach((_, id) => {
        const spend = accountSpend(spendsById.get(id), spendReady);
        accountSpends.push(spend);
        accounts.set(id, accountsReady
            ? metrics(resultsById.get(id), spend, basisMatches, { account: true, coverageComplete })
            : emptyStats());
    });

    const visibleProviders = DASHBOARD_ADS_PROVIDERS.filter((provider) => !accountMode || provider !== "snapchat");
    const rows = [...accounts.values(), ...visibleProviders.map((provider) => providers[provider])];
    const spends = [...accountSpends, ...visibleProviders.map((provider) => providerSpends.get(provider))];
    return {
        accounts,
        providers,
        total: totalMetrics(rows, spends, basisMatches),
        unassignedOrders,
        pending: accountMode && displayed.size > 0 && !accountsReady,
    };
}
