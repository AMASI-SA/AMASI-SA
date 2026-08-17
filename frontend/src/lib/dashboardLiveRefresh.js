export const DASHBOARD_AUTO_REFRESH_MS = 30_000;

export function dashboardOrdersSignature(orders = []) {
    return (orders || []).slice(0, 20).map((order) => [
        order?.order_number,
        order?.updated_at || order?.created_at || order?.order_date,
        order?.status_native || order?.status?.name || order?.status,
        order?.totals?.total ?? order?.total_amount,
    ].map((value) => String(value ?? "").trim()).join(":"))
        .join("|");
}

export function shouldRefreshDashboardForOrders(previousSignature, nextSignature, hasDashboardData) {
    return Boolean(
        hasDashboardData
        && nextSignature
        && previousSignature !== nextSignature
    );
}
