import api from "../lib/api";

function number(value, { integer = false } = {}) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return null;
    return integer ? Math.trunc(parsed) : parsed;
}

function text(value, fallback = "") {
    return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function normalizeTotals(value = {}) {
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
        cpa_native: number(value.cpa_native),
        cpc_sar: number(value.cpc_sar),
        cpc_native: number(value.cpc_native),
        cpm_sar: number(value.cpm_sar),
        cpm_native: number(value.cpm_native),
        ctr_pct: number(value.ctr_pct),
        observed_days: number(value.observed_days, { integer: true }) || 0,
        source_rows: number(value.source_rows, { integer: true }) || 0,
        data_complete: value.data_complete === true,
    };
}

export function normalizeAdSquad(value = {}) {
    const id = text(value.ad_squad_id);
    if (!id) return null;
    return {
        account_id: text(value.account_id),
        account_name: text(value.account_name, value.account_id || "حساب غير معروف"),
        ad_squad_id: id,
        ad_squad_name: text(value.ad_squad_name, id),
        campaign_id: text(value.campaign_id) || null,
        campaign_name: text(value.campaign_name, value.campaign_id || "حملة غير معروفة"),
        status: text(value.status, "unknown"),
        delivery_status: text(value.delivery_status) || null,
        optimization_goal: text(value.optimization_goal) || null,
        billing_event: text(value.billing_event) || null,
        bid_strategy: text(value.bid_strategy) || null,
        start_time: text(value.start_time) || null,
        end_time: text(value.end_time) || null,
        budget: {
            currency: text(value.budget?.currency) || null,
            daily_native: number(value.budget?.daily_native),
            lifetime_native: number(value.budget?.lifetime_native),
        },
        display_currency: text(value.display_currency, value.budget?.currency || "SAR"),
        result_source: "platform",
        commercial_results_scope: text(
            value.commercial_results_scope,
            "snapchat_ad_squad_conversion_reporting",
        ),
        ...normalizeTotals(value),
    };
}

export function normalizeSnapchatAdSquadReport(payload = {}) {
    const value = payload?.data && typeof payload.data === "object"
        ? payload.data
        : payload;
    return {
        provider: "snapchat_ads",
        entity_level: "ad_squad",
        date_from: text(value.date_from) || null,
        date_to: text(value.date_to) || null,
        account_timezone: text(value.account_timezone) || null,
        selected_account_id: text(value.selected_account_id) || null,
        result_source: "platform",
        totals: normalizeTotals(value.totals),
        daily: Array.isArray(value.daily)
            ? value.daily.map((row) => ({ date: text(row.date), ...normalizeTotals(row) }))
            : [],
        ad_squads: Array.isArray(value.ad_squads)
            ? value.ad_squads.map(normalizeAdSquad).filter(Boolean)
            : [],
        pagination: {
            page: number(value.pagination?.page, { integer: true }) || 1,
            limit: number(value.pagination?.limit, { integer: true }) || 25,
            total: number(value.pagination?.total, { integer: true }) || 0,
            pages: number(value.pagination?.pages, { integer: true }) || 0,
        },
        source: value.source && typeof value.source === "object" ? value.source : {},
        policy: value.policy && typeof value.policy === "object"
            ? value.policy
            : { mode: "observe_only", mutations_allowed: false },
    };
}

export async function getSnapchatAdSquadPerformance({
    accountId,
    dateFrom,
    dateTo,
    query = "",
    page = 1,
    limit = 25,
} = {}) {
    const response = await api.get("/integrations-v2/snapchat_ads/ad-squad-report", {
        params: {
            account_id: accountId || undefined,
            from_date: dateFrom || undefined,
            to_date: dateTo || undefined,
            query: String(query || "").trim().slice(0, 120) || undefined,
            page,
            limit,
        },
    });
    return normalizeSnapchatAdSquadReport(response.data);
}
