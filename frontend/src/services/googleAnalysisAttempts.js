import api from "../lib/api";

// The diagnostics endpoint is read-only. Project only fields the UI needs;
// provider/model bodies and exception text must never become display content.
export function selectGoogleAnalysisAttempts(payload, { accountId, dateFrom, dateTo, campaignId }) {
    const rows = Array.isArray(payload?.items) ? payload.items.slice(0, 20) : [];
    return rows.filter((row) => row && String(row.account_id) === String(accountId)
        && row.date_from === dateFrom && row.date_to === dateTo
        && (campaignId ? String(row.campaign_id || "") === String(campaignId) : !row.campaign_id))
        .map((row) => ({
            analysisId: typeof row.analysis_id === "string" ? row.analysis_id.slice(0, 100) : "",
            createdAt: typeof row.created_at === "string" ? row.created_at.slice(0, 40) : "",
            status: ["running", "complete", "failed", "cancelled"].includes(row.status) ? row.status : "unknown",
            phase: ["native_workspace", "policy_read", "structured_analysis", "save_analysis"].includes(row.phase) ? row.phase : "unknown",
            durationMs: Number.isFinite(row.duration_ms) && row.duration_ms >= 0 ? row.duration_ms : null,
            errorCode: typeof row.error_code === "string" && /^google_[a-z0-9_]{1,90}$/.test(row.error_code) ? row.error_code : null,
            providerWrites: row.provider_writes === 0 ? 0 : null,
        }));
}

export async function getGoogleAnalysisAttempts({ accountId, dateFrom, dateTo, campaignId, signal }) {
    const response = await api.get("/integrations-v2/google_ads/ai/analysis-attempts", {
        params: { account_id: accountId, date_from: dateFrom, date_to: dateTo }, signal,
    });
    return selectGoogleAnalysisAttempts(response.data, { accountId, dateFrom, dateTo, campaignId });
}
