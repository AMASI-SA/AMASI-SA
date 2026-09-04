import api from "../lib/api";
import { getAdsManagerOverview } from "./adsManager";
import { retryAdsRead } from "./adsReadRetry";
import { getIntegrationsOverview } from "./integrationsV2";

export const MARKETING_PLATFORMS = Object.freeze([
    "snapchat",
    "tiktok",
    "meta",
    "google",
]);

export const MARKETING_PLATFORM_CONFIG = Object.freeze({
    snapchat: {
        label: "سناب شات",
        integrationProvider: "snapchat_ads",
        adsProvider: "snapchat",
    },
    tiktok: {
        label: "تيك توك",
        integrationProvider: "tiktok_ads",
        adsProvider: "tiktok",
    },
    meta: {
        label: "ميتا",
        integrationProvider: "meta_ads",
        adsProvider: "meta",
    },
    google: {
        label: "إعلانات Google",
        integrationProvider: "google_ads",
        adsProvider: null,
    },
});

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const HOUR_RE = /^(?:[01]\d|2[0-3]):00$/;

export function isMarketingPerformanceProvider(value) {
    return MARKETING_PLATFORMS.includes(String(value || "").trim());
}

export function clampSnapchatRangeToAccountToday(
    { dateFrom, dateTo } = {},
    accountLocalToday,
) {
    if (!ISO_DATE_RE.test(accountLocalToday || "")) {
        return { dateFrom, dateTo };
    }
    return {
        dateFrom: ISO_DATE_RE.test(dateFrom || "") && dateFrom > accountLocalToday
            ? accountLocalToday
            : dateFrom,
        dateTo: ISO_DATE_RE.test(dateTo || "") && dateTo > accountLocalToday
            ? accountLocalToday
            : dateTo,
    };
}

export function snapchatAccountLocalToday(integration = {}) {
    const accountDays = (integration.accounts || [])
        .map((account) => account?.local_today)
        .filter((value) => ISO_DATE_RE.test(value || ""))
        .sort();
    return accountDays[0] || null;
}

function text(value, fallback = "") {
    return typeof value === "string" ? value : fallback;
}

function nullableText(value) {
    const result = text(value).trim();
    return result || null;
}

function number(value, { min = null, integer = false } = {}) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return null;
    if (min !== null && parsed < min) return null;
    return integer ? Math.trunc(parsed) : parsed;
}

function normalizeTotals(value = {}) {
    return {
        spend_sar: number(value.spend_sar, { min: 0 }),
        sales_sar: number(value.sales_sar, { min: 0 }),
        orders: number(value.orders, { min: 0, integer: true }),
        impressions: number(value.impressions, { min: 0, integer: true }),
        swipes: number(value.swipes, { min: 0, integer: true }),
        video_views: number(value.video_views, { min: 0, integer: true }),
        view_content: number(value.view_content, { min: 0, integer: true }),
        add_to_cart: number(value.add_to_cart, { min: 0, integer: true }),
        start_checkout: number(value.start_checkout, { min: 0, integer: true }),
        add_billing: number(value.add_billing, { min: 0, integer: true }),
        paid_reach: number(value.paid_reach, { min: 0, integer: true }),
        paid_frequency: number(value.paid_frequency, { min: 0 }),
        reach_frequency_scope: nullableText(value.reach_frequency_scope),
        roas: number(value.roas, { min: 0 }),
        cpa_sar: number(value.cpa_sar, { min: 0 }),
        cpc_sar: number(value.cpc_sar, { min: 0 }),
        cpm_sar: number(value.cpm_sar, { min: 0 }),
        ctr_pct: number(value.ctr_pct, { min: 0 }),
        observed_days: number(value.observed_days, { min: 0, integer: true }) || 0,
        source_rows: number(value.source_rows, { min: 0, integer: true }) || 0,
        last_observed_at: nullableText(value.last_observed_at),
        last_observed_date: nullableText(value.last_observed_date),
        data_complete: value.data_complete === true,
        salla_total_orders: number(value.salla_total_orders, { min: 0, integer: true }),
        salla_matched_orders: number(value.salla_matched_orders, { min: 0, integer: true }),
        salla_unmatched_orders: number(value.salla_unmatched_orders, { min: 0, integer: true }),
        salla_sales_sar: number(value.salla_sales_sar, { min: 0 }),
        snapchat_purchases: number(value.snapchat_purchases, { min: 0, integer: true }),
        snapchat_purchase_value_sar: number(value.snapchat_purchase_value_sar, { min: 0 }),
        snapchat_spend_sar: number(value.snapchat_spend_sar, { min: 0 }),
        salla_roas: number(value.salla_roas, { min: 0 }),
        snapchat_roas: number(value.snapchat_roas, { min: 0 }),
        salla_cpa_sar: number(value.salla_cpa_sar, { min: 0 }),
        snapchat_cpa_sar: number(value.snapchat_cpa_sar, { min: 0 }),
    };
}

