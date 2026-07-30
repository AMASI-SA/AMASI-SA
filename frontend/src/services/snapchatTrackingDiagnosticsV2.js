import api from "../lib/api";
import { redactIntegrationValue } from "./integrationsV2";

function safeNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

export function normalizeSnapchatTrackingDiagnostics(payload = {}) {
    const safe = redactIntegrationValue(payload || {});
    const reportedStatus = ["complete", "partial", "failed"].includes(safe.status)
        ? safe.status
        : "failed";
    const sourceOnly = safe.source_only === true;
    const providerWriteReached = safe.provider_write_reached === true;
    const eventWriteReached = safe.event_write_reached === true;
    const accountingWriteReached = safe.accounting_write_reached === true;
    const qoyodWriteReached = safe.qoyod_write_reached === true;
    const status = (
        sourceOnly
        && !providerWriteReached
        && !eventWriteReached
        && !accountingWriteReached
        && !qoyodWriteReached
    ) ? reportedStatus : "failed";
    return {
        run_id: typeof safe.run_id === "string" ? safe.run_id : null,
        provider: "snapchat_ads",
        status,
        date_from: typeof safe.date_from === "string" ? safe.date_from : null,
        date_to: typeof safe.date_to === "string" ? safe.date_to : null,
        accounts_attempted: safeNumber(safe.accounts_attempted),
        accounts_complete: safeNumber(safe.accounts_complete),
        pixels_found: safeNumber(safe.pixels_found),
        pixels_complete: safeNumber(safe.pixels_complete),
        domains_observed: safeNumber(safe.domains_observed),
        diagnostics_saved: safeNumber(safe.diagnostics_saved),
        recommendations_count: safeNumber(safe.recommendations_count),
        errors_count: safeNumber(safe.errors_count),
        source_only: sourceOnly,
        provider_write_reached: providerWriteReached,
        event_write_reached: eventWriteReached,
        accounting_write_reached: accountingWriteReached,
        qoyod_write_reached: qoyodWriteReached,
    };
}

export async function diagnoseSnapchatTracking({ days = 7 } = {}) {
    const parsedDays = Number(days);
    if (!Number.isInteger(parsedDays) || parsedDays < 1 || parsedDays > 7) {
        throw new Error("invalid_tracking_diagnostic_days");
    }
    const response = await api.post(
        "/integrations-v2/snapchat_ads/tracking-diagnostics",
        { days: parsedDays },
    );
    return normalizeSnapchatTrackingDiagnostics(response.data);
}
