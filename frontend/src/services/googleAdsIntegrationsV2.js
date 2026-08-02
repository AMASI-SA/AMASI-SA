import api from "../lib/api";

export async function getGoogleAdsReportingReadiness() {
    const response = await api.get(
        "/integrations-v2/google_ads/reporting-readiness",
    );
    return response.data;
}

export async function getGoogleAdsAccountSelection() {
    const response = await api.get(
        "/integrations-v2/google_ads/accounts-selection",
    );
    return response.data;
}

export async function saveGoogleAdsAccountSelection(accountIds) {
    const response = await api.put(
        "/integrations-v2/google_ads/accounts-selection",
        { account_ids: accountIds },
    );
    return response.data;
}

export async function syncGoogleAdsReporting(days = 7) {
    const response = await api.post(
        "/integrations-v2/google_ads/reporting-sync",
        { days },
    );
    return response.data;
}