function normalizeHourly(value = {}) {
    const hour = HOUR_RE.test(value.hour || "")
        ? value.hour
        : `${String(number(value.hour_index, { min: 0, integer: true }) || 0).padStart(2, "0")}:00`;
    return {
        date: ISO_DATE_RE.test(value.date || "") ? value.date : null,
        hour,
        hour_index: number(value.hour_index, { min: 0, integer: true }) || 0,
        spend_sar: number(value.spend_sar, { min: 0 }) || 0,
        sales_sar: number(value.sales_sar, { min: 0 }) || 0,
        orders: number(value.orders, { min: 0, integer: true }) || 0,
        roas: number(value.roas, { min: 0 }),
        cpa_sar: number(value.cpa_sar, { min: 0 }),
        observed: value.observed === true,
        is_future: value.is_future === true,
        result_source: text(value.result_source, "platform"),
    };
}

function normalizeCampaign(value = {}) {
    const campaignId = nullableText(value.campaign_id);
    if (!campaignId) return null;
    const campaign = {
        account_id: nullableText(value.account_id),
        account_name: text(value.account_name, value.account_id || "حساب غير معروف"),
        campaign_id: campaignId,
        campaign_name: text(value.campaign_name, campaignId),
        status: text(value.status, "unknown"),
        data_status: nullableText(value.data_status),
        campaign_active: value.campaign_active === true,
        delivery_status: nullableText(value.delivery_status),
        objective: nullableText(value.objective),
        start_time: nullableText(value.start_time),
        end_time: nullableText(value.end_time),
        budget: {
            currency: nullableText(value.budget?.currency),
            daily_native: number(value.budget?.daily_native, { min: 0 }),
            lifetime_native: number(value.budget?.lifetime_native, { min: 0 }),
        },
        ...normalizeTotals(value),
    };
    if (value.salla_results && typeof value.salla_results === "object") {
        campaign.salla_results = value.salla_results;
    }
    if (value.profitability && typeof value.profitability === "object") {
        campaign.profitability = value.profitability;
    }
    if (value.salla_profitability && typeof value.salla_profitability === "object") {
        campaign.salla_profitability = value.salla_profitability;
    } else if (campaign.profitability) {
        campaign.salla_profitability = campaign.profitability;
    }
    campaign.cost_status = nullableText(value.cost_status);
    return campaign;
}

function withoutSnapchatCommercialAliases(value = {}) {
    return {
        ...value,
        orders: null,
        sales_sar: null,
        roas: null,
        cpa_sar: null,
    };
}

function normalizeAccount(value = {}) {
    const accountId = nullableText(value.account_id);
    if (!accountId) return null;
    return {
        account_id: accountId,
        account_name: text(value.account_name, accountId),
        currency: nullableText(value.currency),
        timezone: nullableText(value.timezone),
        local_today: ISO_DATE_RE.test(value.local_today || "")
            ? value.local_today
            : null,
        ...normalizeTotals(value),
    };
}

