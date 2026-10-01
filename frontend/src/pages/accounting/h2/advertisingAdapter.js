import api from "../../../lib/api";

// Track E #1218: these are native read routes, not the legacy advertising APIs.
export const ADVERTISING_BASE = "/accounting-module/advertising-v2";
export const ADVERTISING_PLATFORMS = { snapchat: "Snap", meta: "Meta", tiktok: "TikTok", google_ads: "Google Ads" };
export const ADVERTISING_GAPS = {
    postedHistory: "ad_posted_daily_status_read_contract_missing",
    fx: "ad_fx_readiness_read_contract_missing",
    balances: "ad_sar_wallet_and_payable_balance_read_contract_missing",
    bank: "track_a_require_financial_ledger_identity_not_integrated",
};
const invalid = () => {
    const error = new Error("ad_native_read_contract_invalid");
    error.code = "ad_native_read_contract_invalid";
    throw error;
};
const identity = item => item && typeof item === "object" &&
    Object.prototype.hasOwnProperty.call(ADVERTISING_PLATFORMS, item.platform) &&
    typeof item.integration_account_id === "string" && item.integration_account_id.trim();

export async function readAdvertisingContext() {
    const { data } = await api.get(`${ADVERTISING_BASE}/stage-12`);
    if (data?.stage !== 12 || data?.identity_source !== "mezan_integration_accounts_v2" ||
        !Array.isArray(data.items) || !data.items.every(identity)) invalid();
    return data;
}
export async function readAdvertisingDueItems() {
    const { data } = await api.get(`${ADVERTISING_BASE}/due-items`, { params: { limit: 100 } });
    if (!Array.isArray(data?.items) || !data.items.every(item => identity(item) && typeof item.business_date === "string")) invalid();
    return data.items;
}
export async function readAdvertisingSource(account, businessDate) {
    if (!identity(account) || !/^\d{4}-\d{2}-\d{2}$/.test(businessDate)) invalid();
    const { data } = await api.get(`${ADVERTISING_BASE}/daily-source`, { params: {
        platform: account.platform, integration_account_id: account.integration_account_id, business_date: businessDate,
    } });
    if (data?.platform !== account.platform || data?.integration_account_id !== account.integration_account_id ||
        data?.business_date !== businessDate || data?.original_currency !== account.currency ||
        data?.native_approval_required !== true || !data?.source_revision) invalid();
    return data;
}
export function advertisingReadFailure(error) {
    const status = error?.response?.status;
    const detail = error?.response?.data?.detail;
    const code = typeof detail?.code === "string" ? detail.code : error?.code === "ad_native_read_contract_invalid" ? error.code : null;
    return {
        blocked: [404, 409, 422, 423, 501, 503].includes(status) || code === "ad_native_read_contract_invalid",
        reason: code || (status === 404 ? "ad_native_route_not_integrated" : "ad_native_read_unavailable"),
    };
}
