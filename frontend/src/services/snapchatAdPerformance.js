import api from "../lib/api";

export const SNAPCHAT_ENTITY_PAGE_SIZE = 9;

function number(value, { integer = false } = {}) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return null;
    return integer ? Math.trunc(parsed) : parsed;
}

function text(value, fallback = "") {
    return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function metrics(value = {}) {
    return {
        spend_sar: number(value.spend_sar),
        spend_native: number(value.spend_native),
        sales_sar: number(value.sales_sar),
        sales_native: number(value.sales_native),
        orders: number(value.orders, { integer: true }),
        impressions: number(value.impressions, { integer: true }),
        swipes: number(value.swipes, { integer: true }),
        video_views: number(value.video_views, { integer: true }),
        roas: number(value.roas),
        cpa_sar: number(value.cpa_sar),
        cpc_sar: number(value.cpc_sar),
        cpm_sar: number(value.cpm_sar),
        ctr_pct: number(value.ctr_pct),
        observed_days: number(value.observed_days, { integer: true }) || 0,
        source_rows: number(value.source_rows, { integer: true }) || 0,
        data_complete: value.data_complete === true,
    };
}

export function normalizeSnapchatAd(value = {}) {
    const id = text(value.ad_id);
    if (!id) return null;
    return {
        account_id: text(value.account_id),
        account_name: text(value.account_name, value.account_id || "حساب غير معروف"),
        ad_id: id,
        ad_name: text(value.ad_name, id),
        ad_squad_id: text(value.ad_squad_id) || null,
        ad_squad_name: text(value.ad_squad_name, value.ad_squad_id || "مجموعة غير معروفة"),
        campaign_id: text(value.campaign_id) || null,
        campaign_name: text(value.campaign_name, value.campaign_id || "حملة غير معروفة"),
        campaign_status: text(value.campaign_status, "unknown"),
        campaign_active: value.campaign_active === true,
        ad_squad_status: text(value.ad_squad_status, "unknown"),
        status: text(value.status, value.configured_status || "unknown"),
        configured_status: text(value.configured_status, value.status || "unknown"),
        review_status: text(value.review_status) || null,
        delivery_state: text(value.delivery_state) || null,
        delivery_status: text(value.delivery_status) || null,
        delivery_reason_code: text(value.delivery_reason_code) || null,
        deliverable: value.deliverable === true,
        delivery_inherited_from_ad_squad: value.delivery_inherited_from_ad_squad === true,
        creative_id: text(value.creative_id) || null,
        creative_name: text(value.creative_name) || null,
        creative_type: text(value.creative_type) || null,
        media_id: text(value.media_id) || null,
        destination_url: text(value.destination_url) || null,
        created_at_provider: text(value.created_at_provider) || null,
        updated_at_provider: text(value.updated_at_provider) || null,
        display_currency: text(value.display_currency, "SAR"),
        result_source: "platform",
        commercial_results_scope: text(
            value.commercial_results_scope,
            "snapchat_ad_conversion_reporting",
        ),
        ...metrics(value),
    };
}

export function normalizeSnapchatAdReport(payload = {}) {
    const value = payload?.data && typeof payload.data === "object"
        ? payload.data
        : payload;
    return {
        provider: "snapchat_ads",
        entity_level: "ad",
        date_from: text(value.date_from) || null,
        date_to: text(value.date_to) || null,
        account_timezone: text(value.account_timezone) || null,
        selected_account_id: text(value.selected_account_id) || null,
        result_source: "platform",
        action_report_time: text(value.action_report_time, "conversion"),
        totals: metrics(value.totals),
        ads: Array.isArray(value.ads)
            ? value.ads.map(normalizeSnapchatAd).filter(Boolean)
            : [],
        pagination: {
            page: number(value.pagination?.page, { integer: true }) || 1,
            limit: number(value.pagination?.limit, { integer: true }) || SNAPCHAT_ENTITY_PAGE_SIZE,
            total: number(value.pagination?.total, { integer: true }) || 0,
            pages: number(value.pagination?.pages, { integer: true }) || 0,
        },
        source: value.source && typeof value.source === "object" ? value.source : {},
        policy: value.policy && typeof value.policy === "object"
            ? value.policy
            : { mode: "observe_only", mutations_allowed: false },
    };
}

export async function getSnapchatAdPerformance({
    accountId,
    dateFrom,
    dateTo,
    query = "",
    campaignId,
    adSquadId,
    page = 1,
    limit = SNAPCHAT_ENTITY_PAGE_SIZE,
    activeCampaignsOnly = true,
    sortBy = "orders",
    actionReportTime = "conversion",
} = {}) {
    const response = await api.get("/integrations-v2/snapchat_ads/ad-report", {
        params: {
            account_id: accountId || undefined,
            from_date: dateFrom || undefined,
            to_date: dateTo || undefined,
            query: String(query || "").trim().slice(0, 120) || undefined,
            campaign_id: String(campaignId || "").trim().slice(0, 120) || undefined,
            ad_squad_id: String(adSquadId || "").trim().slice(0, 120) || undefined,
            page,
            limit,
            active_campaigns_only: activeCampaignsOnly,
            sort_by: sortBy,
            action_report_time: ["conversion", "impression"].includes(actionReportTime)
                ? actionReportTime
                : "conversion",
        },
    });
    return normalizeSnapchatAdReport(response.data);
}