function normalizeInsight(value = {}) {
    return {
        code: text(value.code, "insight"),
        severity: ["info", "warning", "critical"].includes(value.severity)
            ? value.severity
            : "info",
        title: text(value.title, "ملاحظة تحليلية"),
        detail: text(value.detail),
        campaign_id: nullableText(value.campaign_id),
    };
}

function connectionFromIntegration(integration = {}) {
    return {
        status: text(integration.connection_status, "unknown"),
        provenance: text(integration.connection_provenance, "unknown"),
        last_sync_at: nullableText(integration.last_sync_at),
        data_delay_minutes: number(integration.data_delay_minutes, { min: 0 }),
        health_status: text(integration.health?.status, "unknown"),
        health_score: number(integration.health?.score, { min: 0 }),
        accounts_count: Array.isArray(integration.accounts)
            ? integration.accounts.length
            : 0,
    };
}

export function normalizeSnapchatMarketingWorkspace(payload = {}, integration = {}) {
    const source = payload?.data && typeof payload.data === "object"
        ? payload.data
        : payload;
    const value = source && typeof source === "object" ? source : {};
    return {
        platform: "snapchat",
        label: MARKETING_PLATFORM_CONFIG.snapchat.label,
        result_source: text(value.result_source, "salla"),
        request_id: nullableText(value.request_id),
        selected_account_id: nullableText(value.selected_account_id),
        selected_account_name: nullableText(value.selected_account_name),
        salla_as_of: nullableText(value.salla_as_of),
        snapchat_as_of: nullableText(value.snapchat_as_of),
        salla_status: text(value.salla_status, "failed"),
        snapchat_status: text(value.snapchat_status, "failed"),
        matching_status: text(value.matching_status, "failed"),
        reconciliation_status: text(value.reconciliation_status, "unreconciled"),
        reconciliation_reasons: Array.isArray(value.reconciliation_reasons)
            ? value.reconciliation_reasons.filter((item) => typeof item === "string")
            : [],
        reconciliation: value.reconciliation && typeof value.reconciliation === "object"
            ? value.reconciliation
            : null,
        coverage_reasons: value.coverage_reasons && typeof value.coverage_reasons === "object"
            ? value.coverage_reasons
            : {},
        action_report_time: text(value.action_report_time, "conversion"),
        supported_action_report_times: Array.isArray(value.supported_action_report_times)
            ? value.supported_action_report_times.filter((item) => ["conversion", "impression"].includes(item))
            : ["conversion", "impression"],
        range: {
            date_from: ISO_DATE_RE.test(value.date_from || "")
                ? value.date_from
                : null,
            date_to: ISO_DATE_RE.test(value.date_to || "")
                ? value.date_to
                : null,
            timezone: text(value.business_timezone, "Asia/Riyadh"),
            effective_timezone: text(value.effective_timezone, "Asia/Riyadh"),
            snapchat_account_timezone: nullableText(value.account_timezone),
            salla_attribution_timezone: text(value.salla_attribution_timezone, "Asia/Riyadh"),
        },
        connection: connectionFromIntegration(integration),
        totals: withoutSnapchatCommercialAliases({
            ...normalizeTotals(value.totals),
            ...(value.totals?.salla_profitability && typeof value.totals.salla_profitability === "object"
                ? { salla_profitability: value.totals.salla_profitability }
                : value.totals?.profitability && typeof value.totals.profitability === "object"
                    ? { salla_profitability: value.totals.profitability }
                : {}),
        }),
        daily: Array.isArray(value.daily)
            ? value.daily
                .filter((row) => ISO_DATE_RE.test(row?.date || ""))
                .map((row) => withoutSnapchatCommercialAliases({
                    date: row.date,
                    ...normalizeTotals(row),
                }))
            : [],
        hourly: Array.isArray(value.hourly)
            ? value.hourly.map(normalizeHourly).sort((left, right) => left.hour_index - right.hour_index)
            : [],
        accounts: Array.isArray(value.accounts)
            ? value.accounts.map(normalizeAccount).filter(Boolean)
                .map(withoutSnapchatCommercialAliases)
            : [],
        campaigns: Array.isArray(value.campaigns)
            ? value.campaigns.map(normalizeCampaign).filter(Boolean)
                .map(withoutSnapchatCommercialAliases)
            : [],
        campaign_pagination: {
            page: number(value.campaign_pagination?.page, { min: 1, integer: true }) || 1,
            limit: number(value.campaign_pagination?.limit, { min: 10, integer: true }) || 25,
            total: number(value.campaign_pagination?.total, { min: 0, integer: true }) || 0,
            pages: number(value.campaign_pagination?.pages, { min: 0, integer: true }) || 0,
        },
        source: {
            performance_collection: text(value.source?.performance_collection),
            hourly_collection: text(value.source?.hourly_collection),
            hourly_source_mode: text(value.source?.hourly_source_mode),
            hourly_rows: number(value.source?.hourly_rows, { min: 0, integer: true }) || 0,
            hourly_available: value.source?.hourly_available === true,
            entity_collection: text(value.source?.entity_collection),
            attribution_model: text(value.source?.attribution_model),
            selected_account_count: number(value.source?.selected_account_count, { min: 0, integer: true }) || 0,
            performance_rows: number(value.source?.performance_rows, { min: 0, integer: true }) || 0,
            entity_rows: number(value.source?.entity_rows, { min: 0, integer: true }) || 0,
            identity_matches: number(value.source?.identity_matches, { min: 0, integer: true }) || 0,
            identity_coverage_pct: number(value.source?.identity_coverage_pct, { min: 0 }),
            row_limit_reached: value.source?.row_limit_reached === true,
            entity_limit_reached: value.source?.entity_limit_reached === true,
            platform_total_snapshot_ready: value.source?.platform_total_snapshot_ready === true,
            platform_direct_account_total_ready: value.source?.platform_direct_account_total_ready === true,
            platform_action_report_time: nullableText(value.source?.platform_action_report_time),
            account_local_action_report_time: nullableText(value.source?.account_local_action_report_time),
            account_spend_source: nullableText(value.source?.account_spend_source),
            account_commercial_totals_source: nullableText(
                value.source?.account_commercial_totals_source,
            ),
            requested_campaign_diagnostic: (
                value.source?.requested_campaign_diagnostic
                && typeof value.source.requested_campaign_diagnostic === "object"
            ) ? {
                campaign_id: nullableText(value.source.requested_campaign_diagnostic.campaign_id),
                reason: nullableText(value.source.requested_campaign_diagnostic.reason),
                selected_account_id: nullableText(
                    value.source.requested_campaign_diagnostic.selected_account_id,
                ),
                evidence_account_id: nullableText(
                    value.source.requested_campaign_diagnostic.evidence_account_id,
                ),
            } : null,
            campaign_exclusions: Array.isArray(value.source?.campaign_exclusions)
                ? value.source.campaign_exclusions.slice(0, 500).map((item) => ({
                    campaign_id: nullableText(item?.campaign_id),
                    reason: nullableText(item?.reason),
                })).filter((item) => item.campaign_id && item.reason)
                : [],
        },
        ai_readiness: {
            report_ready: value.ai_readiness?.report_ready === true,
            campaign_identity_ready: value.ai_readiness?.campaign_identity_ready === true,
            spend_ready: value.ai_readiness?.spend_ready === true,
            orders_ready: value.ai_readiness?.orders_ready === true,
            sales_ready: value.ai_readiness?.sales_ready === true,
            ratios_ready: value.ai_readiness?.ratios_ready === true,
            funnel_ready: value.ai_readiness?.funnel_ready === true,
            ai_analysis_ready: value.ai_readiness?.ai_analysis_ready === true,
            campaign_creation_enabled: false,
            campaign_management_enabled: false,
            required_lifecycle: Array.isArray(value.ai_readiness?.required_lifecycle)
                ? value.ai_readiness.required_lifecycle.filter((item) => typeof item === "string")
                : [],
        },
        insights: Array.isArray(value.insights)
            ? value.insights.map(normalizeInsight).slice(0, 10)
            : [],
        policy: {
            mode: "observe_only",
            mutations_allowed: false,
        },
    };
}

