const PROVIDERS = Object.freeze(["snapchat", "meta", "tiktok", "google"]);

function finiteNumber(value) {
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
        PROVIDERS.map((provider) => [provider, roundMoney(source[provider])]),
    );
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
    const spendByProvider = providerTotals(platformSpend);
    const summedSpend = roundMoney(
        PROVIDERS.reduce((sum, provider) => sum + spendByProvider[provider], 0),
    );
    const platformTotal = finiteNumber(platformSpend?.total_sar);
    const newAdsTotal = platformTotal !== null
        ? roundMoney(platformTotal)
        : summedSpend;
    const oldAdsTotal = roundMoney(totals.total_ads_cost);
    const adsDelta = oldAdsTotal - newAdsTotal;
    const sales = nonnegative(totals.total_sales);
    const orders = Math.max(0, Math.trunc(nonnegative(totals.total_orders)));
    const nextTotals = {
        ...totals,
        total_ads_cost: newAdsTotal,
        daily_ads_total: newAdsTotal,
        daily_costs_total: roundMoney(
            nonnegative(totals.total_product_cost) + newAdsTotal,
        ),
        overall_roas: newAdsTotal > 0
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
