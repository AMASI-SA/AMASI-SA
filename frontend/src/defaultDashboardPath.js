const DASHBOARD_DESIGN_PREVIEW_HOSTS = new Set([
    "salla-analytics.preview.emergentagent.com",
    "salla-analytics.preview.emergent.host",
]);

export function getDefaultDashboardPath(hostname = window.location.hostname) {
    const normalizedHostname = String(hostname || "").trim().toLowerCase();
    return DASHBOARD_DESIGN_PREVIEW_HOSTS.has(normalizedHostname)
        ? "/dashboard-design-preview"
        : "/dashboard-v2";
}
