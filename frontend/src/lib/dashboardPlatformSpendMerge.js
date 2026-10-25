const PROVIDERS = Object.freeze(["snapchat", "meta", "tiktok", "google"]);

function finiteNumber(value) {
    if (value === null || value === undefined || typeof value === "boolean"
        || !(typeof value === "number" || typeof value === "string" && value.trim() !== "")) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function nonnegative(value) {
    const parsed = finiteNumber(value);
    return parsed !== null && parsed >= 0 ? parsed : 0;
}

function roundMoney(value) {
    return Math.round(nonnegative(value) * 100) / 100;
}

function providerTotals(platformSpend = {}) {
    const source = platformSpend?.provider_totals_sar || {};
    return Object.fromEntries(
        PROVIDERS.map((provider) => {
            const value = finiteNumber(source[provider]);
            return [provider, value !== null && value >= 0 ? roundMoney(value) : null];
        }),
    );
}

function availablePlatformSpend(platformSpend) {
    const quality = platformSpend?.spend_quality || {};
    const total = finiteNumber(platformSpend?.total_sar);
    return total !== null && total >= 0
        && (quality.amount_available === true || quality.amount_complete === true)
        && quality.status !== "incomplete";
}

function provisionalCanonicalSpend(dashboardPayload) {
    const quality = dashboardPayload?.ads_v2?.spend_quality || {};
    return quality.provisional === true || quality.status === "provisional";
}

function unknownSnapchatMetrics(dashboardPayload) {
    const snapchat = dashboardPayload?.ads_v2?.providers?.snapchat;
    const executive = dashboardPayload?.ads_v2?.executive_breakdown?.providers?.snapchat;
    return Boolean(snapchat && (snapchat.platform_reported_metrics_complete === false
        || ["orders", "revenue", "impressions", "clicks"].some((field) =>
            Object.hasOwn(snapchat, field) && finiteNumber(snapchat[field]) === null)))
        || Boolean(executive && Object.hasOwn(executive, "platform_reported_orders")
            && finiteNumber(executive.platform_reported_orders) === null);
}

export function needsCanonicalDashboardSpendRefresh(dashboardPayload, platformSpend) {
    if (!availablePlatformSpend(platformSpend)) return false;
    const total = finiteNumber(dashboardPayload?.totals?.total_ads_cost);
    const quality = dashboardPayload?.ads_v2?.spend_quality || {};
    return total === null || total < 0
        || dashboardPayload?.totals?.ads_spend_amount_available === false
        || quality.amount_available === false || quality.status === "incomplete"
        || (provisionalCanonicalSpend(dashboardPayload)
            && platformSpend.spend_quality?.status === "complete"
            && platformSpend.spend_quality?.amount_complete === true);
}

function mergeProviderMetrics(existing = {}, spend = 0) {
    const orders = nonnegative(existing.orders);
    const revenue = nonnegative(existing.revenue);
    return {
        ...existing,
        spend,
        cpa: orders > 0 ? roundMoney(spend / orders) : 0,
        cost_per_order: orders > 0 && spend > 0
            ? roundMoney(spend / orders)
            : null,
        roas: spend > 0 ? Math.round((revenue / spend) * 100) / 100 : 0,
    };
}

function mergeExecutiveBreakdown(existing = {}, spendByProvider = {}) {
    const currentProviders = existing?.providers || {};
    const providers = {};
    let totalSpend = 0;
    let totalSallaOrders = 0;
    let totalSallaSales = 0;
    let totalPlatformOrders = 0;
    let completePlatformDenominator = true;

    PROVIDERS.forEach((provider) => {
        const current = currentProviders[provider] || {};
        const spend = roundMoney(spendByProvider[provider]);
        const sallaOrders = Math.max(0, Math.trunc(nonnegative(current.salla_orders)));
        const sallaSales = roundMoney(current.salla_sales_sar);
        const reportedOrdersRaw = finiteNumber(current.platform_reported_orders);
        const reportedOrders = provider === "google"
            ? null
            : (reportedOrdersRaw === null
                ? 0
                : Math.max(0, Math.trunc(reportedOrdersRaw)));

        if (provider === "google" && spend > 0) {
            completePlatformDenominator = false;
        }
        if (reportedOrders !== null) totalPlatformOrders += reportedOrders;
        totalSpend += spend;
        totalSallaOrders += sallaOrders;
        totalSallaSales += sallaSales;

        providers[provider] = {
            ...current,
            provider,
            spend_sar: spend,
            salla_orders: sallaOrders,
            salla_sales_sar: sallaSales,
            platform_reported_orders: reportedOrders,
            platform_cost_per_order_sar: (
                reportedOrders !== null && reportedOrders > 0 && spend > 0
                    ? roundMoney(spend / reportedOrders)
                    : null
            ),
            actual_roas: spend > 0
                ? Math.round((sallaSales / spend) * 100) / 100
                : null,
        };
    });

    totalSpend = roundMoney(totalSpend);
    totalSallaSales = roundMoney(totalSallaSales);
    return {
        ...existing,
        providers,
        total: {
            ...(existing?.total || {}),
            spend_sar: totalSpend,
            salla_orders: totalSallaOrders,
            salla_sales_sar: totalSallaSales,
            platform_reported_orders: completePlatformDenominator
                ? totalPlatformOrders
                : null,
            platform_cost_per_order_sar: (
                completePlatformDenominator
                && totalSpend > 0
                && totalPlatformOrders > 0
                    ? roundMoney(totalSpend / totalPlatformOrders)
                    : null
            ),
            actual_roas: totalSpend > 0
                ? Math.round((totalSallaSales / totalSpend) * 100) / 100
                : null,
        },
        coverage: {
            ...(existing?.coverage || {}),
            platform_cpa_denominator_complete: completePlatformDenominator,
        },
        source_contract: {
            ...(existing?.source_contract || {}),
            spend: "dashboard_four_platform_spend_v1:selected_period",
        },
    };
}

export function mergeDashboardWithPlatformSpend(
    dashboardPayload = {},
    platformSpend = {},
) {
    const totals = dashboardPayload?.totals && typeof dashboardPayload.totals === "object"
        ? dashboardPayload.totals
        : {};
    // A partial known subtotal never supplies a full expense or profit base.
    // Provisional finances remain backend-owned, including account bank fees.
    // The page loader can request one new canonical snapshot when the complete
    // provisional portfolio becomes available after this snapshot was built.
    if (!availablePlatformSpend(platformSpend)
        || needsCanonicalDashboardSpendRefresh(dashboardPayload, platformSpend)
        || provisionalCanonicalSpend(dashboardPayload)
        || unknownSnapchatMetrics(dashboardPayload)
        || platformSpend?.spend_quality?.provisional === true
        || platformSpend?.spend_quality?.status === "provisional"
        || Object.values(providerTotals(platformSpend)).some((value) => value === null)) {
        return { ...dashboardPayload, dashboard_platform_spend: platformSpend };
    }
    const spendByProvider = providerTotals(platformSpend);
    const platformTotal = finiteNumber(platformSpend?.total_sar);
    const newAdsTotal = roundMoney(platformTotal);
    const oldAdsTotal = roundMoney(totals.total_ads_cost);
    const adsDelta = oldAdsTotal - newAdsTotal;
    const sales = finiteNumber(totals.total_sales);
    const productCost = finiteNumber(totals.total_product_cost);
    const orders = Math.max(0, Math.trunc(nonnegative(totals.total_orders)));
    const nextTotals = {
        ...totals,
        total_ads_cost: newAdsTotal,
        daily_ads_total: newAdsTotal,
        daily_costs_total: productCost !== null && productCost >= 0
            ? roundMoney(productCost + newAdsTotal) : null,
        overall_roas: newAdsTotal > 0 && sales !== null
            ? Math.round((sales / newAdsTotal) * 100) / 100
            : null,
        avg_cost_per_order: newAdsTotal > 0 && orders > 0
            ? roundMoney(newAdsTotal / orders)
            : null,
        snapchat_spend: spendByProvider.snapchat,
        meta_spend: spendByProvider.meta,
        tiktok_spend: spendByProvider.tiktok,
        google_ads_spend: spendByProvider.google,
    };

    if (finiteNumber(totals.net_profit) !== null) {
        nextTotals.net_profit = Math.round(
            (Number(totals.net_profit) + adsDelta) * 100,
        ) / 100;
    }
    const config = dashboardPayload?.net_sales_config || {};
    if (
        config.deduct_ads !== false
        && finiteNumber(totals.net_sales) !== null
    ) {
        nextTotals.net_sales = Math.round(
            (Number(totals.net_sales) + adsDelta) * 100,
        ) / 100;
    }

    const existingAds = dashboardPayload?.ads_v2
        && typeof dashboardPayload.ads_v2 === "object"
        ? dashboardPayload.ads_v2
        : {};
    const existingProviders = existingAds.providers || {};
    const nextProviders = Object.fromEntries(
        PROVIDERS.map((provider) => [
            provider,
            mergeProviderMetrics(existingProviders[provider], spendByProvider[provider]),
        ]),
    );
    const nextBreakdown = {
        ...(existingAds.breakdown || {}),
        snapchat: spendByProvider.snapchat,
        meta: spendByProvider.meta,
        tiktok: spendByProvider.tiktok,
        google: spendByProvider.google,
        google_transitional: spendByProvider.google,
    };

    return {
        ...dashboardPayload,
        totals: nextTotals,
        ads_v2: {
            ...existingAds,
            total: newAdsTotal,
            breakdown: nextBreakdown,
            providers: nextProviders,
            executive_breakdown: mergeExecutiveBreakdown(
                existingAds.executive_breakdown || {},
                spendByProvider,
            ),
            platform_spend_period: {
                date_from: platformSpend?.date_from || null,
                date_to: platformSpend?.date_to || null,
                timezone: platformSpend?.timezone || "Asia/Riyadh",
            },
            source_contract: {
                ...(existingAds.source_contract || {}),
                dashboard_total: "dashboard_four_platform_spend_v1:selected_period",
            },
        },
        dashboard_platform_spend: platformSpend,
        source_contract: {
            ...(dashboardPayload?.source_contract || {}),
            advertising: "dashboard_four_platform_spend_v1:selected_period",
        },
    };
}

export { PROVIDERS as DASHBOARD_EXECUTIVE_AD_PROVIDERS };
