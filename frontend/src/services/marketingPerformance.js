import api from "../lib/api";
import { getAdsManagerOverview } from "./adsManager";
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

export function isMarketingPerformanceProvider(value) {
    return MARKETING_PLATFORMS.includes(String(value || "").trim());
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
    };
}

function normalizeCampaign(value = {}) {
    const campaignId = nullableText(value.campaign_id);
    if (!campaignId) return null;
    return {
        account_id: nullableText(value.account_id),
        account_name: text(value.account_name, value.account_id || "حساب غير معروف"),
        campaign_id: campaignId,
        campaign_name: text(value.campaign_name, campaignId),
        status: text(value.status, "unknown"),
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
}

function normalizeAccount(value = {}) {
    const accountId = nullableText(value.account_id);
    if (!accountId) return null;
    return {
        account_id: accountId,
        account_name: text(value.account_name, accountId),
        currency: nullableText(value.currency),
        timezone: nullableText(value.timezone),
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

export function normalizeSnapchatMarketingWorkspace(payload = {}) {
    const source = payload?.data && typeof payload.data === "object"
        ? payload.data
        : payload;
    const value = source && typeof source === "object" ? source : {};
    const campaigns = Array.isArray(value.campaigns)
        ? value.campaigns.map(normalizeCampaign).filter(Boolean)
        : [];
    const accounts = Array.isArray(value.accounts)
        ? value.accounts.map(normalizeAccount).filter(Boolean)
        : [];
    return {
        platform: "snapchat",
        label: MARKETING_PLATFORM_CONFIG.snapchat.label,
        range: {
            date_from: ISO_DATE_RE.test(value.range?.date_from || "")
                ? value.range.date_from
                : null,
            date_to: ISO_DATE_RE.test(value.range?.date_to || "")
                ? value.range.date_to
                : null,
            timezone: text(value.range?.timezone, "Asia/Riyadh"),
        },
        connection: {
            status: text(value.connection?.status, "unknown"),
            provenance: text(value.connection?.provenance, "unknown"),
            last_sync_at: nullableText(value.connection?.last_sync_at),
            data_delay_minutes: number(value.connection?.data_delay_minutes, { min: 0 }),
            health_status: text(value.connection?.health_status, "unknown"),
            health_score: number(value.connection?.health_score, { min: 0 }),
            accounts_count: number(value.connection?.accounts_count, { min: 0, integer: true }) || 0,
        },
        totals: normalizeTotals(value.totals),
        daily: Array.isArray(value.daily)
            ? value.daily
                .filter((row) => ISO_DATE_RE.test(row?.date || ""))
                .map((row) => ({ date: row.date, ...normalizeTotals(row) }))
            : [],
        accounts,
        campaigns,
        campaign_pagination: {
            page: number(value.campaign_pagination?.page, { min: 1, integer: true }) || 1,
            limit: number(value.campaign_pagination?.limit, { min: 10, integer: true }) || 25,
            total: number(value.campaign_pagination?.total, { min: 0, integer: true }) || 0,
            pages: number(value.campaign_pagination?.pages, { min: 0, integer: true }) || 0,
        },
        source: {
            performance_collection: text(value.source?.performance_collection),
            entity_collection: text(value.source?.entity_collection),
            attribution_model: text(value.source?.attribution_model),
            performance_rows: number(value.source?.performance_rows, { min: 0, integer: true }) || 0,
            entity_rows: number(value.source?.entity_rows, { min: 0, integer: true }) || 0,
            identity_matches: number(value.source?.identity_matches, { min: 0, integer: true }) || 0,
            identity_coverage_pct: number(value.source?.identity_coverage_pct, { min: 0 }),
            row_limit_reached: value.source?.row_limit_reached === true,
            entity_limit_reached: value.source?.entity_limit_reached === true,
        },
        ai_readiness: {
            report_ready: value.ai_readiness?.report_ready === true,
            campaign_identity_ready: value.ai_readiness?.campaign_identity_ready === true,
            spend_ready: value.ai_readiness?.spend_ready === true,
            orders_ready: value.ai_readiness?.orders_ready === true,
            sales_ready: value.ai_readiness?.sales_ready === true,
            ratios_ready: value.ai_readiness?.ratios_ready === true,
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

function adaptAdsManager(platform, overview) {
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
        accounts: [],
        campaigns: (overview.campaigns || [])
            .filter((row) => row.provider === config.adsProvider)
            .map((row) => normalizeCampaign({
                ...row,
                spend_sar: row.spend_sar_equivalent,
                sales_sar: row.revenue_sar_equivalent,
                orders: row.purchases,
                swipes: row.clicks,
                status: "unknown",
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
        connection: {
            status: integration?.connection_status || "not_configured",
            provenance: integration?.connection_provenance || "disconnected",
            last_sync_at: integration?.last_sync_at || null,
            data_delay_minutes: integration?.data_delay_minutes ?? null,
            health_status: integration?.health?.status || "unknown",
            health_score: integration?.health?.score ?? null,
            accounts_count: integration?.accounts?.length || 0,
        },
        totals: normalizeTotals({}),
        daily: [],
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
} = {}) {
    if (!isMarketingPerformanceProvider(platform)) {
        throw new Error("invalid_marketing_platform");
    }
    if (platform === "snapchat") {
        const response = await api.get("/ads-manager/snapchat-workspace", {
            params: {
                date_from: ISO_DATE_RE.test(dateFrom || "") ? dateFrom : undefined,
                date_to: ISO_DATE_RE.test(dateTo || "") ? dateTo : undefined,
                campaign_query: String(campaignQuery || "").trim().slice(0, 120) || undefined,
                page,
                limit,
            },
        });
        return normalizeSnapchatMarketingWorkspace(response.data);
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
