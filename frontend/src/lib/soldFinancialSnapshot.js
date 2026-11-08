import api from "./api";
import { getDashboardAdsSpend } from "../services/dashboardAdsSpend";
import { mergeDashboardWithPlatformSpend, needsCanonicalDashboardSpendRefresh } from "./dashboardPlatformSpendMerge";

// Use the executive dashboard's four-platform spend contract, including Google.
// Order filters apply to commerce only; advertising remains period-wide.
export async function loadSoldFinancialSnapshot(range, { apiClient = api, spendLoader = getDashboardAdsSpend, spendTimeoutMs = 12000 } = {}) {
    const request = () => apiClient.get("/dashboard-v2", {
        params: { from_date: range.from, to_date: range.to, report_statuses: range.statuses, _refresh: Date.now() },
        headers: { "Cache-Control": "no-cache", Pragma: "no-cache" },
    });
    let timer;
    const spendRead = Promise.race([
        spendLoader({ dateFrom: range.from, dateTo: range.to, refresh: false }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("financial_spend_timeout")), spendTimeoutMs); }),
    ]).finally(() => clearTimeout(timer));
    let [response, spend] = await Promise.all([request(), spendRead]);
    if (!spend || spend.date_from !== range.from || spend.date_to !== range.to) {
        throw new Error("financial_spend_period_mismatch");
    }
    if (needsCanonicalDashboardSpendRefresh(response.data, spend)) response = await request();
    return mergeDashboardWithPlatformSpend(response.data, spend);
}