export function adaptAdsManager(platform, overview) {
    const config = MARKETING_PLATFORM_CONFIG[platform];
    const provider = overview.providers?.find(
        (row) => row.provider === config.adsProvider,
    ) || null;
    const metrics = provider?.metrics || {};
    return {
        platform,
        label: config.label,
        range: overview.range,
        connection: {
            status: provider?.connection_status || "unknown",
            provenance: provider?.connection_provenance || "unknown",
            last_sync_at: provider?.last_sync_at || null,
            data_delay_minutes: provider?.freshness?.data_delay_minutes ?? null,
            health_status: provider?.health_status || "unknown",
            health_score: provider?.health_score ?? null,
            accounts_count: provider?.account_performance_coverage?.length || 0,
        },
        totals: normalizeTotals({
            spend_sar: metrics.provider_reported_spend_sar,
            sales_sar: metrics.platform_attributed_revenue_sar,
            orders: metrics.platform_reported_purchases,
            impressions: metrics.platform_reported_impressions,
            swipes: metrics.platform_reported_clicks,
            roas: metrics.platform_roas,
            cpa_sar: metrics.platform_cpa_sar,
            cpc_sar: metrics.platform_cpc_sar,
            cpm_sar: metrics.platform_cpm_sar,
            ctr_pct: metrics.platform_ctr_pct,
            observed_days: provider?.freshness?.observed_days,
            last_observed_at: provider?.freshness?.last_observed_at,
            data_complete: provider?.performance_coverage?.status === "complete",
        }),
        daily: (overview.daily_spend || []).map((row) => ({
            date: row.date,
            ...normalizeTotals({ spend_sar: row[config.adsProvider] }),
        })),
        hourly: [],
        accounts: [],
        campaigns: (overview.campaigns || [])
            .filter((row) => row.provider === config.adsProvider)
            .map((row) => normalizeCampaign({
                ...row,
                spend_sar: row.spend_sar_equivalent,
                sales_sar: row.revenue_sar_equivalent,
                orders: row.purchases,
                swipes: row.clicks,
                status: row.status || "unknown",
                delivery_status: row.delivery_status || null,
                objective: row.objective || null,
                start_time: row.start_time || null,
                end_time: row.end_time || null,
                budget: row.budget || {
                    currency: row.spend_currency || null,
                    daily_native: null,
                    lifetime_native: null,
                },
            }))
            .filter(Boolean),
        campaign_pagination: overview.campaign_pagination,
        source: {
            performance_rows: provider?.campaign_coverage?.source_rows || 0,
            identity_coverage_pct: provider?.campaign_coverage?.status === "available" ? 100 : null,
            row_limit_reached: (overview.coverage?.source_row_limit_reached || []).length > 0,
            entity_limit_reached: false,
        },
        ai_readiness: {
            report_ready: provider?.performance_coverage?.status === "complete",
            campaign_identity_ready: provider?.campaign_coverage?.status === "available",
            spend_ready: metrics.provider_reported_spend_sar !== null,
            orders_ready: metrics.platform_reported_purchases !== null,
            sales_ready: metrics.platform_attributed_revenue_sar !== null,
            ratios_ready: provider?.performance_coverage?.eligible_for_ratios === true,
            ai_analysis_ready: provider?.performance_coverage?.eligible_for_ratios === true,
            campaign_creation_enabled: false,
            campaign_management_enabled: false,
            required_lifecycle: [
                "proposal", "preview", "approval", "execution",
                "verification", "audit", "rollback",
            ],
        },
        insights: overview.insights || [],
        policy: { mode: "observe_only", mutations_allowed: false },
    };
}

