import api from "../lib/api";
import { retryAdsRead } from "./adsReadRetry";

export async function getSnapchatOrderSourceAudit({
    accountId,
    dateFrom,
    dateTo,
} = {}) {
    const response = await retryAdsRead(() => api.get(
        "/integrations-v2/snapchat_ads/order-source-audit",
        {
            params: {
                account_id: accountId || undefined,
                from_date: dateFrom || undefined,
                to_date: dateTo || undefined,
            },
        },
    ));
    return response.data;
}

export const SNAPCHAT_ORDER_SOURCE_AUDIT_CONTRACT = Object.freeze({
    read_only: true,
    campaign_distribution_allowed: false,
    exact_campaign_match_only: true,
});