async function googleWorkspace() {
    const overview = await getIntegrationsOverview();
    const integration = overview.providers.find((row) => row.provider === "google_ads");
    return {
        platform: "google",
        label: MARKETING_PLATFORM_CONFIG.google.label,
        range: { date_from: null, date_to: null, timezone: "Asia/Riyadh" },
        connection: connectionFromIntegration(integration),
        totals: normalizeTotals({}),
        daily: [],
        hourly: [],
        accounts: [],
        campaigns: [],
        campaign_pagination: { page: 1, limit: 25, total: 0, pages: 0 },
        source: {
            performance_rows: 0,
            identity_coverage_pct: null,
            row_limit_reached: false,
            entity_limit_reached: false,
        },
        ai_readiness: {
            report_ready: false,
            campaign_identity_ready: false,
            spend_ready: false,
            orders_ready: false,
            sales_ready: false,
            ratios_ready: false,
            ai_analysis_ready: false,
            campaign_creation_enabled: false,
            campaign_management_enabled: false,
            required_lifecycle: [
                "proposal", "preview", "approval", "execution",
                "verification", "audit", "rollback",
            ],
        },
        insights: [],
        policy: { mode: "observe_only", mutations_allowed: false },
    };
}

export async function getMarketingPerformance({
    platform,
    dateFrom,
    dateTo,
    campaignQuery = "",
    page = 1,
    limit = 25,
    activeCampaignsOnly = true,
    actionReportTime = "conversion",
    resultSource = "salla",
    requestId,
} = {}) {
    if (!isMarketingPerformanceProvider(platform)) {
        throw new Error("invalid_marketing_platform");
    }
    if (platform === "snapchat") {
        const integrations = await retryAdsRead(() => getIntegrationsOverview());
        const integration = integrations.providers.find(
            (row) => row.provider === "snapchat_ads",
        ) || {};
        const accountLocalToday = snapchatAccountLocalToday(integration);
        const requestRange = { dateFrom, dateTo };
        const reportResponse = await retryAdsRead(() => api.get(
            "/integrations-v2/snapchat_ads/campaign-report",
            {
                params: {
                    request_id: String(requestId || "").trim().slice(0, 120) || undefined,
                    from_date: ISO_DATE_RE.test(requestRange.dateFrom || "")
                        ? requestRange.dateFrom
                        : undefined,
                    to_date: ISO_DATE_RE.test(requestRange.dateTo || "")
                        ? requestRange.dateTo
                        : undefined,
                    campaign_query: String(campaignQuery || "").trim().slice(0, 120) || undefined,
                    page,
                    limit,
                    active_campaigns_only: activeCampaignsOnly,
                    result_source: ["salla", "platform"].includes(resultSource)
                        ? resultSource
                        : "salla",
                    action_report_time: ["conversion", "impression"].includes(actionReportTime)
                        ? actionReportTime
                        : "conversion",
                },
            },
        ));
        const normalized = normalizeSnapchatMarketingWorkspace(
            reportResponse.data,
            integration,
        );
        return {
            ...normalized,
            account_local_today: accountLocalToday,
            range: {
                ...normalized.range,
                date_from: normalized.range.date_from || requestRange.dateFrom,
                date_to: normalized.range.date_to || requestRange.dateTo,
            },
        };
    }
    if (platform === "google") return googleWorkspace();
    const config = MARKETING_PLATFORM_CONFIG[platform];
    const overview = await getAdsManagerOverview({
        dateFrom,
        dateTo,
        provider: config.adsProvider,
        campaignQuery,
        page,
        limit,
    });
    return adaptAdsManager(platform, overview);
}
